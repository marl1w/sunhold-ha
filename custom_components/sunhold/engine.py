"""The running part: watches the house and decides what each zone should be doing.

Sunhold never commands a thermostat. It publishes each zone's recommendation as entities,
and whatever drives the actual device - an automation for a cloud thermostat, a blueprint,
another integration - follows them. It reads the thermostats only to learn the season and
to notice when a person overrides a recommendation.
"""

from __future__ import annotations

import asyncio
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass, field
import datetime as dt
import logging
from statistics import fmean
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from . import history
from .const import (
    COMFORT_BAND_HIGH,
    COMFORT_BAND_LOW,
    CONF_CLIMATE,
    CONF_HOLD_UNTIL,
    CONF_HOUSE_POWER,
    CONF_NOTIFY,
    CONF_OPENINGS,
    CONF_OUTDOOR_TEMP,
    CONF_PUMP_POWER,
    CONF_PV_POWER,
    CONF_SUMMER_PAUSE,
    CONF_SUMMER_TARGET,
    CONF_SUN,
    CONF_SUN_FLOOR,
    CONF_WEATHER,
    CONF_WINDOW_HEAT,
    CONF_WINTER_TARGET,
    CONF_ZONE_TEMP,
    DEFAULT_HOLD_UNTIL,
    DOMAIN,
    FEEDBACK_LABELS,
    HOLD_MARGIN,
    MANUAL_GRACE_SECONDS,
    PUMP_RUNNING_W,
    SUN_CHECK_MIN_RATIO,
    WINDOW_CLOSED_SECONDS,
    WINDOW_OPEN_SECONDS,
    Feedback,
    Intent,
    Season,
)
from .decide import Setpoints, ZoneSituation, intent_for, setpoint_for
from .journal import Journal
from .models import (
    DIRECT_STEP,
    FALLBACK_MAX_CLOUD,
    INDIRECT_STEP,
    MIN_DAYS,
    MIN_SUN_DAYS,
    CloudMap,
    Comfort,
    DayObservation,
    RiseModel,
    SolarGate,
    SolarSurplus,
    clear_day,
    cooling_thresholds,
    fit_cloud_map,
    fit_rise,
    plan_winter,
    solar_elevation,
)

_LOGGER = logging.getLogger(__name__)

SIGNAL_UPDATE = f"{DOMAIN}_update"
FEEDBACK_ACTION = "SUNHOLD"
BACKFILL_DAYS = 400
CLEAR_WINDOW_DAYS = 21
# Summer comfort guard: a zone this far past its summer target ends a solar pause.
SUMMER_GUARD = 1.0
# The solar gate only runs while the sun is meaningfully up.
SUN_UP_ELEVATION = 10.0
# Samples of the pump's running draw needed before the summer thresholds recalibrate:
# about 33 hours of running, minute by minute.
DRAWS_TO_CALIBRATE = 2000
# A free-running day in a hot spell says little about winter; days this warm stay out of
# the winter model unless they were recorded in the heating season.
WINTER_MAX_OUTDOOR = 20.0
FEEDBACK_DAYS = 30
ATTR_OPERATING_MODE = "operating_mode"


@dataclass
class Zone:
    """One thermostat zone and what Sunhold knows about it right now."""

    zone_id: str
    title: str
    cfg: dict[str, Any]
    intent: Intent = Intent.SCHEDULE
    setpoint: float | None = None
    since: dt.datetime | None = None
    # Whether the thermostat has been seen at the recommended set point during the
    # current recommendation: only then can a departure from it be a person.
    followed: bool = False
    suppressed: set[Intent] = field(default_factory=set)
    window_open: bool = False
    sun_hold: bool = False
    mismatch_since: dt.datetime | None = None
    open_since: dt.datetime | None = None
    closed_since: dt.datetime | None = None
    # Kept through a thermostat's brief unavailability, so a cloud blip does not end
    # every recommendation.
    last_season: Season = Season.OFF
    # What the thermostat's own schedule asks for, seen while it was following it.
    schedule_setpoint: float | None = None

    @property
    def climate(self) -> str | None:
        """Return the thermostat entity Sunhold reads, if any."""
        return self.cfg.get(CONF_CLIMATE)


