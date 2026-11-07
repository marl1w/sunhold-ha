"""Setting Sunhold up: the house, a zone, and changing both later."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sunhold.const import DOMAIN

HOUSE = {
    "weather": "weather.home",
    "outdoor_temperature": "sensor.outdoor",
    "hold_until": "13:00:00",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Start the recorder before Home Assistant: Sunhold depends on it."""
    return enable_custom_integrations


async def test_house_then_zone(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], HOUSE)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "zone"), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Ground floor",
            "climate": "climate.ground",
            "temperature_sensor": "sensor.ground_temperature",
            "winter_target": 20.5,
            "summer_target": 25.5,
            "sun_floor": 19.0,
            "window_pause_heat": 16.0,
            "summer_pause": 30.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (zone,) = entry.subentries.values()
    assert zone.title == "Ground floor"
    assert "name" not in zone.data


async def test_reconfigure_the_house(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=HOUSE)
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**HOUSE, "hold_until": "12:30:00"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["hold_until"] == "12:30:00"


async def test_thresholds_default_to_the_built_in_values(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], HOUSE)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].data["solar_thresholds"] == {
        "winter_surplus_on_w": 1000,
        "winter_surplus_off_w": 600,
        "summer_resume_w": 1500,
        "summer_cut_w": 900,
        "learn_summer_thresholds": True,
    }


@pytest.mark.parametrize(
    ("thresholds", "error"),
    [
        ({"winter_surplus_on_w": 600, "winter_surplus_off_w": 600}, "winter_order"),
        ({"summer_resume_w": 800, "summer_cut_w": 900}, "summer_order"),
    ],
)
async def test_a_threshold_pair_needs_a_gap(hass: HomeAssistant, thresholds, error) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**HOUSE, "solar_thresholds": thresholds}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"solar_thresholds": error}


async def test_reconfigure_the_thresholds(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=HOUSE)  # set up before thresholds existed
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **HOUSE,
            "solar_thresholds": {"winter_surplus_on_w": 1200, "learn_summer_thresholds": False},
        },
    )
    assert result["reason"] == "reconfigure_successful"
    t = entry.data["solar_thresholds"]
    assert (t["winter_surplus_on_w"], t["winter_surplus_off_w"]) == (1200, 600)
    assert t["learn_summer_thresholds"] is False
