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
