"""The engine inside a real Home Assistant: entities follow the house."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.sunhold.const import DOMAIN, Intent

HOUSE = {
    "weather": "weather.home",
    "outdoor_temperature": "sensor.outdoor",
    "sun_sensor": "sensor.sun",
    "pv_power": "sensor.pv",
    "house_power": "sensor.house",
    "heat_pump_power": "sensor.pump",
    "hold_until": "13:00:00",
}
ZONE = {
    "climate": "climate.ground",
    "temperature_sensor": "sensor.ground_temperature",
    "openings": ["binary_sensor.kitchen_door"],
    "winter_target": 20.5,
    "summer_target": 25.5,
    "sun_floor": 19.0,
    "window_pause_heat": 16.0,
    "summer_pause": 30.0,
}


def _states(hass: HomeAssistant, mode: str = "heat", target: float = 20.0) -> None:
    hass.states.async_set(
        "climate.ground", mode, {"temperature": target, "hvac_modes": ["off", "heat", "auto"]}
    )
    hass.states.async_set("sensor.ground_temperature", "19.7")
    hass.states.async_set("sensor.outdoor", "8")
    hass.states.async_set("binary_sensor.kitchen_door", "off")
    for sensor, value in (("sensor.pv", 2500), ("sensor.house", 1500), ("sensor.pump", 1000)):
        hass.states.async_set(sensor, str(value))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Start the recorder before Home Assistant: Sunhold depends on it."""
    return enable_custom_integrations


