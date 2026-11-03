"""Reading the recorder's long-term statistics into days Sunhold can learn from.

Long-term statistics outlive the recorder's purge window by months, which is what lets a
fresh install learn from the house's past instead of starting blind.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import datetime as dt
from statistics import fmean

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant

from .const import COMFORT_HOURS

# The windows every feature is measured over, in local hours. Training and the morning
# decision must agree on them, so they live here and nowhere else.
T_START_HOUR = 7
PEAK_HOURS = range(10, 18)
SUN_HOURS = range(8, 16)
OUTDOOR_HOURS = range(7, 16)
HEAT_HOURS = range(5, 12)

Hourly = dict[str, dict[dt.datetime, dict[str, float]]]


async def fetch(
    hass: HomeAssistant,
    statistic_ids: Iterable[str | None],
    start: dt.datetime,
    end: dt.datetime,
    period: str = "hour",
) -> Hourly:
    """Return {statistic_id: {local start: {"mean", "max"}}} for the given ids."""
    ids = {i for i in statistic_ids if i}
    if not ids:
        return {}
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        end,
        ids,
        period,
        None,
        {"mean", "max"},
    )
    tz = start.tzinfo
    out: Hourly = {}
    for sid, series in rows.items():
        by_start: dict[dt.datetime, dict[str, float]] = {}
        for row in series:
            begin = row["start"]
            when = (
                dt.datetime.fromtimestamp(begin, tz)
                if isinstance(begin, (int, float))
                else begin.astimezone(tz)
            )
            by_start[when] = {k: row[k] for k in ("mean", "max") if row.get(k) is not None}
        out[sid] = by_start
    return out


@dataclass(frozen=True)
class Sources:
    """The statistic ids a day is built from."""

    zone_temp: str
    outdoor: str
    sun: str | None
    pump: str | None
    pv: str | None = None
    house: str | None = None


def _at(series: dict, day: dt.date, hour: int, tz: dt.tzinfo) -> dict[str, float] | None:
    return series.get(dt.datetime(day.year, day.month, day.day, hour, tzinfo=tz))


def _hours(series: dict, day: dt.date, hours: range, tz: dt.tzinfo, key: str) -> list[float]:
    values = []
    for h in hours:
        row = _at(series, day, h, tz)
        if row is None or key not in row:
            return []
        values.append(row[key])
    return values


def house_day(data: Hourly, src: Sources, day: dt.date, tz: dt.tzinfo) -> dict | None:
    """Return the house-wide measurements of one day, or None if they are incomplete."""
    outdoor = _hours(data.get(src.outdoor, {}), day, OUTDOOR_HOURS, tz, "mean")
    if not outdoor:
        return None
    result = {"outdoor_mean": round(fmean(outdoor), 2), "sun": None, "heat": None}
    if src.sun:
        sun = _hours(data.get(src.sun, {}), day, SUN_HOURS, tz, "mean")
        if sun:
            # Hourly mean W (or W/m2) summed over hours is Wh; kWh keeps numbers near 1.
            result["sun"] = round(sum(max(0.0, s) for s in sun) / 1000, 3)
    if src.pump:
        heat = _hours(data.get(src.pump, {}), day, HEAT_HOURS, tz, "mean")
        if heat:
            result["heat"] = round(sum(max(0.0, h) for h in heat) / 1000, 3)
        result.update(_pump_energy(data, src, day, tz))
    return result


def _pump_energy(data: Hourly, src: Sources, day: dt.date, tz: dt.tzinfo) -> dict:
    """Return the day's heat pump energy, and how much of it the panels covered, in kWh.

    Per hour, the panels cover the pump up to what is left after the rest of the house:
    PV - (house - pump). Hourly means blur a pump cycling inside the hour, so this is an
    estimate, and an honest one only when all three meters were read that hour.
    """
    pump = _hours(data.get(src.pump, {}), day, range(24), tz, "mean")
    if not pump:
        return {}
    out = {"pump_kwh": round(sum(max(0.0, p) for p in pump) / 1000, 2)}
    if src.pv and src.house:
        pv = _hours(data.get(src.pv, {}), day, range(24), tz, "mean")
        house = _hours(data.get(src.house, {}), day, range(24), tz, "mean")
        if pv and house:
            solar = sum(
                min(max(0.0, p), max(0.0, v - (h - p)))
                for p, v, h in zip(pump, pv, house, strict=True)
            )
            out["pump_solar_kwh"] = round(solar / 1000, 2)
    return out


def zone_day(data: Hourly, src: Sources, day: dt.date, tz: dt.tzinfo) -> dict | None:
    """Return one zone's morning temperature and afternoon peak, or None if incomplete.

    `hours` carries the waking hours' means for the comfort score; it is not stored.
    """
    series = data.get(src.zone_temp, {})
    start = _at(series, day, T_START_HOUR, tz)
    peaks = _hours(series, day, PEAK_HOURS, tz, "max") or _hours(
        series, day, PEAK_HOURS, tz, "mean"
    )
    if start is None or "mean" not in start or not peaks:
        return None
    hours = [
        row["mean"]
        for h in COMFORT_HOURS
        if (row := _at(series, day, h, tz)) is not None and "mean" in row
    ]
    return {"t07": round(start["mean"], 2), "peak": round(max(peaks), 2), "hours": hours}


def sun_so_far(data: Hourly, sun_id: str, since: dt.datetime, until: dt.datetime) -> float | None:
    """Return the sun measured between two instants from 5-minute statistics, in kWh."""
    series = data.get(sun_id)
    if not series:
        return None
    rows = [r["mean"] for t, r in series.items() if since <= t < until and "mean" in r]
    expected = int((until - since).total_seconds() // 300)
    # A sun sensor that dropped out most of the morning cannot judge it.
    if len(rows) < expected * 0.7:
        return None
    return sum(max(0.0, r) for r in rows) * 5 / 60 / 1000
