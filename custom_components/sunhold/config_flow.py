"""Setup for Sunhold: the house once, then one subentry per thermostat zone."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector
import voluptuous as vol

from .const import (
    CONF_CLIMATE,
    CONF_HOLD_UNTIL,
    CONF_HOUSE_POWER,
    CONF_LEARN_SUMMER,
    CONF_NOTIFY,
    CONF_OPENINGS,
    CONF_OUTDOOR_TEMP,
    CONF_PUMP_POWER,
    CONF_PV_POWER,
    CONF_SUMMER_CUT_W,
    CONF_SUMMER_PAUSE,
    CONF_SUMMER_RESUME_W,
    CONF_SUMMER_TARGET,
    CONF_SUN,
    CONF_SUN_FLOOR,
    CONF_THRESHOLDS,
    CONF_WEATHER,
    CONF_WINDOW_HEAT,
    CONF_WINTER_OFF_W,
    CONF_WINTER_ON_W,
    CONF_WINTER_TARGET,
    CONF_ZONE_TEMP,
    DEFAULT_CUT_W,
    DEFAULT_HOLD_UNTIL,
    DEFAULT_RESUME_W,
    DEFAULT_SUMMER_PAUSE,
    DEFAULT_SUMMER_TARGET,
    DEFAULT_SUN_FLOOR,
    DEFAULT_WINDOW_HEAT,
    DEFAULT_WINTER_OFF_W,
    DEFAULT_WINTER_ON_W,
    DEFAULT_WINTER_TARGET,
    DOMAIN,
    SUBENTRY_ZONE,
)

_TEMP = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=5, max=35, step=0.5, unit_of_measurement="°C", mode=selector.NumberSelectorMode.BOX
    )
)


_WATTS = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0, max=20000, step=50, unit_of_measurement="W", mode=selector.NumberSelectorMode.BOX
    )
)


def _sensor(device_class: str | list[str]) -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor", device_class=device_class)
    )


def _house_schema(current: dict[str, Any]) -> vol.Schema:
    def opt(key: str) -> vol.Optional:
        return vol.Optional(key, description={"suggested_value": current.get(key)})

    return vol.Schema(
        {
            vol.Required(CONF_WEATHER, default=current.get(CONF_WEATHER, vol.UNDEFINED)): (
                selector.EntitySelector(selector.EntitySelectorConfig(domain="weather"))
            ),
            vol.Required(
                CONF_OUTDOOR_TEMP, default=current.get(CONF_OUTDOOR_TEMP, vol.UNDEFINED)
            ): _sensor("temperature"),
            opt(CONF_SUN): _sensor(["irradiance", "power"]),
            opt(CONF_PV_POWER): _sensor("power"),
            opt(CONF_HOUSE_POWER): _sensor("power"),
            opt(CONF_PUMP_POWER): _sensor("power"),
            opt(CONF_NOTIFY): selector.TextSelector(),
            vol.Required(
                CONF_HOLD_UNTIL, default=current.get(CONF_HOLD_UNTIL, DEFAULT_HOLD_UNTIL)
            ): selector.TimeSelector(),
            vol.Required(CONF_THRESHOLDS, default={}): section(
                _thresholds_schema(current.get(CONF_THRESHOLDS, {})), {"collapsed": True}
            ),
        }
    )


def _thresholds_schema(current: dict[str, Any]) -> vol.Schema:
    def watts(key: str, default: float) -> vol.Required:
        return vol.Required(key, default=current.get(key, default))

    return vol.Schema(
        {
            watts(CONF_WINTER_ON_W, DEFAULT_WINTER_ON_W): _WATTS,
            watts(CONF_WINTER_OFF_W, DEFAULT_WINTER_OFF_W): _WATTS,
            watts(CONF_SUMMER_RESUME_W, DEFAULT_RESUME_W): _WATTS,
            watts(CONF_SUMMER_CUT_W, DEFAULT_CUT_W): _WATTS,
            vol.Required(
                CONF_LEARN_SUMMER, default=current.get(CONF_LEARN_SUMMER, True)
            ): selector.BooleanSelector(),
        }
    )


def _threshold_errors(user_input: dict[str, Any]) -> dict[str, str]:
    """Each pair needs a gap, or the surplus and the pause would flicker."""
    t = user_input.get(CONF_THRESHOLDS, {})
    if t.get(CONF_WINTER_OFF_W, 0) >= t.get(CONF_WINTER_ON_W, 1):
        return {CONF_THRESHOLDS: "winter_order"}
    if t.get(CONF_SUMMER_CUT_W, 0) >= t.get(CONF_SUMMER_RESUME_W, 1):
        return {CONF_THRESHOLDS: "summer_order"}
    return {}


class SunholdConfigFlow(ConfigFlow, domain=DOMAIN):
    """One Sunhold per house."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the house-wide inputs."""
        errors = _threshold_errors(user_input) if user_input is not None else {}
        if user_input is not None and not errors:
            return self.async_create_entry(title="Sunhold", data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=_house_schema(user_input or {}), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the house-wide inputs."""
        entry = self._get_reconfigure_entry()
        errors = _threshold_errors(user_input) if user_input is not None else {}
        if user_input is not None and not errors:
            return self.async_update_reload_and_abort(entry, data=user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_house_schema(user_input or dict(entry.data)),
            errors=errors,
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Zones are added as subentries."""
        return {SUBENTRY_ZONE: ZoneSubentryFlow}


def _zone_schema(current: dict[str, Any]) -> vol.Schema:
    def req(key: str, default: Any = vol.UNDEFINED) -> vol.Required:
        return vol.Required(key, default=current.get(key, default))

    return vol.Schema(
        {
            req("name"): selector.TextSelector(),
            req(CONF_CLIMATE): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="climate")
            ),
            req(CONF_ZONE_TEMP): _sensor("temperature"),
            vol.Optional(CONF_OPENINGS, default=current.get(CONF_OPENINGS, [])): (
                selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="binary_sensor",
                        device_class=["door", "window", "opening", "garage_door"],
                        multiple=True,
                    )
                )
            ),
            req(CONF_WINTER_TARGET, DEFAULT_WINTER_TARGET): _TEMP,
            req(CONF_SUMMER_TARGET, DEFAULT_SUMMER_TARGET): _TEMP,
            req(CONF_SUN_FLOOR, DEFAULT_SUN_FLOOR): _TEMP,
            req(CONF_WINDOW_HEAT, DEFAULT_WINDOW_HEAT): _TEMP,
            req(CONF_SUMMER_PAUSE, DEFAULT_SUMMER_PAUSE): _TEMP,
        }
    )


class ZoneSubentryFlow(ConfigSubentryFlow):
    """Add or change one thermostat zone."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a zone."""
        if user_input is not None:
            data = {k: v for k, v in user_input.items() if k != "name"}
            return self.async_create_entry(title=user_input["name"], data=data)
        return self.async_show_form(step_id="user", data_schema=_zone_schema({}))

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change a zone."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            data = {k: v for k, v in user_input.items() if k != "name"}
            return self.async_update_and_abort(
                self._get_entry(), subentry, title=user_input["name"], data=data
            )
        current = {**subentry.data, "name": subentry.title}
        return self.async_show_form(step_id="reconfigure", data_schema=_zone_schema(current))
