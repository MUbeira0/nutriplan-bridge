"""Calendar entities: the meals of the current plan and the next appointment."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import DietoProDataUpdateCoordinator, dieta_index_for, plan_start_date
from .sensor import CITA_DATE_KEYS, _first_present, _parse_datetime, _scan_cita_date, franja_labels, parse_hora, shape_meals

MEAL_DURATION = timedelta(minutes=30)
APPOINTMENT_DURATION = timedelta(hours=1)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: DietoProDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DietoProMealsCalendar(coordinator, entry), DietoProAppointmentCalendar(coordinator, entry)])


class _DietoProCalendar(CoordinatorEntity[DietoProDataUpdateCoordinator], CalendarEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: DietoProDataUpdateCoordinator, entry: ConfigEntry, key: str, name: str, icon: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon
        email = entry.data.get(CONF_EMAIL, "Paciente")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Nutriplan Bridge ({email})",
            manufacturer="Nutriplan Bridge",
            model=email,
            entry_type="service",
        )


class DietoProMealsCalendar(_DietoProCalendar):
    """One event per meal ("Comida: Lasaña + ...") at its scheduled hour."""

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "calendario_comidas", "Comidas", "mdi:silverware-fork-knife")

    def _events_between(self, start: date, end: date) -> list[CalendarEvent]:
        data = self.coordinator.data or {}
        plan = data.get("plan_detail") if isinstance(data.get("plan_detail"), dict) else {}
        dietas = plan.get("dietas") if isinstance(plan.get("dietas"), list) else []
        if not dietas:
            return []

        today = dt_util.now().date()
        pstart = plan_start_date(plan)
        span_start = (pstart or today) - timedelta(days=(pstart or today).weekday())
        # Only the plan's own span (plus always the current week): showing the
        # same plan repeated forever would be wrong once a new one is issued.
        today_week_end = today - timedelta(days=today.weekday()) + timedelta(days=7)
        span_end = max(span_start + timedelta(days=max(len(dietas), 7)), today_week_end)

        labels = franja_labels(data)
        events: list[CalendarEvent] = []
        day = max(start, span_start)
        last = min(end, span_end)
        while day < last:
            index = dieta_index_for(day, len(dietas), pstart)
            for meal in shape_meals(dietas[index]) if index is not None else []:
                hora = parse_hora(meal.get("hora"))
                if hora is None:
                    continue
                begin = datetime.combine(day, hora, tzinfo=dt_util.DEFAULT_TIME_ZONE)
                platos = meal.get("platos") or []
                names = " + ".join(p.get("nombre") or "?" for p in platos)
                kcal = sum(p.get("calorias") or 0 for p in platos)
                events.append(
                    CalendarEvent(
                        start=begin,
                        end=begin + MEAL_DURATION,
                        summary=f"{labels.get(meal['franja'], meal['franja'])}: {names}",
                        description=f"{kcal} kcal" if kcal else None,
                    )
                )
            day += timedelta(days=1)
        return events

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        today = now.date()
        for ev in sorted(self._events_between(today, today + timedelta(days=2)), key=lambda e: e.start):
            if ev.end > now:
                return ev
        return None

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        local_start = dt_util.as_local(start_date).date()
        local_end = dt_util.as_local(end_date).date() + timedelta(days=1)
        return self._events_between(local_start, local_end)


class DietoProAppointmentCalendar(_DietoProCalendar):
    """The next appointment with the dietitian."""

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "calendario_citas", "Citas", "mdi:calendar-clock")

    def _appointment(self) -> CalendarEvent | None:
        wrapper = (self.coordinator.data or {}).get("cita")
        cita = wrapper.get("cita") if isinstance(wrapper, dict) else None
        if not isinstance(cita, dict):
            return None
        when = _parse_datetime(_first_present(cita, CITA_DATE_KEYS)) or _scan_cita_date(cita)
        if when is None:
            return None
        local = dt_util.as_local(when)
        if local.hour == 0 and local.minute == 0:
            # Date only, no time of day: an all-day event.
            return CalendarEvent(start=local.date(), end=local.date() + timedelta(days=1), summary="Cita con el dietista")
        return CalendarEvent(start=local, end=local + APPOINTMENT_DURATION, summary="Cita con el dietista")

    @property
    def event(self) -> CalendarEvent | None:
        ev = self._appointment()
        if ev is None:
            return None
        end = ev.end if isinstance(ev.end, datetime) else dt_util.start_of_local_day(ev.end)
        return ev if end > dt_util.now() else None

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        ev = self._appointment()
        if ev is None:
            return []
        begin = ev.start if isinstance(ev.start, datetime) else dt_util.start_of_local_day(ev.start)
        return [ev] if start_date <= begin < end_date else []