class Engine:
    """Everything Sunhold does for one house."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, journal: Journal) -> None:
        """Build the zones from the entry's subentries."""
        self.hass = hass
        self.entry = entry
        self.journal = journal
        self.zones: dict[str, Zone] = {
            sub.subentry_id: Zone(sub.subentry_id, sub.title, dict(sub.data))
            for sub in entry.subentries.values()
        }
        self.features = {
            "sun_hold": True,
            "solar_cooling": True,
            "solar_boost": True,
            "window_pause": True,
        }
        self.gate = SolarGate()
        self.surplus = SolarSurplus()
        self._unsubs: list[Callable[[], None]] = []
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------- lifecycle

    @property
    def cfg(self) -> dict[str, Any]:
        """Return the house-wide configuration."""
        return dict(self.entry.data)

    def models(self) -> dict[str, RiseModel]:
        """Return each zone's fitted rise model."""
        return {z: RiseModel.from_dict(self.journal.data["models"].get(z)) for z in self.zones}

    def comfort(self, zone_id: str) -> Comfort:
        """Return a zone's learned comfort offsets."""
        return Comfort.from_dict(self.journal.data["comfort"].get(zone_id))

    async def async_start(self) -> None:
        """Wire up listeners and timers, and pick the day up where it was left."""
        summer = self.journal.data["summer"]
        if "cut" in summer:
            self._set_thresholds(summer["cut"], summer["resume"])
        paused_at = dt_util.parse_datetime(summer.get("paused_at") or "")
        if paused_at and paused_at.date() == dt_util.now().date() and self._sun_up(dt_util.now()):
            self.gate.paused, self.gate.paused_at = True, paused_at

        watched = [z.climate for z in self.zones.values() if z.climate]
        for zone in self.zones.values():
            watched += zone.cfg.get(CONF_OPENINGS, [])
        if watched:
            self._unsubs.append(
                async_track_state_change_event(self.hass, watched, self._on_state_change)
            )
        self._unsubs += [
            async_track_time_interval(self.hass, self._on_minute, dt.timedelta(minutes=1)),
            async_track_time_change(self.hass, self._morning, hour=[5, 6, 7], minute=30, second=0),
            async_track_time_change(self.hass, self._sun_check, hour=10, minute=30, second=0),
            # Yesterday, once its last hour is in the statistics, so the day is whole.
            async_track_time_change(self.hass, self._outcome, hour=0, minute=20, second=0),
            async_track_time_change(self.hass, self._midnight, hour=0, minute=0, second=5),
        ]
        hour, minute = self._hold_until()
        self._unsubs.append(
            async_track_time_change(self.hass, self._hold_end, hour=hour, minute=minute, second=0)
        )
        self._unsubs.append(
            self.hass.bus.async_listen("mobile_app_notification_action", self._on_notification)
        )

        self._restore_today()
        for zone in self.zones.values():
            zone.window_open = self._any_open(zone)
        self._spawn(self._backfill_if_new())
        if 5 <= dt_util.now().hour < 8:
            self._spawn(self._morning(dt_util.now()))
        self._evaluate()

    async def async_stop(self) -> None:
        """Stop everything and write the journal."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for task in self._tasks:
            task.cancel()
        await self.journal.async_flush()

    def _spawn(self, coro) -> None:
        task = self.hass.async_create_background_task(coro, f"{DOMAIN} job")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _hold_until(self) -> tuple[int, int]:
        text = self.cfg.get(CONF_HOLD_UNTIL) or DEFAULT_HOLD_UNTIL
        hour, minute, *_ = (int(x) for x in text.split(":"))
        return hour, minute

    def _restore_today(self) -> None:
        now = dt_util.now()
        hour, minute = self._hold_until()
        before_end = (now.hour, now.minute) < (hour, minute)
        for zone_id, rec in self.journal.day(now.date())["zones"].items():
            zone = self.zones.get(zone_id)
            plan = rec.get("plan")
            if zone is None:
                continue
            zone.suppressed = {Intent(i) for i in rec.get("suppressed", [])}
            if plan and before_end and not rec.get("cancelled"):
                zone.sun_hold = bool(plan.get("hold"))

    # ------------------------------------------------------------- reading the house

    def _float(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    def _unit(self, entity_id: str | None) -> str | None:
        state = self.hass.states.get(entity_id) if entity_id else None
        unit = state.attributes.get("unit_of_measurement") if state else None
        # Sun is stored as hourly means summed and divided by 1000.
        return {"W/m²": "kWh/m²", "W": "kWh", "kW": "MWh"}.get(unit or "")

    def season(self, zone: Zone) -> Season:
        """Return which way a zone is working, from its own HVAC mode."""
        state = self.hass.states.get(zone.climate) if zone.climate else None
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return zone.last_season
        zone.last_season = {"heat": Season.WINTER, "cool": Season.SUMMER}.get(
            state.state, Season.OFF
        )
        return zone.last_season

    def _any_open(self, zone: Zone) -> bool:
        return any(
            (s := self.hass.states.get(e)) is not None and s.state == STATE_ON
            for e in zone.cfg.get(CONF_OPENINGS, [])
        )

    def target(self, zone: Zone, season: Season) -> float:
        """Return a zone's comfort target with the learned offset applied."""
        base = zone.cfg[CONF_SUMMER_TARGET if season is Season.SUMMER else CONF_WINTER_TARGET]
        return float(base) + self.comfort(zone.zone_id).offset(season)

    def _sun_up(self, now: dt.datetime) -> bool:
        return (
            solar_elevation(self.hass.config.latitude, self.hass.config.longitude, now)
            > SUN_UP_ELEVATION
        )

    # ------------------------------------------------------------- the minute loop

    @callback
    def _on_state_change(self, event: Event) -> None:
        old, new = event.data.get("old_state"), event.data.get("new_state")
        # Thermostats report their reading every few seconds; only the mode and the set
        # point matter here, and the minute loop catches everything else.
        if (
            old is not None
            and new is not None
            and old.state == new.state
            and old.attributes.get(ATTR_TEMPERATURE) == new.attributes.get(ATTR_TEMPERATURE)
        ):
            return
        self._evaluate()

    @callback
    def _on_minute(self, _now: dt.datetime) -> None:
        self._evaluate(sample=True)

    @callback
    def _evaluate(self, *, sample: bool = False) -> None:
        """Bring every zone's recommendation up to date.

        `sample` is set only by the minute loop, so the pump's draw is sampled once a
        minute however often the house's sensors happen to report.
        """
        now = dt_util.now()
        self._update_windows(now)
        self._update_gate(now, sample=sample)
        self._update_surplus(now)
        self._watch_people(now)
        for zone in self.zones.values():
            self._reconcile(zone, now)
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    def _update_windows(self, now: dt.datetime) -> None:
        for zone in self.zones.values():
            if self._any_open(zone):
                zone.closed_since = None
                zone.open_since = zone.open_since or now
                if (now - zone.open_since).total_seconds() >= WINDOW_OPEN_SECONDS:
                    zone.window_open = True
            else:
                zone.open_since = None
                zone.closed_since = zone.closed_since or now
                if (
                    zone.window_open
                    and (now - zone.closed_since).total_seconds() >= WINDOW_CLOSED_SECONDS
                ):
                    zone.window_open = False
                    # A person who overrode a window pause did so for that opening only.
                    zone.suppressed.discard(Intent.WINDOW_PAUSE)

    def headroom(self) -> float | None:
        """Return what the panels could give the heat pump now, in W."""
        pv = self._float(self.cfg.get(CONF_PV_POWER))
        house = self._float(self.cfg.get(CONF_HOUSE_POWER))
        pump = self._float(self.cfg.get(CONF_PUMP_POWER))
        if pv is None or house is None or pump is None:
            return None
        # Excludes the pump's own draw, so the signal does not move when the pump stops.
        return pv - (house - pump)

    def _set_thresholds(self, cut: float, resume: float) -> None:
        self.gate.cut_w, self.gate.resume_w = cut, resume
        self.surplus.off_w, self.surplus.on_w = cut, resume

    def _update_surplus(self, now: dt.datetime) -> None:
        active = any(self.season(z) is not Season.OFF for z in self.zones.values())
        headroom = self.headroom() if active and self.features["solar_boost"] else None
        was = self.surplus.active
        self.surplus.update(now, headroom, self._sun_up(now))
        if self.surplus.active != was:
            self.journal.event(now, "surplus_start" if self.surplus.active else "surplus_end")

    def _update_gate(self, now: dt.datetime, *, sample: bool) -> None:
        summer = [z for z in self.zones.values() if self.season(z) is Season.SUMMER]
        pump = self._float(self.cfg.get(CONF_PUMP_POWER))
        if sample and summer and pump is not None and pump > PUMP_RUNNING_W:
            self.journal.data["summer"].setdefault("draws", []).append(round(pump))
        was = self.gate.paused
        if not summer or not self.features["solar_cooling"]:
            self.gate.update(now, None, None, False)
        else:
            too_warm = any(
                (t := self._float(z.cfg[CONF_ZONE_TEMP])) is not None
                and t > self.target(z, Season.SUMMER) + SUMMER_GUARD
                for z in summer
            )
            self.gate.update(now, self.headroom(), pump, self._sun_up(now), too_warm)
        if self.gate.paused != was:
            # Kept across a restart: resuming on grid power at every restart during a
            # cloudy afternoon would defeat the pause.
            self.journal.data["summer"]["paused_at"] = (
                self.gate.paused_at.isoformat() if self.gate.paused else None
            )
            if not self.gate.paused:
                for zone in self.zones.values():
                    zone.suppressed.discard(Intent.SOLAR_PAUSE)
            self.journal.event(now, "solar_pause" if self.gate.paused else "solar_resume")
            self._notify_gate(self.gate.paused)

    def _watch_people(self, now: dt.datetime) -> None:
        """Notice a person overriding a recommendation, from the thermostat's set point."""
        for zone in self.zones.values():
            if zone.setpoint is None or not zone.climate:
                zone.mismatch_since = None
                continue
            state = self.hass.states.get(zone.climate)
            if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                continue
            current = state.attributes.get(ATTR_TEMPERATURE)
            mode = state.attributes.get(ATTR_OPERATING_MODE)
            if isinstance(current, (int, float)) and abs(current - zone.setpoint) < 0.01:
                # A thermostat that reports its mode only counts as following once it is
                # off its schedule - otherwise a schedule that happens to ask for the
                # same temperature would look like one.
                zone.followed = zone.followed or mode in (None, "manual")
                zone.mismatch_since = None
                continue
            if not zone.followed or mode == "auto":
                # Never applied yet, or the override ran out and the thermostat went back
                # to its schedule: the device's doing, not a person's. Whatever drives it
                # will apply the recommendation again.
                zone.followed = False
                zone.mismatch_since = None
                continue
            zone.mismatch_since = zone.mismatch_since or now
            if (now - zone.mismatch_since).total_seconds() >= MANUAL_GRACE_SECONDS:
                self._person_took_over(zone, state, now)

    def _person_took_over(self, zone: Zone, state: State, now: dt.datetime) -> None:
        intent, setpoint = zone.intent, zone.setpoint
        zone.suppressed.add(intent)
        detail = {
            "intent": intent.value,
            "setpoint": setpoint,
            "to": state.attributes.get(ATTR_TEMPERATURE),
        }
        self.journal.event(now, "override", zone.zone_id, **detail)
        self.journal.zone_day(now.date(), zone.zone_id).setdefault("overrides", []).append(
            {"at": now.isoformat(timespec="seconds"), **detail}
        )
        self.journal.zone_day(now.date(), zone.zone_id)["suppressed"] = sorted(zone.suppressed)
        # Turning up a sun hold says the house was wanted warmer; turning down a solar
        # pause, cooler. Anything else - a window pause, or a change the other way - says
        # nothing about comfort, but the person still wins.
        season = self.season(zone)
        to = state.attributes.get(ATTR_TEMPERATURE)
        if isinstance(to, (int, float)) and setpoint is not None:
            signal = None
            if intent is Intent.SUN_HOLD and to > setpoint:
                signal = Feedback.TOO_COLD
            elif intent is Intent.SOLAR_PAUSE and to < setpoint:
                signal = Feedback.TOO_WARM
            if signal is not None:
                self._learn(zone, season, signal, direct=False, now=now)
                self.journal.zone_day(now.date(), zone.zone_id)["overrides"][-1]["signal"] = (
                    signal.value
                )
        self._reconcile(zone, now)

    def _track_schedule(self, zone: Zone) -> None:
        state = self.hass.states.get(zone.climate) if zone.climate else None
        if state is None or zone.intent is not Intent.SCHEDULE:
            return
        current = state.attributes.get(ATTR_TEMPERATURE)
        if isinstance(current, (int, float)) and state.attributes.get(ATTR_OPERATING_MODE) in (
            None,
            "auto",
        ):
            zone.schedule_setpoint = float(current)

    def _reconcile(self, zone: Zone, now: dt.datetime) -> None:
        season = self.season(zone)
        self._track_schedule(zone)
        setpoints = Setpoints(
            float(zone.cfg[CONF_SUN_FLOOR]),
            float(zone.cfg[CONF_WINDOW_HEAT]),
            float(zone.cfg[CONF_SUMMER_PAUSE]),
            self.target(zone, season) if season is not Season.OFF else 0.0,
        )
        situation = ZoneSituation(
            season=season,
            window_open=zone.window_open and self.features["window_pause"],
            sun_hold=zone.sun_hold and self.features["sun_hold"],
            solar_paused=self.gate.paused,
            solar_surplus=self.surplus.active
            and boost_helps(season, setpoint_for(Intent.SOLAR_BOOST, season, setpoints), zone),
            suppressed=frozenset(zone.suppressed),
        )
        intent = intent_for(situation)
        setpoint = setpoint_for(intent, season, setpoints)
        if intent is zone.intent and setpoint == zone.setpoint:
            return
        self._close_episode(zone, now)
        zone.intent, zone.setpoint, zone.since = intent, setpoint, now
        zone.followed, zone.mismatch_since = False, None
        self.journal.event(now, intent.value, zone.zone_id, setpoint=setpoint)
        if intent is not Intent.SCHEDULE:
            self.journal.zone_day(now.date(), zone.zone_id).setdefault("episodes", []).append(
                {"intent": intent.value, "start": now.isoformat(timespec="seconds")}
            )

    def _close_episode(self, zone: Zone, now: dt.datetime) -> None:
        if zone.since is None:
            return
        episodes = self.journal.zone_day(zone.since.date(), zone.zone_id).get("episodes", [])
        if episodes and "end" not in episodes[-1]:
            episodes[-1]["end"] = now.isoformat(timespec="seconds")
            self.journal.save()

    def until(self, zone: Zone) -> dt.datetime | None:
        """Return when the current recommendation is planned to end, if it is known."""
        if zone.intent is not Intent.SUN_HOLD:
            return None
        hour, minute = self._hold_until()
        return dt_util.now().replace(hour=hour, minute=minute, second=0, microsecond=0)

    # ------------------------------------------------------------- winter

    async def _forecast(self, now: dt.datetime) -> dict | None:
        weather = self.cfg.get(CONF_WEATHER)
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": weather, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
        except Exception as err:
            _LOGGER.warning("Forecast unavailable: %s", err)
            return None
        cloud, outdoor = [], []
        for item in (response or {}).get(weather, {}).get("forecast", []):
            parsed = dt_util.parse_datetime(str(item.get("datetime", "")))
            if parsed is None:
                continue
            when = dt_util.as_local(parsed)
            if when.date() != now.date():
                continue
            if when.hour in history.SUN_HOURS and item.get("cloud_coverage") is not None:
                cloud.append(float(item["cloud_coverage"]))
            if when.hour in history.OUTDOOR_HOURS and item.get("temperature") is not None:
                outdoor.append(float(item["temperature"]))
        if not cloud or not outdoor:
            return None
        return {"cloud": round(fmean(cloud), 1), "outdoor": round(fmean(outdoor), 2)}

    def _sun_days(self) -> list[tuple[dt.date, float | None]]:
        return [(day, rec["house"].get("sun")) for day, rec in self.journal.days()]

    def _clear(self, today: dt.date) -> float | None:
        return clear_before(today, self._sun_days())

    async def _morning(self, _now: dt.datetime) -> None:
        """Plan the day. Retried at 06:00 and 07:00, and after a restart, until it has."""
        now = dt_util.now()
        day = self.journal.day(now.date())
        if day["house"].get("forecast") or (now.hour, now.minute) >= self._hold_until():
            return
        forecast = await self._forecast(now)
        if forecast is None:
            return
        day["house"]["forecast"] = forecast
        clear = self._clear(now.date())
        cmap = CloudMap.from_dict(self.journal.data["cloud_map"])
        models = self.models()
        for zone in self.zones.values():
            if self.season(zone) is not Season.WINTER:
                continue
            indoor = self._float(zone.cfg[CONF_ZONE_TEMP])
            target = self.target(zone, Season.WINTER)
            rec = self.journal.zone_day(now.date(), zone.zone_id)
            if clear is None or indoor is None:
                # Not enough measured days to know what sun means here yet.
                hold = forecast["cloud"] <= FALLBACK_MAX_CLOUD
                rec["plan"] = {"hold": hold, "fallback": True, "cloud": forecast["cloud"]}
            else:
                sun = cmap.expected(clear, forecast["cloud"])
                plan = plan_winter(
                    indoor, models[zone.zone_id], sun, forecast["outdoor"], target, HOLD_MARGIN
                )
                rec["plan"] = {**plan.as_dict(), "indoor": indoor, "cloud": forecast["cloud"]}
            zone.sun_hold = rec["plan"]["hold"]
            self.journal.event(now, "plan", zone.zone_id, **rec["plan"])
        self.journal.save()
        self._evaluate()

    def _sun_share(self, day: dt.date, until: dt.time) -> float:
        """Return the part of a clear day's 08-16 sun that falls before `until`."""
        tz = dt_util.get_default_time_zone()
        lat, lon = self.hass.config.latitude, self.hass.config.longitude
        total = before = 0.0
        t = dt.datetime.combine(day, dt.time(history.SUN_HOURS.start), tz)
        end = dt.datetime.combine(day, dt.time(history.SUN_HOURS.stop), tz)
        cutoff = dt.datetime.combine(day, until, tz)
        while t < end:
            weight = max(0.0, solar_elevation(lat, lon, t + dt.timedelta(minutes=5)))
            total += weight
            if t < cutoff:
                before += weight
            t += dt.timedelta(minutes=10)
        return before / total if total else 0.0

    async def _sun_check(self, _now: dt.datetime) -> None:
        now = dt_util.now()
        held = [z for z in self.zones.values() if z.sun_hold]
        sun_id = self.cfg.get(CONF_SUN)
        if not held or not sun_id:
            return
        since = now.replace(hour=history.SUN_HOURS.start, minute=0, second=0, microsecond=0)
        data = await history.fetch(self.hass, [sun_id], since, now, period="5minute")
        measured = history.sun_so_far(data, sun_id, since, now)
        clear = self._clear(now.date())
        forecast = self.journal.day(now.date())["house"].get("forecast")
        if measured is None or clear is None or not forecast:
            return
        promised = CloudMap.from_dict(self.journal.data["cloud_map"]).expected(
            clear, forecast["cloud"]
        ) * self._sun_share(now.date(), now.time())
        if promised <= 0 or measured / promised >= SUN_CHECK_MIN_RATIO:
            return
        for zone in held:
            zone.sun_hold = False
            self.journal.zone_day(now.date(), zone.zone_id)["cancelled"] = "no_sun"
        self.journal.event(
            now, "sun_missing", measured=round(measured, 3), promised=round(promised, 3)
        )
        self._evaluate()
        self._notify(
            "No sun - heating back on",
            f"The forecast promised sun, but only {measured / promised:.0%} of it came. "
            "Heating is back on schedule.",
        )

    async def _hold_end(self, _now: dt.datetime) -> None:
        now = dt_util.now()
        held = [z for z in self.zones.values() if z.sun_hold]
        for zone in held:
            zone.sun_hold = False
        if not held:
            return
        self._evaluate()
        lines = []
        for zone in held:
            temp = self._float(zone.cfg[CONF_ZONE_TEMP])
            lines.append(
                f"{zone.title} {temp if temp is not None else '?'} °C "
                f"(target {self.target(zone, Season.WINTER):.1f})"
            )
        self._notify(
            "Sun hold over - how was it?",
            "Heating is back on schedule. " + ", ".join(lines) + ".",
            feedback=(now.date(), [z.zone_id for z in held]),
        )

    async def _midnight(self, _now: dt.datetime) -> None:
        for zone in self.zones.values():
            zone.suppressed.clear()
            zone.sun_hold = False
        self.journal.prune(dt_util.now().date())
        self.journal.save()

    # ------------------------------------------------------------- learning

    def _sources(self, zone: Zone) -> history.Sources:
        return history.Sources(
            zone_temp=zone.cfg[CONF_ZONE_TEMP],
            outdoor=self.cfg[CONF_OUTDOOR_TEMP],
            sun=self.cfg.get(CONF_SUN),
            pump=self.cfg.get(CONF_PUMP_POWER),
            pv=self.cfg.get(CONF_PV_POWER),
            house=self.cfg.get(CONF_HOUSE_POWER),
        )

    def _record_days(self, data: history.Hourly, days: list[dt.date], *, live: bool) -> None:
        if not self.zones:
            return
        tz = dt_util.get_default_time_zone()
        house_src = self._sources(next(iter(self.zones.values())))
        for day in days:
            house = history.house_day(data, house_src, day, tz)
            if house is None:
                continue
            for zone in self.zones.values():
                zone_rec = history.zone_day(data, self._sources(zone), day, tz)
                if zone_rec is None:
                    continue
                rec = self.journal.day(day)
                rec["house"].update(house)
                zone_day = rec["zones"].setdefault(zone.zone_id, {})
                hours = zone_rec.pop("hours", [])
                zone_day.update(zone_rec)
                if live:
                    zone_day["season"] = self.season(zone).value
                season = day_season(zone_day, house)
                if hours and season is not Season.OFF:
                    target = self.target(zone, season)
                    inside = [
                        target - COMFORT_BAND_LOW <= t <= target + COMFORT_BAND_HIGH for t in hours
                    ]
                    zone_day["comfort_pct"] = round(100 * sum(inside) / len(inside))

    async def _outcome(self, _now: dt.datetime) -> None:
        yesterday = dt_util.now().date() - dt.timedelta(days=1)
        start = dt_util.start_of_local_day(yesterday)
        data = await history.fetch(
            self.hass, self._all_sources(), start, dt_util.start_of_local_day(dt_util.now())
        )
        self._record_days(data, [yesterday], live=True)
        await self.async_refit()

    def _all_sources(self) -> set[str]:
        ids: set[str] = set()
        for zone in self.zones.values():
            src = self._sources(zone)
            ids |= {
                i for i in (src.zone_temp, src.outdoor, src.sun, src.pump, src.pv, src.house) if i
            }
        return ids

    async def _backfill_if_new(self) -> None:
        """Learn from the house's past on first start, so day one is not blind."""
        if self.journal.data.get("backfilled") or not self.zones:
            return
        today = dt_util.now().date()
        start = dt_util.start_of_local_day(today - dt.timedelta(days=BACKFILL_DAYS))
        data = await history.fetch(
            self.hass, self._all_sources(), start, dt_util.start_of_local_day(today)
        )
        days = [today - dt.timedelta(days=n) for n in range(BACKFILL_DAYS, 0, -1)]
        self._record_days(data, days, live=False)
        self.journal.data["backfilled"] = True
        self.journal.event(dt_util.now(), "backfill", days=len(self.journal.data["days"]))
        await self.async_refit()

    def observations(self, zone_id: str) -> list[DayObservation]:
        """Return the days a zone's winter model learns from."""
        obs = []
        for day, rec in self.journal.days():
            house, zone = rec["house"], rec["zones"].get(zone_id, {})
            if None in (house.get("sun"), house.get("outdoor_mean"), house.get("heat")):
                continue
            if "t07" not in zone or "peak" not in zone:
                continue
            if day_season(zone, house) is not Season.WINTER:
                continue
            obs.append(
                DayObservation(
                    day,
                    house["sun"],
                    house["outdoor_mean"] - zone["t07"],
                    house["heat"],
                    zone["peak"] - zone["t07"],
                )
            )
        return obs

    async def async_refit(self) -> None:
        """Refit every model from the journal, the arithmetic off the event loop.

        Everything the fit reads is copied out on the loop first: the journal keeps
        changing while the executor runs.
        """
        today = dt_util.now().date()
        obs = {z: self.observations(z) for z in self.zones}
        sun_days = self._sun_days()
        samples = []
        for day, rec in self.journal.days():
            fc, sun = rec["house"].get("forecast"), rec["house"].get("sun")
            if fc and sun is not None and (clear := clear_before(day, sun_days)):
                samples.append((clear, fc["cloud"], sun))

        def work() -> dict[str, Any]:
            return {
                "models": {z: fit_rise(o, today).as_dict() for z, o in obs.items()},
                "cloud_map": fit_cloud_map(samples).as_dict(),
            }

        result = await self.hass.async_add_executor_job(work)
        self.journal.data["models"] = result["models"]
        self.journal.data["cloud_map"] = result["cloud_map"]

        summer = self.journal.data["summer"]
        draws = summer.get("draws", [])
        if len(draws) >= DRAWS_TO_CALIBRATE and (pair := cooling_thresholds(draws)):
            summer["cut"], summer["resume"] = pair
            self._set_thresholds(*pair)
        self.journal.event(dt_util.now(), "refit")
        self.journal.prune(today)
        self.journal.save()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    # ------------------------------------------------------------- people

    def _learn(
        self, zone: Zone, season: Season, signal: Feedback, *, direct: bool, now: dt.datetime
    ) -> None:
        if season is Season.OFF:
            return
        comfort = self.comfort(zone.zone_id)
        comfort.learn(season, signal, direct=direct, when=now.isoformat(timespec="seconds"))
        self.journal.data["comfort"][zone.zone_id] = comfort.as_dict()
        self.journal.save()

    def feedback(self, zone_ids: list[str], signal: Feedback, day: dt.date | None = None) -> int:
        """Record how a day felt for some zones, learn from it, and return how many took.

        Only the last month counts: feedback on a day long gone, or one to come, would
        teach the comfort offset about a house that is not the one being run.
        """
        now = dt_util.now()
        day = day or now.date()
        if not now.date() - dt.timedelta(days=FEEDBACK_DAYS) <= day <= now.date():
            return 0
        recorded = 0
        for zone_id in zone_ids:
            zone = self.zones.get(zone_id)
            if zone is None:
                continue
            recorded += 1
            rec = self.journal.zone_day(day, zone_id)
            previous = rec.get("feedback")
            if previous == signal.value:
                continue
            rec["feedback"] = signal.value
            season = Season(rec.get("season") or self.season(zone).value)
            # A changed answer replaces the old one rather than adding to it.
            if previous in (Feedback.TOO_COLD, Feedback.TOO_WARM):
                undo = Feedback.TOO_WARM if previous == Feedback.TOO_COLD else Feedback.TOO_COLD
                self._learn(zone, season, undo, direct=True, now=now)
            self._learn(zone, season, signal, direct=True, now=now)
            self.journal.event(now, "feedback", zone_id, signal=signal.value, day=day.isoformat())
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)
        return recorded

    @callback
    def _on_notification(self, event: Event) -> None:
        parts = str(event.data.get("action", "")).split("|")
        if len(parts) != 4 or parts[0] != FEEDBACK_ACTION:
            return
        _, day, signal, zones = parts
        try:
            self.feedback(zones.split(","), Feedback(signal), dt.date.fromisoformat(day))
        except ValueError:
            return

    # ------------------------------------------------------------- notifications

    def _notify(
        self,
        title: str,
        message: str,
        feedback: tuple[dt.date, list[str]] | None = None,
    ) -> None:
        target = self.cfg.get(CONF_NOTIFY)
        if not target:
            return
        domain, _, service = target.rpartition(".")
        data: dict[str, Any] = {"title": f"Sunhold - {title}", "message": message}
        if feedback:
            day, zones = feedback
            data["data"] = {
                "actions": [
                    {
                        "action": "|".join(
                            (FEEDBACK_ACTION, day.isoformat(), s.value, ",".join(zones))
                        ),
                        "title": label,
                    }
                    for s, label in FEEDBACK_LABELS.items()
                ]
            }
        self._spawn(self.hass.services.async_call(domain or "notify", service, data))

    def _notify_gate(self, paused: bool) -> None:
        headroom = self.headroom()
        if paused:
            self._notify(
                "cooling paused",
                f"The panels can only spare {headroom or 0:.0f} W, so cooling waits until "
                f"there is {self.gate.resume_w:.0f} W of surplus for 20 minutes.",
            )
        else:
            self._notify("cooling back on", "Cooling is back on schedule.")

    # ------------------------------------------------------------- outside views

    @callback
    def async_refresh(self) -> None:
        """Re-evaluate every zone now, after a switch or a setting changed."""
        self._evaluate()

    def overview(self, days: int = 60) -> dict[str, Any]:
        """Return what the panel shows, in one round trip."""
        today = dt_util.now().date()
        models = self.models()
        zones = []
        for zone in self.zones.values():
            season = self.season(zone)
            comfort = self.comfort(zone.zone_id)
            until = self.until(zone)
            zones.append(
                {
                    "id": zone.zone_id,
                    "title": zone.title,
                    "season": season.value,
                    "intent": zone.intent.value,
                    "setpoint": zone.setpoint,
                    "since": zone.since.isoformat() if zone.since else None,
                    "until": until.isoformat() if until else None,
                    "indoor": self._float(zone.cfg.get(CONF_ZONE_TEMP)),
                    "window_open": zone.window_open,
                    "targets": {
                        "winter": float(zone.cfg[CONF_WINTER_TARGET]),
                        "summer": float(zone.cfg[CONF_SUMMER_TARGET]),
                    },
                    "comfort": {
                        "winter": comfort.winter,
                        "summer": comfort.summer,
                        "history": comfort.history[-30:],
                    },
                    "model": models[zone.zone_id].as_dict(),
                    "limits": {
                        "sun_floor": float(zone.cfg[CONF_SUN_FLOOR]),
                        "window_pause_heat": float(zone.cfg[CONF_WINDOW_HEAT]),
                        "summer_pause": float(zone.cfg[CONF_SUMMER_PAUSE]),
                    },
                }
            )
        cutoff = today - dt.timedelta(days=days)
        return {
            "today": today.isoformat(),
            "comfort_band": {"low": COMFORT_BAND_LOW, "high": COMFORT_BAND_HIGH},
            "min_days": {"model": MIN_DAYS, "sun": MIN_SUN_DAYS},
            "learning_steps": {"feedback": DIRECT_STEP, "override": INDIRECT_STEP},
            "window_delays": {"open": WINDOW_OPEN_SECONDS, "closed": WINDOW_CLOSED_SECONDS},
            "sun_unit": self._unit(self.cfg.get(CONF_SUN)),
            "features": dict(self.features),
            "hold_until": "{:02d}:{:02d}".format(*self._hold_until()),
            "surplus": self.surplus.active,
            "gate": {
                "paused": self.gate.paused,
                "cut": self.gate.cut_w,
                "resume": self.gate.resume_w,
                "headroom": self.headroom(),
                "draw_samples": len(self.journal.data["summer"].get("draws", [])),
            },
            "cloud_map": self.journal.data["cloud_map"],
            "clear_day": self._clear(today),
            "zones": zones,
            "days": [
                {"date": day.isoformat(), **rec}
                for day, rec in self.journal.days()
                if day >= cutoff
            ],
            "events": self.journal.data["events"][-200:],
        }


def day_season(zone_day: dict[str, Any], house: dict[str, Any]) -> Season:
    """Return the season a recorded day belongs to.

    Live days carry the thermostat's own; days learned from history guess it from the
    weather.
    """
    if season := zone_day.get("season"):
        return Season(season)
    return Season.WINTER if house.get("outdoor_mean", 99) <= WINTER_MAX_OUTDOOR else Season.SUMMER


def clear_before(day: dt.date, sun_days: list[tuple[dt.date, float | None]]) -> float | None:
    """Return what a clear day measured in the weeks before `day` (sorted input)."""
    lo = bisect_left(sun_days, (day - dt.timedelta(days=CLEAR_WINDOW_DAYS),))
    hi = bisect_left(sun_days, (day,))
    return clear_day([sun for _, sun in sun_days[lo:hi]])


def boost_helps(season: Season, boost: float | None, zone: Zone) -> bool:
    """Return whether a boost would store more than the schedule already does.

    A schedule already asking for more heat (or more cooling) than the boost would make
    the boost a cut, not a boost.
    """
    if boost is None or zone.schedule_setpoint is None:
        return boost is not None
    if season is Season.WINTER:
        return boost > zone.schedule_setpoint
    return boost < zone.schedule_setpoint
