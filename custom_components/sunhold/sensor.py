"""Sunhold's recommendations, as sensors a thermostat automation can follow."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import Intent, Season
from .engine import Engine, Zone
from .entity import SunholdEntity


@dataclass(frozen=True, kw_only=True)
class ZoneSensorDescription(SensorEntityDescription):
    """A per-zone sensor, valued from the engine."""

    value: Callable[[Engine, Zone], Any]


def _plan(engine: Engine, zone: Zone) -> dict:
    return engine.journal.peek(dt_util.now().date(), zone.zone_id).get("plan") or {}


def _target(engine: Engine, zone: Zone) -> float | None:
    season = engine.season(zone)
    return None if season is Season.OFF else round(engine.target(zone, season), 2)


ZONE_SENSORS = (
    ZoneSensorDescription(
        key="recommendation",
        device_class=SensorDeviceClass.ENUM,
        options=[i.value for i in Intent],
        value=lambda e, z: z.intent.value,
    ),
    ZoneSensorDescription(
        key="setpoint",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value=lambda e, z: z.setpoint,
    ),
    ZoneSensorDescription(
        key="comfort_target",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value=_target,
    ),
    ZoneSensorDescription(
        key="predicted_peak",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value=lambda e, z: _plan(e, z).get("predicted_peak"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the house sensors, then each zone's under its subentry."""
    engine: Engine = entry.runtime_data
    async_add_entities([HeadroomSensor(engine)])
    for zone in engine.zones.values():
        async_add_entities(
            [ZoneSensor(engine, zone, d) for d in ZONE_SENSORS],
            config_subentry_id=zone.zone_id,
        )


class ZoneSensor(SunholdEntity, SensorEntity):
    """One value of one zone."""

    entity_description: ZoneSensorDescription

    def __init__(self, engine: Engine, zone: Zone, description: ZoneSensorDescription) -> None:
        """Bind the description."""
        super().__init__(engine, description.key, zone)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the engine's value."""
        return self.entity_description.value(self.engine, self.zone)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Give the recommendation what an automation needs to act on it."""
        if self.entity_description.key != "recommendation":
            return None
        until = self.engine.until(self.zone)
        return {
            "since": self.zone.since.isoformat() if self.zone.since else None,
            "until": until.isoformat() if until else None,
            "season": self.engine.season(self.zone).value,
        }


class HeadroomSensor(SunholdEntity, SensorEntity):
    """Watts the panels could give the heat pump, the solar gate's input."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, engine: Engine) -> None:
        """Attach to the house."""
        super().__init__(engine, "solar_headroom")

    @property
    def native_value(self) -> float | None:
        """Return the headroom, quantised so the recorder is not written every second."""
        value = self.engine.headroom()
        return None if value is None else round(value / 25) * 25
