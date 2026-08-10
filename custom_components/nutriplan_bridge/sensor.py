"""Sensors for DietoPro: current plan, today's meals, next appointment and seguimientos.

Field names used below (sentByEmailAt, nombreHorario, the 9 meal-slot keys,
peso/imc/pesoGrasa/...) are not guessed: they come from decompiling the app's
own compiled bundle with hermes-dec (see api.py docstring for the full
breakdown). The one thing that could NOT be confirmed without a live account
is the exact date field inside a "cita" (appointment) object, so that one
still uses a defensive multi-candidate lookup and always exposes the raw
payload as an attribute.
"""
from __future__ import annotations

import logging
import unicodedata
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import BASE_URL, DOMAIN
from .coordinator import DietoProDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# The 9 meal-slot keys a "dieta" (one day/schedule of a plan) can carry,
# confirmed from the decompiled transformDieta() function.
MEAL_SLOT_KEYS = (
    "desayuno",
    "tentempie1",
    "tentempie2",
    "comida",
    "merienda1",
    "merienda2",
    "cena",
    "recena1",
    "recena2",
)

WEEKDAYS_ES = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")

# The "cita" object's own date field was never confirmed against a real
# response (no test account available) - this is the only best-effort part left.
CITA_DATE_KEYS = ("fecha", "fechaHora", "fechaCita", "datetime", "date", "inicio")


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _first_present(data: Any, keys: tuple[str, ...]) -> Any:
    if not isinstance(data, dict):
        return None
    for key in keys:
        if data.get(key) not in (None, ""):
            return data[key]
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return dt_util.utc_from_timestamp(value)
        except (ValueError, OSError):
            return None
    if isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        if parsed is None:
            parsed_date = dt_util.parse_date(value)
            if parsed_date is not None:
                parsed = datetime.combine(parsed_date, datetime.min.time())
        if parsed is not None and parsed.tzinfo is None:
            parsed = dt_util.as_local(parsed)
        return parsed
    return None


def _meal_detail(slot: str, plato: Any) -> dict:
    """Expand a raw meal-slot ("ingesta") object into plato/receta/ingredientes.

    Confirmed by decompiling the app's PlatoDetail screen component
    (/(app)/planes/plato.tsx): each meal slot object carries
    "alimentoCantidades" (ingredient list: alimento.nombre + cantidad +
    medidaCasera) and "superPlato" (nombre, receta, comensales, rating,
    thumbnail - a relative path made absolute via BASE_URL + thumbnail).
    """
    if not isinstance(plato, dict):
        return {"franja": slot}

    super_plato = plato.get("superPlato") if isinstance(plato.get("superPlato"), dict) else {}
    ingredientes = []
    for item in plato.get("alimentoCantidades") or []:
        if not isinstance(item, dict):
            continue
        alimento = item.get("alimento") if isinstance(item.get("alimento"), dict) else {}
        ingredientes.append(
            {
                "nombre": alimento.get("nombre"),
                "cantidad": item.get("cantidad"),
                "medida_casera": item.get("medidaCasera"),
            }
        )

    thumbnail = super_plato.get("thumbnail")
    return {
        "franja": slot,
        "plato": super_plato.get("nombre"),
        "receta": super_plato.get("receta"),
        "comensales": super_plato.get("comensales"),
        "rating": super_plato.get("rating"),
        "imagen": f"{BASE_URL}{thumbnail}" if thumbnail else None,
        "ingredientes": ingredientes,
    }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: DietoProDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            DietoProCurrentPlanSensor(coordinator, entry),
            DietoProTodayMealsSensor(coordinator, entry),
            DietoProNextAppointmentSensor(coordinator, entry),
            DietoProSeguimientosSensor(coordinator, entry),
            DietoProDietistaSensor(coordinator, entry),
        ]
    )


