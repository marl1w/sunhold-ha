"""What Sunhold remembers: one record per day, the learned models, and an event log.

The day records are both the panel's history and the training data, so what the panel
shows is exactly what the models learned from.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION

KEEP_DAYS = 800
KEEP_EVENTS = 1000
KEEP_DRAWS = 3000
SAVE_DELAY = 30


class Journal:
    """A thin, typed-by-convention wrapper over one JSON store."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Bind the store to its config entry."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry_id}"
        )
        self.data: dict[str, Any] = {}

    async def async_load(self) -> None:
        """Read the store, starting empty if there is none yet."""
        self.data = await self._store.async_load() or {}
        for key in ("days", "models", "comfort", "summer"):
            self.data.setdefault(key, {})
        self.data.setdefault("events", [])
        self.data.setdefault("cloud_map", {})

    def save(self) -> None:
        """Schedule a write; bursts of changes collapse into one."""
        self._store.async_delay_save(lambda: self.data, SAVE_DELAY)

    async def async_flush(self) -> None:
        """Write now, on unload."""
        await self._store.async_save(self.data)

    async def async_remove(self) -> None:
        """Delete the store with the entry."""
        await self._store.async_remove()

    # ------------------------------------------------------------- days

    def day(self, day: dt.date) -> dict[str, Any]:
        """Return a day's record, creating it."""
        return self.data["days"].setdefault(day.isoformat(), {"house": {}, "zones": {}})

    def zone_day(self, day: dt.date, zone_id: str) -> dict[str, Any]:
        """Return one zone's part of a day's record, creating it."""
        return self.day(day)["zones"].setdefault(zone_id, {})

    def peek(self, day: dt.date, zone_id: str) -> dict[str, Any]:
        """Return one zone's part of a day's record without creating anything."""
        return self.data["days"].get(day.isoformat(), {}).get("zones", {}).get(zone_id, {})

    def days(self) -> list[tuple[dt.date, dict[str, Any]]]:
        """Return every day, oldest first."""
        return sorted((dt.date.fromisoformat(k), v) for k, v in self.data["days"].items())

    def prune(self, today: dt.date) -> None:
        """Forget days and events past their keep limits."""
        cutoff = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
        for key in [k for k in self.data["days"] if k < cutoff]:
            del self.data["days"][key]
        del self.data["events"][:-KEEP_EVENTS]
        draws = self.data["summer"].get("draws", [])
        del draws[:-KEEP_DRAWS]

    # ------------------------------------------------------------- events

    def event(self, when: dt.datetime, kind: str, zone: str | None = None, **detail: Any) -> None:
        """Append to the event log the panel's timeline is drawn from."""
        self.data["events"].append(
            {"at": when.isoformat(timespec="seconds"), "kind": kind, "zone": zone, **detail}
        )
        self.save()