@pytest.fixture
async def engine(hass: HomeAssistant, freezer: FrozenDateTimeFactory):
    """Set Sunhold up on one zone at 04:00 on a November morning."""
    await hass.config.async_set_time_zone("Europe/Rome")
    hass.config.latitude, hass.config.longitude = 41.9, 12.5
    freezer.move_to(dt.datetime(2026, 11, 3, 3, 0, tzinfo=dt.UTC))  # 04:00 local
    _states(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=HOUSE,
        subentries_data=[
            ConfigSubentryData(
                data=ZONE, subentry_type="zone", title="Ground floor", unique_id=None
            )
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


def _rec(hass: HomeAssistant):
    return hass.states.get("sensor.sunhold_ground_floor_recommendation")


async def _advance(hass, freezer, minutes: int) -> None:
    for _ in range(minutes):
        freezer.tick(dt.timedelta(minutes=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def test_entities_start_on_schedule(engine, hass: HomeAssistant) -> None:
    assert _rec(hass).state == "schedule"
    assert hass.states.get("sensor.sunhold_ground_floor_set_point").state == "unknown"
    assert hass.states.get("switch.sunhold_sun_hold").state == "on"
    assert float(hass.states.get("sensor.sunhold_ground_floor_comfort_target").state) == 20.5


async def test_window_pauses_after_five_minutes_and_resumes(engine, hass, freezer) -> None:
    hass.states.async_set("binary_sensor.kitchen_door", "on")
    await _advance(hass, freezer, 4)
    assert _rec(hass).state == "schedule"
    await _advance(hass, freezer, 2)
    assert _rec(hass).state == "window_pause"
    assert float(hass.states.get("sensor.sunhold_ground_floor_set_point").state) == 16.0
    hass.states.async_set("binary_sensor.kitchen_door", "off")
    await _advance(hass, freezer, 3)
    assert _rec(hass).state == "schedule"


async def test_window_pause_switch_off_ends_it(engine, hass, freezer) -> None:
    hass.states.async_set("binary_sensor.kitchen_door", "on")
    await _advance(hass, freezer, 6)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.sunhold_window_pause"}, blocking=True
    )
    assert _rec(hass).state == "schedule"


async def test_sunny_morning_holds_until_the_end_time(engine, hass, freezer) -> None:
    with patch.object(engine, "_forecast", return_value={"cloud": 20.0, "outdoor": 10.0}):
        freezer.move_to(dt.datetime(2026, 11, 3, 4, 30, tzinfo=dt.UTC))  # 05:30 local
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert _rec(hass).state == "sun_hold"
    assert _rec(hass).attributes["until"].startswith("2026-11-03T13:00")
    assert float(hass.states.get("sensor.sunhold_ground_floor_set_point").state) == 19.0
    freezer.move_to(dt.datetime(2026, 11, 3, 12, 0, tzinfo=dt.UTC))  # 13:00 local
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _rec(hass).state == "schedule"


async def test_cloudy_morning_does_not_hold(engine, hass, freezer) -> None:
    with patch.object(engine, "_forecast", return_value={"cloud": 90.0, "outdoor": 6.0}):
        freezer.move_to(dt.datetime(2026, 11, 3, 4, 30, tzinfo=dt.UTC))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert _rec(hass).state == "schedule"


async def test_a_person_overriding_a_hold_is_learned(engine, hass, freezer) -> None:
    with patch.object(engine, "_forecast", return_value={"cloud": 20.0, "outdoor": 10.0}):
        freezer.move_to(dt.datetime(2026, 11, 3, 4, 30, tzinfo=dt.UTC))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    # The thermostat follows the recommendation, then someone turns it up.
    _states(hass, target=19.0)
    await _advance(hass, freezer, 1)
    _states(hass, target=21.0)
    await _advance(hass, freezer, 4)
    assert _rec(hass).state == "schedule"
    assert engine.comfort(next(iter(engine.zones))).winter == pytest.approx(0.1)
    assert float(hass.states.get("sensor.sunhold_ground_floor_comfort_target").state) == 20.6


async def test_a_thermostat_that_never_followed_is_not_a_person(engine, hass, freezer) -> None:
    with patch.object(engine, "_forecast", return_value={"cloud": 20.0, "outdoor": 10.0}):
        freezer.move_to(dt.datetime(2026, 11, 3, 4, 30, tzinfo=dt.UTC))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    await _advance(hass, freezer, 10)
    assert _rec(hass).state == "sun_hold"
    assert engine.comfort(next(iter(engine.zones))).winter == 0


async def test_feedback_counts_once_per_day_and_a_change_replaces_it(engine, hass) -> None:
    zone_id = next(iter(engine.zones))
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.sunhold_ground_floor_too_cold"}, blocking=True
    )
    assert engine.comfort(zone_id).winter == pytest.approx(0.25)
    await hass.services.async_call(DOMAIN, "feedback", {"signal": "too_cold"}, blocking=True)
    assert engine.comfort(zone_id).winter == pytest.approx(0.25)
    await hass.services.async_call(
        DOMAIN, "feedback", {"signal": "too_warm", "zone": "Ground floor"}, blocking=True
    )
    assert engine.comfort(zone_id).winter == pytest.approx(-0.25)


async def test_feedback_outside_the_last_month_is_refused(engine, hass) -> None:
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "feedback", {"signal": "too_cold", "date": "2020-01-01"}, blocking=True
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "feedback", {"signal": "too_cold", "zone": "Attic"}, blocking=True
        )


async def test_a_notification_tap_counts_once_after_a_reload(engine, hass) -> None:
    entry = engine.entry
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    engine = entry.runtime_data
    zone_id = next(iter(engine.zones))
    hass.bus.async_fire(
        "mobile_app_notification_action", {"action": f"SUNHOLD|2026-11-03|too_cold|{zone_id}"}
    )
    hass.bus.async_fire("mobile_app_notification_action", {"action": "SUNHOLD|bad|x|y"})
    await hass.async_block_till_done()
    assert engine.comfort(zone_id).winter == pytest.approx(0.25)


async def _sunny_morning(engine, hass, freezer) -> None:
    with patch.object(engine, "_forecast", return_value={"cloud": 20.0, "outdoor": 10.0}):
        freezer.move_to(dt.datetime(2026, 11, 3, 4, 30, tzinfo=dt.UTC))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def test_an_override_running_out_is_not_a_person(engine, hass, freezer) -> None:
    await _sunny_morning(engine, hass, freezer)
    hass.states.async_set(
        "climate.ground", "heat", {"temperature": 19.0, "operating_mode": "manual"}
    )
    await _advance(hass, freezer, 1)
    # The timed override lapses: back on the schedule, at the schedule's value.
    hass.states.async_set("climate.ground", "heat", {"temperature": 20.0, "operating_mode": "auto"})
    await _advance(hass, freezer, 5)
    assert _rec(hass).state == "sun_hold"
    assert engine.comfort(next(iter(engine.zones))).winter == 0


async def test_turning_a_hold_down_wins_but_teaches_nothing(engine, hass, freezer) -> None:
    await _sunny_morning(engine, hass, freezer)
    _states(hass, target=19.0)
    await _advance(hass, freezer, 1)
    _states(hass, target=17.0)
    await _advance(hass, freezer, 4)
    assert _rec(hass).state == "schedule"
    assert engine.comfort(next(iter(engine.zones))).winter == 0


async def test_a_thermostat_blip_keeps_the_recommendation(engine, hass, freezer) -> None:
    hass.states.async_set("binary_sensor.kitchen_door", "on")
    await _advance(hass, freezer, 6)
    hass.states.async_set("climate.ground", "unavailable")
    await _advance(hass, freezer, 2)
    assert _rec(hass).state == "window_pause"


async def test_midnight_forgets_todays_overrides(engine, hass, freezer) -> None:
    zone = next(iter(engine.zones.values()))
    zone.suppressed.add(Intent.WINDOW_PAUSE)
    freezer.move_to(dt.datetime(2026, 11, 3, 23, 0, 10, tzinfo=dt.UTC))  # 00:00:10 local
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not zone.suppressed


async def test_summer_gate_pauses_cooling_when_the_panels_fall_short(engine, hass, freezer) -> None:
    freezer.move_to(dt.datetime(2027, 7, 28, 10, 0, tzinfo=dt.UTC))
    _states(hass, mode="cool", target=24.0)
    hass.states.async_set("sensor.ground_temperature", "25.0")
    hass.states.async_set("sensor.pv", "1200")  # headroom 1200 - (1500 - 1000) = 700 W
    await _advance(hass, freezer, 12)
    assert hass.states.get("binary_sensor.sunhold_solar_cooling_pause").state == "on"
    assert _rec(hass).state == "solar_pause"
    assert float(hass.states.get("sensor.sunhold_ground_floor_set_point").state) == 30.0


async def test_summer_comfort_guard_ends_a_pause(engine, hass, freezer) -> None:
    freezer.move_to(dt.datetime(2027, 7, 28, 10, 0, tzinfo=dt.UTC))
    _states(hass, mode="cool", target=24.0)
    hass.states.async_set("sensor.pv", "1200")
    hass.states.async_set("sensor.ground_temperature", "27.0")  # past 25.5 + 1
    await _advance(hass, freezer, 12)
    assert _rec(hass).state == "schedule"


async def test_overview_has_what_the_panel_needs(engine, hass) -> None:
    data = engine.overview()
    assert data["zones"][0]["title"] == "Ground floor"
    assert {"gate", "cloud_map", "days", "events", "features"} <= set(data)


async def test_winter_surplus_stores_heat_up_to_the_band(engine, hass, freezer) -> None:
    freezer.move_to(dt.datetime(2026, 11, 3, 10, 0, tzinfo=dt.UTC))  # 11:00 local
    hass.states.async_set("sensor.pv", "2600")  # headroom 2600 - (1500 - 1000) = 2100 W
    await _advance(hass, freezer, 22)
    assert _rec(hass).state == "solar_boost"
    assert float(hass.states.get("sensor.sunhold_ground_floor_set_point").state) == 21.0
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.sunhold_solar_boost"}, blocking=True
    )
    assert _rec(hass).state == "schedule"


async def test_a_solar_pause_survives_a_restart(engine, hass, freezer) -> None:
    freezer.move_to(dt.datetime(2027, 7, 28, 10, 0, tzinfo=dt.UTC))
    _states(hass, mode="cool", target=24.0)
    hass.states.async_set("sensor.ground_temperature", "25.0")
    hass.states.async_set("sensor.pv", "1200")
    await _advance(hass, freezer, 12)
    assert engine.gate.paused
    assert await hass.config_entries.async_reload(engine.entry.entry_id)
    await hass.async_block_till_done()
    assert engine.entry.runtime_data.gate.paused
    assert _rec(hass).state == "solar_pause"
