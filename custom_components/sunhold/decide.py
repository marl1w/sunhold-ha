"""Which override a zone should carry right now. Pure, so every rule is a test."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .const import BOOST_DELTA, Intent, Season


@dataclass(frozen=True)
class ZoneSituation:
    """Everything the decision needs about one zone at one moment."""

    season: Season
    window_open: bool
    sun_hold: bool
    solar_paused: bool
    solar_surplus: bool = False
    suppressed: frozenset[Intent] = frozenset()


@dataclass(frozen=True)
class Setpoints:
    """A zone's configured override temperatures."""

    sun_floor: float
    window_heat: float
    summer_pause: float
    target: float


def intent_for(s: ZoneSituation) -> Intent:
    """Return the recommendation that wins for a zone.

    An open window outranks everything: heating or cooling a room with the outside
    let in is waste whatever else is going on. Holding back for the sun or the panels
    comes next, then storing a solar surplus in the house. One a person has
    overridden today is skipped, so the next one down applies - or the schedule.
    """
    if s.season is Season.OFF:
        return Intent.SCHEDULE
    candidates = []
    if s.window_open:
        candidates.append(Intent.WINDOW_PAUSE)
    if s.season is Season.WINTER and s.sun_hold:
        candidates.append(Intent.SUN_HOLD)
    if s.season is Season.SUMMER and s.solar_paused:
        candidates.append(Intent.SOLAR_PAUSE)
    if s.solar_surplus:
        candidates.append(Intent.SOLAR_BOOST)
    for intent in candidates:
        if intent not in s.suppressed:
            return intent
    return Intent.SCHEDULE


def setpoint_for(intent: Intent, season: Season, sp: Setpoints) -> float | None:
    """Return the override temperature for a recommendation, or None for the schedule.

    Every override is a limit the thermostat still enforces by itself: a held zone that
    gets too cold heats, a paused one that gets too hot cools, a boosted one stops at
    the edge of the comfort band.
    """
    if intent is Intent.SCHEDULE:
        return None
    if intent is Intent.SOLAR_BOOST:
        # Store the surplus as heat (or cold) in the house, up to the comfort band's edge.
        delta = BOOST_DELTA if season is Season.WINTER else -BOOST_DELTA
        # Thermostats step in half degrees; a learned target need not. A tie rounds up in
        # both seasons, which keeps the boost inside the comfort band either way (round()
        # rounds ties to even, so 20.75 went to 21.0 but 19.25 to 19.0).
        return math.floor((sp.target + delta) * 2 + 0.5) / 2
    if season is Season.SUMMER:
        return sp.summer_pause
    if intent is Intent.WINDOW_PAUSE:
        return sp.window_heat
    return sp.sun_floor