class DietoProEntity(CoordinatorEntity[DietoProDataUpdateCoordinator], SensorEntity):
    """Base entity: groups everything under one 'DietoPro' device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DietoProDataUpdateCoordinator, entry: ConfigEntry, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Nutriplan Bridge",
            manufacturer="Nutriplan Bridge",
            model=entry.data.get(CONF_EMAIL, "Paciente"),
            entry_type="service",
        )


class DietoProCurrentPlanSensor(DietoProEntity):
    _attr_icon = "mdi:food-apple"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "plan_actual", "Plan actual")

    def _plan(self) -> dict | None:
        detail = (self.coordinator.data or {}).get("plan_detail")
        return detail if isinstance(detail, dict) else None

    @property
    def native_value(self) -> str | None:
        plan = self._plan()
        if not plan:
            return None
        title = plan.get("title")
        if title:
            return title
        created = _parse_datetime(plan.get("created"))
        return created.strftime("%d-%m-%Y") if created else None

    @property
    def extra_state_attributes(self) -> dict:
        plan = self._plan() or {}
        plans = (self.coordinator.data or {}).get("plans") or []
        return {
            "plan_id": plan.get("id"),
            "creado": plan.get("created"),
            "enviado_por_email": plan.get("sentByEmailAt"),
            "total_planes": len(plans) if isinstance(plans, list) else 0,
            "num_dietas": len(plan.get("dietas") or []) if plan.get("dietas") else 0,
            "raw": plan,
        }


class DietoProTodayMealsSensor(DietoProEntity):
    """Meal slots (desayuno/comida/cena/...) scheduled for today's weekday in the current plan."""

    _attr_icon = "mdi:silverware-fork-knife"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "comidas_hoy", "Comidas de hoy")

    def _dietas(self) -> list[dict]:
        plan = (self.coordinator.data or {}).get("plan_detail")
        dietas = plan.get("dietas") if isinstance(plan, dict) else None
        return dietas if isinstance(dietas, list) else []

    def _todays_dieta(self) -> dict | None:
        dietas = self._dietas()
        if not dietas:
            return None
        if len(dietas) == 1:
            return dietas[0]
        today_name = WEEKDAYS_ES[dt_util.now().weekday()]
        for dieta in dietas:
            nombre = _strip_accents(str(dieta.get("nombreHorario") or "")).lower()
            if today_name in nombre:
                return dieta
        return dietas[0]

    def _meals(self) -> list[dict]:
        dieta = self._todays_dieta()
        if not isinstance(dieta, dict):
            return []
        return [_meal_detail(slot, dieta[slot]) for slot in MEAL_SLOT_KEYS if dieta.get(slot)]

    @property
    def native_value(self) -> int:
        return len(self._meals())

    @property
    def extra_state_attributes(self) -> dict:
        dieta = self._todays_dieta() or {}
        meals = self._meals()
        return {
            "horario": dieta.get("nombreHorario"),
            "franjas": [m["franja"] for m in meals],
            "comidas": meals,
            "raw": dieta,
        }


class DietoProNextAppointmentSensor(DietoProEntity):
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "proxima_cita", "Próxima cita")

    def _cita(self) -> dict | None:
        # GET /api/paciente/cita returns {"cita": {...} | null} - confirmed from source.
        wrapper = (self.coordinator.data or {}).get("cita")
        cita = wrapper.get("cita") if isinstance(wrapper, dict) else None
        return cita if isinstance(cita, dict) else None

    @property
    def native_value(self) -> str | None:
        cita = self._cita()
        if not cita:
            return None
        when = _parse_datetime(_first_present(cita, CITA_DATE_KEYS))
        return when.isoformat() if when else "programada"

    @property
    def extra_state_attributes(self) -> dict:
        return {"raw": self._cita()}


class DietoProSeguimientosSensor(DietoProEntity):
    """Body-composition tracking entries (peso, imc, pesoGrasa, ...)."""

    _attr_icon = "mdi:chart-line"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "seguimientos", "Seguimientos")

    def _seguimientos(self) -> list[dict]:
        data = (self.coordinator.data or {}).get("seguimientos")
        return data if isinstance(data, list) else []

    def _latest(self) -> dict | None:
        items = [s for s in self._seguimientos() if isinstance(s, dict) and s.get("createdTimestamp")]
        if not items:
            return None
        items.sort(key=lambda s: float(s["createdTimestamp"]), reverse=True)
        return items[0]

    @property
    def native_value(self) -> int:
        return len(self._seguimientos())

    @property
    def extra_state_attributes(self) -> dict:
        latest = self._latest() or {}
        return {
            "ultimo_peso": latest.get("peso"),
            "ultimo_imc": latest.get("imc"),
            "ultimo_peso_grasa": latest.get("pesoGrasa"),
            "ultimo_porcentaje_grasa": latest.get("porcentajeGrasa"),
            "ultimo_peso_masa_magra": latest.get("pesoMasaMagra"),
            "ultimo_peso_agua": latest.get("pesoAgua"),
            "ultimo_perimetro_cintura": latest.get("perimetroCintura"),
            "ultimo_perimetro_cadera": latest.get("perimetroCadera"),
            "fecha_ultimo": _parse_datetime(latest.get("createdTimestamp")),
            "raw": self._seguimientos(),
        }


class DietoProDietistaSensor(DietoProEntity):
    _attr_icon = "mdi:account-heart"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "dietista", "Dietista")

    def _dietista(self) -> dict | None:
        data = (self.coordinator.data or {}).get("dietista")
        return data if isinstance(data, dict) else None

    @property
    def native_value(self) -> str | None:
        dietista = self._dietista()
        if not dietista:
            return None
        return _first_present(dietista, ("nombre", "nombreCompleto", "name")) or "Sin asignar"

    @property
    def extra_state_attributes(self) -> dict:
        return {"raw": self._dietista()}
