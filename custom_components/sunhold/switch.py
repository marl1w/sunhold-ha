"""One switch per thing Sunhold can recommend, so each can be turned off on its own."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .engine import Engine
from .entity import SunholdEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the feature switches to the house."""
    engine: Engine = entry.runtime_data
    async_add_entities(FeatureSwitch(engine, key) for key in engine.features)


class FeatureSwitch(SunholdEntity, SwitchEntity, RestoreEntity):
    """Turns one of Sunhold's recommendations on or off, remembered across restarts."""

    def __init__(self, engine: Engine, key: str) -> None:
        """Bind to one feature."""
        super().__init__(engine, key)
        self._key = key

    async def async_added_to_hass(self) -> None:
        """Restore the last choice."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self.engine.features[self._key] = last.state != STATE_OFF
            self.engine.async_refresh()

    @property
    def is_on(self) -> bool:
        """Return whether the feature is on."""
        return self.engine.features[self._key]

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the feature on."""
        self.engine.features[self._key] = True
        self.engine.async_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the feature off; its recommendations end at once."""
        self.engine.features[self._key] = False
        self.engine.async_refresh()
