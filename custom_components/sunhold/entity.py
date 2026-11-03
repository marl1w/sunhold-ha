"""Shared base for Sunhold's entities: one device for the house, one per zone."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .engine import SIGNAL_UPDATE, Engine, Zone


class SunholdEntity(Entity):
    """Refreshes whenever the engine says something changed."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, engine: Engine, key: str, zone: Zone | None = None) -> None:
        """Attach to the house device, or to a zone's."""
        self.engine = engine
        self.zone = zone
        self._attr_translation_key = key
        owner = zone.zone_id if zone else engine.entry.entry_id
        self._attr_unique_id = f"{owner}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, owner)},
            name=f"Sunhold {zone.title}" if zone else "Sunhold",
            manufacturer="Sunhold",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Follow the engine."""
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
