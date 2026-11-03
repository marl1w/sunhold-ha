"""The summer solar pause as an on/off entity, for automations and dashboards."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .engine import Engine
from .entity import SunholdEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the solar pause for the house."""
    engine: Engine = entry.runtime_data
    async_add_entities([SolarPause(engine)])


class SolarPause(SunholdEntity, BinarySensorEntity):
    """On while the panels cannot carry the heat pump and cooling should wait."""

    def __init__(self, engine: Engine) -> None:
        """Attach to the house."""
        super().__init__(engine, "solar_pause")

    @property
    def is_on(self) -> bool:
        """Return whether cooling is paused for the sun."""
        return self.engine.gate.paused
