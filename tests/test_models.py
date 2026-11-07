"""The learned parts, driven with plain numbers."""

from __future__ import annotations

import datetime as dt
import random

import pytest

from custom_components.sunhold.const import Feedback, Intent, Season
from custom_components.sunhold.decide import Setpoints, ZoneSituation, intent_for, setpoint_for
from custom_components.sunhold.models import (
    PRIOR,
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

TODAY = dt.date(2026, 11, 3)


def _days(n, a=0.8, b=-0.04, d=0.15, c=-0.5, noise=0.0, seed=1):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        sun, dout, heat = rng.uniform(0.2, 1.8), rng.uniform(-9, -2), rng.uniform(0, 6)
        rise = a * sun + b * dout + d * heat + c + rng.gauss(0, noise)
        out.append(DayObservation(TODAY - dt.timedelta(days=n - i), sun, dout, heat, rise))
    return out


def test_fit_recovers_known_coefficients():
    model = fit_rise(_days(60), TODAY)
    assert model.a == pytest.approx(0.8, abs=0.05)
    assert model.d == pytest.approx(0.15, abs=0.02)
    assert model.c == pytest.approx(-0.5, abs=0.15)
    assert model.days == 60
    assert model.mae is not None and model.mae < 0.05


def test_no_days_is_the_prior():
    assert fit_rise([], TODAY) == RiseModel(*PRIOR)


def test_few_days_lean_on_the_prior():
    model = fit_rise(_days(2, a=2.0), TODAY)
    assert PRIOR[0] < model.a < 2.0
    assert model.mae is None


def test_recent_days_win_over_old_ones():
    old = [
        DayObservation(o.day - dt.timedelta(days=120), o.sun, o.delta_out, o.heat, o.rise)
        for o in _days(40, a=0.4)
    ]
    model = fit_rise(old + _days(20, a=1.0, seed=2), TODAY)
    assert model.a > 0.8


def test_winter_plan_holds_only_past_the_margin():
    model = RiseModel(0.8, 0.0, 0.5, 0.0)
    assert plan_winter(19.7, model, 1.2, 10, target=20.3, margin=0.3).hold
    assert not plan_winter(19.7, model, 0.3, 10, target=20.3, margin=0.3).hold


def test_a_sunny_cool_morning_is_a_hold():
    model = RiseModel(0.77, -0.038, 0.1, -0.45)
    plan = plan_winter(19.7, model, sun=1.55, outdoor_mean=12.0, target=20.0, margin=0.4)
    assert plan.hold


def test_solar_noon_elevation():
    # Equinox noon on the equator at Greenwich: the sun is straight up.
    noon = dt.datetime(2026, 3, 20, 12, 7, tzinfo=dt.UTC)
    assert solar_elevation(0.0, 0.0, noon) == pytest.approx(90, abs=1.0)
    # 50 N in late November: low winter sun.
    assert solar_elevation(50.0, 0.0, dt.datetime(2026, 11, 21, 12, 0, tzinfo=dt.UTC)) == (
        pytest.approx(20.0, abs=1.0)
    )


def test_clear_day_is_a_high_percentile_of_recent_days():
    assert clear_day([1.0] * 5) is None
    assert clear_day([0.2 * i for i in range(1, 21)]) == pytest.approx(3.8)


def test_cloud_map_learns_attenuation_and_shape():
    rng = random.Random(3)
    samples = []
    for _ in range(60):
        clear, cloud = rng.uniform(1.5, 2.5), rng.uniform(0, 100)
        samples.append((clear, cloud, clear * (1 - 0.5 * (cloud / 100) ** 2.0)))
    cmap = fit_cloud_map(samples)
    assert cmap.p == 2.0
    assert cmap.k == pytest.approx(0.5, abs=0.02)
    assert cmap.expected(2.0, 0) == 2.0


def test_cloud_map_needs_days():
    assert fit_cloud_map([(2, 50, 1)]) == CloudMap()


def test_comfort_moves_and_is_bounded():
    comfort = Comfort()
    comfort.learn(Season.WINTER, Feedback.TOO_COLD, direct=True, when="d")
    comfort.learn(Season.WINTER, Feedback.TOO_COLD, direct=False, when="d")
    assert comfort.winter == pytest.approx(0.35)
    assert comfort.summer == 0
    for _ in range(20):
        comfort.learn(Season.SUMMER, Feedback.TOO_WARM, direct=True, when="d")
    assert comfort.summer == -1.5


def test_cooling_thresholds_follow_the_pumps_draw():
    rng = random.Random(4)
    draws = [rng.gauss(1000, 150) for _ in range(500)]
    cut, resume = cooling_thresholds(draws)
    # Cut below a typical run (p50 1000 W), resume above nearly any run (p90 ~1190 W).
    assert 800 <= cut <= 900
    assert 1250 <= resume <= 1400
    assert cooling_thresholds(draws[:10]) is None


T0 = dt.datetime(2026, 7, 28, 12, 0, tzinfo=dt.UTC)


def _drive(gate, start, minutes, headroom, pump=1000.0, sun_up=True):
    for m in range(minutes):
        gate.update(start + dt.timedelta(minutes=m), headroom, pump, sun_up)
    return start + dt.timedelta(minutes=minutes)


def test_gate_cuts_after_ten_minutes_of_deficit():
    gate = SolarGate()
    t = _drive(gate, T0, 10, 500)
    assert not gate.paused
    _drive(gate, t, 1, 500)
    assert gate.paused


def test_gate_does_not_cut_an_idle_pump():
    gate = SolarGate()
    _drive(gate, T0, 30, 500, pump=8)
    assert not gate.paused


def test_gate_resumes_only_after_an_hour_and_sustained_surplus():
    gate = SolarGate()
    t = _drive(gate, T0, 11, 500)
    t = _drive(gate, t, 30, 2000)
    assert gate.paused  # surplus held 20 min, but not paused an hour yet
    _drive(gate, t, 31, 2000)
    assert not gate.paused


def test_gate_lost_metering_or_sunset_resumes():
    gate = SolarGate()
    t = _drive(gate, T0, 11, 500)
    gate.update(t, None, 1000, True)
    assert not gate.paused
    gate = SolarGate()
    t = _drive(gate, T0, 11, 500)
    gate.update(t, 500, 1000, False)
    assert not gate.paused


def test_gate_comfort_guard_blocks_a_pause():
    gate = SolarGate()
    for m in range(30):
        gate.update(T0 + dt.timedelta(minutes=m), 500, 1000, True, too_warm=True)
    assert not gate.paused


def test_gate_waits_45_minutes_between_cuts():
    gate = SolarGate()
    gate.resumed_at = T0
    _drive(gate, T0, 30, 500)
    assert not gate.paused


SP = Setpoints(sun_floor=19.0, window_heat=16.0, summer_pause=30.0, target=20.5)


def test_window_outranks_the_sun_hold():
    s = ZoneSituation(Season.WINTER, window_open=True, sun_hold=True, solar_paused=False)
    assert intent_for(s) is Intent.WINDOW_PAUSE
    assert setpoint_for(Intent.WINDOW_PAUSE, Season.WINTER, SP) == 16.0


def test_an_overridden_recommendation_falls_through():
    s = ZoneSituation(
        Season.WINTER,
        window_open=True,
        sun_hold=True,
        solar_paused=False,
        suppressed=frozenset({Intent.WINDOW_PAUSE}),
    )
    assert intent_for(s) is Intent.SUN_HOLD
    assert setpoint_for(Intent.SUN_HOLD, Season.WINTER, SP) == 19.0


def test_seasons_only_see_their_own_recommendations():
    assert (
        intent_for(ZoneSituation(Season.SUMMER, False, sun_hold=True, solar_paused=False))
        is Intent.SCHEDULE
    )
    assert intent_for(ZoneSituation(Season.SUMMER, False, False, True)) is Intent.SOLAR_PAUSE
    assert setpoint_for(Intent.SOLAR_PAUSE, Season.SUMMER, SP) == 30.0
    assert intent_for(ZoneSituation(Season.OFF, True, True, True)) is Intent.SCHEDULE


def test_surplus_is_stored_below_holds_and_windows():
    s = ZoneSituation(Season.WINTER, False, sun_hold=False, solar_paused=False, solar_surplus=True)
    assert intent_for(s) is Intent.SOLAR_BOOST
    assert setpoint_for(Intent.SOLAR_BOOST, Season.WINTER, SP) == 21.0
    assert setpoint_for(Intent.SOLAR_BOOST, Season.SUMMER, SP) == 20.0
    held = ZoneSituation(
        Season.WINTER, False, sun_hold=True, solar_paused=False, solar_surplus=True
    )
    assert intent_for(held) is Intent.SUN_HOLD
    windy = ZoneSituation(
        Season.SUMMER, True, sun_hold=False, solar_paused=False, solar_surplus=True
    )
    assert intent_for(windy) is Intent.WINDOW_PAUSE


@pytest.mark.parametrize(
    ("target", "season", "expected"),
    [
        (20.25, Season.WINTER, 21.0),  # 20.75: a tie, up
        (18.75, Season.WINTER, 19.5),  # 19.25: a tie, up - round() made it 19.0
        (20.3, Season.WINTER, 21.0),
        (25.25, Season.SUMMER, 25.0),  # 24.75: a tie, up - still in the comfort band
        (25.75, Season.SUMMER, 25.5),  # 25.25: a tie, up
    ],
)
def test_boost_ties_round_up_in_both_seasons(target, season, expected):
    sp = Setpoints(sun_floor=19.0, window_heat=16.0, summer_pause=30.0, target=target)
    assert setpoint_for(Intent.SOLAR_BOOST, season, sp) == expected


def test_surplus_needs_twenty_minutes_and_ends_after_ten_below():
    surplus = SolarSurplus()
    for m in range(20):
        surplus.update(T0 + dt.timedelta(minutes=m), 2000, True)
    assert not surplus.active
    surplus.update(T0 + dt.timedelta(minutes=20), 2000, True)
    assert surplus.active
    t = T0 + dt.timedelta(minutes=21)
    for m in range(5):
        surplus.update(t + dt.timedelta(minutes=m), 1200, True)  # between thresholds
    assert surplus.active
    for m in range(5, 16):
        surplus.update(t + dt.timedelta(minutes=m), 500, True)
    assert not surplus.active


def test_surplus_ends_at_sunset_or_lost_metering():
    surplus = SolarSurplus(active=True)
    assert not surplus.update(T0, 2000, False)
    surplus = SolarSurplus(active=True)
    assert not surplus.update(T0, None, True)
