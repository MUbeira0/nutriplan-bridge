"""Binary sensors: handy on/off conditions for automations."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import DietoProDataUpdateCoordinator, plan_start_date, unread_count
from .sensor import CITA_DATE_KEYS, _first_present, _parse_datetime, _scan_cita_date, build_device_info, seguimiento_history

MEASUREMENT_DUE_DAYS = 7


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: DietoProDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            UnreadMessagesBinary(coordinator, entry),
            AppointmentSoonBinary(coordinator, entry),
            NewPlanBinary(coordinator, entry),
            MeasurementDueBinary(coordinator, entry),
            AppointmentUnconfirmedBinary(coordinator, entry),
        ]
    )


class _DietoProBinary(CoordinatorEntity[DietoProDataUpdateCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: DietoProDataUpdateCoordinator, entry: ConfigEntry, key: str, name: str, icon: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = build_device_info(entry)

    _icon_on = "mdi:bell-ring"
    _icon_off = "mdi:bell-outline"

    @property
    def icon(self) -> str:
        return self._icon_on if self.is_on else self._icon_off

    @property
    def _data(self) -> dict:
        return self.coordinator.data or {}


class UnreadMessagesBinary(_DietoProBinary):
    _icon_on = "mdi:message-badge"
    _icon_off = "mdi:message-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "hay_mensajes", "Mensajes pendientes", "mdi:message-badge")

    @property
    def is_on(self) -> bool:
        return unread_count(self._data) > 0

    @property
    def extra_state_attributes(self) -> dict:
        return {"sin_leer": unread_count(self._data)}


class AppointmentSoonBinary(_DietoProBinary):
    """On when the appointment is within the next 24 hours."""

    _icon_on = "mdi:calendar-alert"
    _icon_off = "mdi:calendar-clock"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "cita_proxima", "Cita en 24 h", "mdi:calendar-alert")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_track_time_interval(self.hass, self._async_tick, timedelta(minutes=5)))

    @callback
    def _async_tick(self, _now) -> None:
        # @callback keeps it on the event loop (a lambda would run in a thread).
        self.async_write_ha_state()

    def _when(self):
        wrapper = self._data.get("cita")
        cita = wrapper.get("cita") if isinstance(wrapper, dict) else None
        if not isinstance(cita, dict):
            return None
        return _parse_datetime(_first_present(cita, CITA_DATE_KEYS)) or _scan_cita_date(cita)

    @property
    def is_on(self) -> bool:
        when = self._when()
        return when is not None and timedelta(0) <= when - dt_util.now() <= timedelta(hours=24)

    @property
    def extra_state_attributes(self) -> dict:
        when = self._when()
        return {"fecha": when.isoformat() if when else None}


class NewPlanBinary(_DietoProBinary):
    """On while the patient already has a plan that starts in the future."""

    _icon_on = "mdi:calendar-star"
    _icon_off = "mdi:calendar-blank-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "plan_nuevo", "Plan nuevo pendiente", "mdi:calendar-star")

    def _upcoming(self) -> dict | None:
        plans = self._data.get("plans")
        today = dt_util.now().date()
        future = []
        for plan in plans if isinstance(plans, list) else []:
            start = plan_start_date(plan) if isinstance(plan, dict) else None
            if start is not None and start > today:
                future.append((start, plan))
        return min(future, key=lambda item: item[0]) if future else None

    @property
    def is_on(self) -> bool:
        return self._upcoming() is not None

    @property
    def extra_state_attributes(self) -> dict:
        upcoming = self._upcoming()
        if not upcoming:
            return {}
        return {"inicio": upcoming[0].isoformat(), "titulo": upcoming[1].get("title"), "plan_id": upcoming[1].get("id")}


class MeasurementDueBinary(_DietoProBinary):
    """On when the last body measurement is older than a week."""

    _icon_on = "mdi:scale-balance"
    _icon_off = "mdi:scale-bathroom"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "medicion_pendiente", "Medición pendiente", "mdi:scale-bathroom")

    def _days(self) -> int | None:
        history = [h for h in seguimiento_history(self._data) if h.get("fecha")]
        return (dt_util.now() - history[-1]["fecha"]).days if history else None

    @property
    def is_on(self) -> bool:
        days = self._days()
        return days is not None and days >= MEASUREMENT_DUE_DAYS

    @property
    def extra_state_attributes(self) -> dict:
        return {"dias_desde_ultima": self._days(), "umbral_dias": MEASUREMENT_DUE_DAYS}


class AppointmentUnconfirmedBinary(_DietoProBinary):
    """On while the next appointment still needs a confirmation (from you
    when your account requires it, or from the dietitian for a slot you asked for)."""

    _icon_on = "mdi:calendar-question"
    _icon_off = "mdi:calendar-check"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "cita_sin_confirmar", "Cita sin confirmar", "mdi:calendar-question")

    def _state(self) -> tuple[bool, bool]:
        wrapper = self._data.get("cita")
        wrapper = wrapper if isinstance(wrapper, dict) else {}
        cita = wrapper.get("cita") if isinstance(wrapper.get("cita"), dict) else None
        if cita is None:
            return False, False
        needs_me = bool(wrapper.get("citasConfirmedByPaciente")) and not cita.get("confirmedByPaciente")
        needs_dietista = bool(wrapper.get("pendingDietistaConfirmation")) or (
            bool(cita.get("askedByPaciente")) and not cita.get("confirmedByDietista")
        )
        return needs_me, needs_dietista

    @property
    def is_on(self) -> bool:
        return any(self._state())

    @property
    def extra_state_attributes(self) -> dict:
        needs_me, needs_dietista = self._state()
        return {"falta_tu_confirmacion": needs_me, "falta_confirmacion_dietista": needs_dietista}
