"""Which of today's meals have been eaten, kept in Home Assistant so every
device (phone, tablet, PC) sees the same ticks and so adherence can be computed."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1
KEEP_DAYS = 90


class EatenStore:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.eaten_{entry_id}")
        self._days: dict[str, set[str]] = {}
        self._listeners: list[Callable[[], None]] = []

    async def async_load(self) -> None:
        stored = await self._store.async_load() or {}
        cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
        self._days = {
            day: set(franjas) for day, franjas in (stored.get("days") or {}).items() if isinstance(franjas, list) and day >= cutoff
        }

    def get(self, day: date) -> set[str]:
        return set(self._days.get(day.isoformat(), set()))

    def mark(self, day: date, franja: str, eaten: bool) -> None:
        franjas = self._days.setdefault(day.isoformat(), set())
        if eaten:
            franjas.add(franja)
        else:
            franjas.discard(franja)
        if not franjas:
            self._days.pop(day.isoformat(), None)
        self._store.async_delay_save(self._data_to_save, 5)
        for listener in list(self._listeners):
            listener()

    @callback
    def _data_to_save(self) -> dict:
        return {"days": {day: sorted(franjas) for day, franjas in self._days.items()}}

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove
