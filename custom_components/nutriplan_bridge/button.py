"""Buttons: refresh now / mark the chat as read."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DietoProApiError, DietoProAuthError
from .const import DOMAIN
from .coordinator import DietoProDataUpdateCoordinator
from .sensor import build_device_info


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: DietoProDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RefreshButton(coordinator, entry), MarkChatReadButton(coordinator, entry)])


class _DietoProButton(CoordinatorEntity[DietoProDataUpdateCoordinator], ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: DietoProDataUpdateCoordinator, entry: ConfigEntry, key: str, name: str, icon: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = build_device_info(entry)


class RefreshButton(_DietoProButton):
    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "actualizar", "Actualizar ahora", "mdi:refresh")

    async def async_press(self) -> None:
        await self.coordinator.async_refresh()


class MarkChatReadButton(_DietoProButton):
    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "marcar_chat_leido", "Marcar chat como leído", "mdi:message-check")

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.async_mark_chat_read()
        except (DietoProApiError, DietoProAuthError) as err:
            raise HomeAssistantError(f"No se pudo marcar el chat como leído: {err}") from err
        await self.coordinator.async_request_refresh()
