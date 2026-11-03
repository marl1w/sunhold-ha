"""Sunhold: a self-learning heating and cooling assistant.

Decides, per thermostat zone, when the schedule should be left alone and when it should
give way - to the sun in winter, to the solar panels in summer, to an open window always -
and publishes that as entities. Driving the thermostats is left to whatever follows those
entities, so Sunhold works with any device.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .const import DOMAIN, PANEL_URL_PATH, STATIC_URL, Feedback
from .engine import Engine
from .journal import Journal

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR, Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_FEEDBACK = "feedback"
FEEDBACK_SCHEMA = vol.Schema(
    {
        vol.Required("signal"): vol.In([f.value for f in Feedback]),
        vol.Optional("zone"): cv.string,
        vol.Optional("date"): cv.date,
    }
)

type SunholdConfigEntry = ConfigEntry[Engine]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register what does not depend on an entry: the websocket API and the service."""
    websocket_api.async_register_command(hass, ws_overview)
    websocket_api.async_register_command(hass, ws_feedback)

    async def feedback(call: ServiceCall) -> None:
        engine = _engine(hass)
        if engine is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
        wanted = call.data.get("zone")
        zones = [
            z.zone_id
            for z in engine.zones.values()
            if wanted in (None, z.zone_id, z.title, z.climate)
        ]
        if not engine.feedback(zones, Feedback(call.data["signal"]), call.data.get("date")):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="feedback_not_recorded"
            )

    hass.services.async_register(DOMAIN, SERVICE_FEEDBACK, feedback, schema=FEEDBACK_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SunholdConfigEntry) -> bool:
    """Start the engine and show the panel."""
    journal = Journal(hass, entry.entry_id)
    await journal.async_load()
    engine = Engine(hass, entry, journal)
    entry.runtime_data = engine
    await engine.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Subentries (zones) changing reload the entry like the house settings do.
    entry.async_on_unload(entry.add_update_listener(_reload))

    # Static paths cannot be unregistered, so they are registered once per run.
    if not hass.data.get(DOMAIN):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend"), True)]
        )
        hass.data[DOMAIN] = True
    if PANEL_URL_PATH not in hass.data.get(frontend.DATA_PANELS, {}):
        await panel_custom.async_register_panel(
            hass,
            webcomponent_name="sunhold-panel",
            frontend_url_path=PANEL_URL_PATH,
            module_url=f"{STATIC_URL}/sunhold-panel.js",
            sidebar_title="Sunhold",
            sidebar_icon="mdi:sun-thermometer",
            require_admin=False,
        )
    return True


async def _reload(hass: HomeAssistant, entry: SunholdConfigEntry) -> None:
    """Zones added, changed or removed: start over with the new set."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SunholdConfigEntry) -> bool:
    """Stop the engine and remove the panel."""
    await entry.runtime_data.async_stop()
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: SunholdConfigEntry) -> None:
    """Forget everything learned along with the entry."""
    await Journal(hass, entry.entry_id).async_remove()


def _engine(hass: HomeAssistant) -> Engine | None:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0].runtime_data if entries else None


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sunhold/overview",
        vol.Optional("days", default=60): vol.All(int, vol.Range(min=1, max=400)),
    }
)
@callback
def ws_overview(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Return everything the panel shows."""
    engine = _engine(hass)
    if engine is None:
        connection.send_error(msg["id"], "not_loaded", "Sunhold is not set up")
        return
    connection.send_result(msg["id"], engine.overview(msg["days"]))


# Open to every signed-in user on purpose: the people living in the house are the ones
# whose comfort it learns. The damage is bounded - one step per zone and day, the offset
# capped, only the last month.
@websocket_api.websocket_command(
    {
        vol.Required("type"): "sunhold/feedback",
        vol.Required("zone_id"): str,
        vol.Required("signal"): vol.In([f.value for f in Feedback]),
        vol.Optional("date"): str,
    }
)
@callback
def ws_feedback(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Record feedback from the panel."""
    engine = _engine(hass)
    if engine is None:
        connection.send_error(msg["id"], "not_loaded", "Sunhold is not set up")
        return
    try:
        day = dt.date.fromisoformat(msg["date"]) if "date" in msg else dt_util.now().date()
    except ValueError:
        connection.send_error(msg["id"], "invalid_date", "Dates are YYYY-MM-DD")
        return
    if not engine.feedback([msg["zone_id"]], Feedback(msg["signal"]), day):
        connection.send_error(
            msg["id"], "not_recorded", "Unknown zone, or a day outside the last 30"
        )
        return
    connection.send_result(msg["id"], {"ok": True})
