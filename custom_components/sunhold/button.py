"""Feedback buttons per zone: how today feels, in one press from anywhere."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import Feedback
from .engine import Engine, Zone
from .entity import SunholdEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add three feedback buttons to every zone."""
    engine: Engine = entry.runtime_data
    for zone in engine.zones.values():
        async_add_entities(
            [FeedbackButton(engine, zone, signal) for signal in Feedback],
            config_subentry_id=zone.zone_id,
        )


class FeedbackButton(SunholdEntity, ButtonEntity):
    """Says how today feels in one zone."""

    def __init__(self, engine: Engine, zone: Zone, signal: Feedback) -> None:
        """Bind to a zone and a feeling."""
        super().__init__(engine, f"feedback_{signal.value}", zone)
        self._signal = signal

    async def async_added_to_hass(self) -> None:
        """Skip the engine's updates: a button's state is only when it was last pressed."""
        await super(SunholdEntity, self).async_added_to_hass()

    async def async_press(self) -> None:
        """Record the feedback for today."""
        self.engine.feedback([self.zone.zone_id], self._signal)
