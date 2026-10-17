"""Sensors for DietoPro: current plan, today's meals (with recipes), next
appointment and seguimientos.

Field names used below (sentByEmailAt, nombreHorario, the 9 meal-slot keys,
subingestas/plato/superPlato/receta/alergenos, peso/imc/pesoGrasa/...) are
not guessed: the meal-slot layout was confirmed by decompiling the app's own
compiled bundle with hermes-dec (see api.py docstring), and the
subingestas -> plato -> superPlato nesting plus exact field names were
additionally verified against a real authenticated API response. The one
thing that could NOT be confirmed against a real response is the exact date
field inside a "cita" (appointment) object, so that one still uses a
defensive multi-candidate lookup and always exposes the raw payload as an
attribute.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, UnitOfMass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import BASE_URL, DOMAIN
from .coordinator import DietoProDataUpdateCoordinator, dieta_index_for, plan_start_date

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

# Every one of these keys was present on a real "plato" object (confirmed
# against a real API response, not guessed). Units follow the standard
# Spanish food-composition-table convention (BEDCA-style: mg/mcg/g) - the
# API itself never states units explicitly, so that part is a reasonable
# but unconfirmed assumption; the raw values are always in "raw" too.
NUTRIENT_KEY_MAP = {
    "energia_kcal": "energia",
    "energia_kcal_100g": "vet100",
    "alcohol_g": "etanol",
    "proteinas_g": "proteinasTotales",
    "proteinas_animales_g": "proteinasAnimales",
    "grasas_g": "grasasTotales",
    "grasas_saturadas_g": "ags",
    "grasas_monoinsaturadas_g": "agmi",
    "grasas_poliinsaturadas_g": "agpi",
    "colesterol_mg": "colesterol",
    "carbohidratos_g": "glucidosTotales",
    "almidon_g": "polisacaridos",
    "azucares_g": "azucares",
    "azucares_anadidos_g": "azucaresAnadidos",
    "fibra_g": "fibra",
    "sodio_mg": "sodio",
    "potasio_mg": "potasio",
    "calcio_mg": "calcio",
    "hierro_mg": "hierro",
    "magnesio_mg": "magnesio",
    "fosforo_mg": "fosforo",
    "yodo_mcg": "iodo",
    "vitamina_a_mcg": "vitA",
    "vitamina_c_mg": "vitC",
    "vitamina_d_mcg": "vitD",
    "vitamina_e_mg": "vitE",
    "vitamina_b1_mg": "vitB1",
    "vitamina_b2_mg": "vitB2",
    "vitamina_b6_mg": "vitB6",
    "vitamina_b12_mcg": "vitB12",
    "folato_mcg": "vitFolato",
    "niacina_mg": "vitNiacina",
}


def _nutrients(plato: dict) -> dict:
    return {out_key: plato.get(src_key) for out_key, src_key in NUTRIENT_KEY_MAP.items()}


# "energia_kcal_100g" is a density (kcal per 100g), not a total - summing it
# across several dishes would produce a meaningless number, so it's excluded
# from the daily aggregate even though it's shown per-dish.
_SUMMABLE_NUTRIENT_KEYS = [k for k in NUTRIENT_KEY_MAP if k != "energia_kcal_100g"]


def _sum_nutrients(nutrient_dicts: list[dict]) -> dict:
    totals: dict[str, float] = {key: 0.0 for key in _SUMMABLE_NUTRIENT_KEYS}
    seen = False
    for nutrients in nutrient_dicts:
        for key in _SUMMABLE_NUTRIENT_KEYS:
            value = nutrients.get(key)
            if isinstance(value, (int, float)):
                totals[key] += value
                seen = True
    return {key: round(value, 2) for key, value in totals.items()} if seen else {}

# The "cita" object's own date field was never confirmed against a real
# response (no test account available) - this is the only best-effort part left.
CITA_DATE_KEYS = ("fecha", "fechaHora", "fechaCita", "datetime", "date", "inicio")


def _first_present(data: Any, keys: tuple[str, ...]) -> Any:
    if not isinstance(data, dict):
        return None
    for key in keys:
        if data.get(key) not in (None, ""):
            return data[key]
    return None


def _scan_cita_date(cita: dict) -> datetime | None:
    """The date field inside "cita" was never confirmed, so when none of
    CITA_DATE_KEYS matches, take any key that looks date-like (fecha*, date*,
    inicio, start, hora) whose value actually parses, instead of showing
    "programada" with no date."""
    for key, value in cita.items():
        if isinstance(value, (str, int, float)) and any(
            hint in key.lower() for hint in ("fecha", "date", "inicio", "start", "hora", "timestamp")
        ):
            parsed = _parse_datetime(value)
            if parsed is not None:
                return parsed
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        # createdTimestamp arrives as a numeric string (e.g. "1770753900"),
        # not a real number - without this it fell through to the ISO-string
        # branch below, which parse_datetime()/parse_date() can't parse,
        # silently producing "unknown" instead of a date.
        value = float(value)
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


def _plato_name(sub: dict, plato: dict, super_plato: dict, ingredientes: list[dict]) -> tuple[str | None, bool]:
    """(name, derived). A recipe-style plato names itself via superPlato, but a
    simple food added on its own (e.g. a chicken breast) has no superPlato, so
    it showed up as "Sin especificar". Fall back to the plato's / subingesta's
    own name, then to the ingredient(s): one ingredient -> "Pechuga de pollo
    (150 g)", several -> "A + B + C"."""
    for source in (super_plato, plato, sub):
        for key in ("nombre", "name", "titulo"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip(), False

    named = [i for i in ingredientes if isinstance(i.get("nombre"), str) and i["nombre"].strip()]
    if not named:
        return None, False
    if len(named) == 1:
        item = named[0]
        name = item["nombre"].strip()
        name = name[0].upper() + name[1:]
        quantity = item.get("cantidad")
        if isinstance(quantity, (int, float)) and quantity > 0:
            name = f"{name} ({quantity:g} g)"
        return name, True
    names = []
    for item in named[:3]:
        text = item["nombre"].strip()
        names.append(text[0].upper() + text[1:] if not names else text)
    return " + ".join(names) + (" + …" if len(named) > 3 else ""), True


def _loose_item_detail(sub: dict, kind: str, item: dict) -> dict:
    """A subingesta that is a single food (alimentoCantidad) or supplement
    (suplementoCantidad) instead of a plato: shaped like a plato so the card
    and the shopping list treat it the same way."""
    inner = item.get("alimento") if kind == "alimento" else item.get("suplemento")
    inner = inner if isinstance(inner, dict) else {}
    grupo = inner.get("grupoAlimento") if isinstance(inner.get("grupoAlimento"), dict) else {}
    super_grupo = grupo.get("superGrupoAlimento") if isinstance(grupo.get("superGrupoAlimento"), dict) else {}
    if not super_grupo and isinstance(inner.get("superGrupoAlimento"), dict):
        super_grupo = inner["superGrupoAlimento"]
    nombre_item = inner.get("nombre") or item.get("nombre")
    ingrediente = {
        "nombre": nombre_item,
        "cantidad": item.get("cantidad"),
        "medida_casera": item.get("medidaCasera"),
        "grupo": grupo.get("nombre"),
        "categoria": super_grupo.get("nombre"),
    }
    nombre, _ = _plato_name(sub, {}, {}, [ingrediente])
    energia = item.get("energia")
    nutrientes = _nutrients(item)
    estimadas = False
    cantidad = item.get("cantidad")
    per_100 = inner.get("energia")
    if (
        not isinstance(energia, (int, float))
        and isinstance(per_100, (int, float))
        and isinstance(cantidad, (int, float))
        and cantidad > 0
    ):
        # The food record only carries its composition (food tables are per 100 g),
        # so scale it by the quantity. Marked as an estimate in the card.
        factor = cantidad / 100
        nutrientes = {
            key: round(value * factor, 2) if isinstance(value, (int, float)) else value
            for key, value in _nutrients(inner).items()
        }
        nutrientes["energia_kcal_100g"] = per_100
        energia = per_100 * factor
        estimadas = True
    return {
        "subingesta_id": sub.get("id"),
        "plato_id": None,
        "super_plato_id": None,
        "tipo": kind,
        "alimento_id": inner.get("id") if kind == "alimento" else None,
        "cantidad": item.get("cantidad"),
        "nombre": nombre,
        "nombre_derivado": True,
        "receta": None,
        "notas_raciones": item.get("observaciones"),
        "comensales": None,
        "duracion_min": None,
        "rating": None,
        "calorias": round(energia) if isinstance(energia, (int, float)) else energia,
        "calorias_estimadas": estimadas,
        "talla": None,
        "alergenos": [],
        "imagen": None,
        "ingredientes": [ingrediente] if nombre_item else [],
        "nutrientes": nutrientes,
        "modificado_por_paciente": sub.get("changedByPaciente"),
    }


def _plato_detail(sub: Any) -> dict:
    """Expand one raw "subingesta" object into name/receta/ingredientes/allergens.

    Confirmed against a real (user-supplied) API response: a meal slot
    ("ingesta", e.g. dieta.desayuno) carries a "subingestas" array; each
    subingesta has an "id" (needed to swap this dish - see
    api.async_change_plato) and a "plato" object with "id" (this specific
    size/talla variant), "alimentoCantidades" (ingredient list:
    alimento.nombre + cantidad + medidaCasera), "energia" (kcal for that
    serving), "alergenos", and "superPlato" (nombre, receta - can be null
    for simple items like a piece of fruit -, comensales, duracion, rating,
    imagePath/thumbnail - relative paths made absolute via BASE_URL).
    """
    if not isinstance(sub, dict):
        return {}
    if not isinstance(sub.get("plato"), dict):
        # The app's own model (traced in its code): a subingesta is a plato, OR a
        # lone food with its quantity (alimentoCantidad), OR a supplement
        # (suplementoCantidad). Only the first was handled, so simple foods like
        # a chicken breast came out as "Sin especificar".
        if isinstance(sub.get("alimentoCantidad"), dict):
            return _loose_item_detail(sub, "alimento", sub["alimentoCantidad"])
        if isinstance(sub.get("suplementoCantidad"), dict):
            return _loose_item_detail(sub, "suplemento", sub["suplementoCantidad"])
    plato = sub.get("plato") if isinstance(sub.get("plato"), dict) else {}

    super_plato = plato.get("superPlato") if isinstance(plato.get("superPlato"), dict) else {}
    ingredientes = []
    for item in plato.get("alimentoCantidades") or []:
        if not isinstance(item, dict):
            continue
        alimento = item.get("alimento") if isinstance(item.get("alimento"), dict) else {}
        grupo = alimento.get("grupoAlimento") if isinstance(alimento.get("grupoAlimento"), dict) else {}
        super_grupo = (
            alimento.get("superGrupoAlimento") if isinstance(alimento.get("superGrupoAlimento"), dict) else {}
        )
        ingredientes.append(
            {
                "nombre": alimento.get("nombre"),
                "cantidad": item.get("cantidad"),
                "medida_casera": item.get("medidaCasera"),
                "grupo": grupo.get("nombre"),
                "categoria": super_grupo.get("nombre"),
            }
        )

    nombre, nombre_derivado = _plato_name(sub, plato, super_plato, ingredientes)
    talla_plato = plato.get("tallaPlato") if isinstance(plato.get("tallaPlato"), dict) else {}
    image_path = super_plato.get("imagePath") or super_plato.get("thumbnail")
    energia = plato.get("energia")
    return {
        "subingesta_id": sub.get("id"),
        "plato_id": plato.get("id"),
        "super_plato_id": super_plato.get("id"),
        "tipo": "plato",
        "nombre": nombre,
        "nombre_derivado": nombre_derivado,
        "modificado_por_paciente": sub.get("changedByPaciente"),
        "receta": super_plato.get("receta"),
        "notas_raciones": super_plato.get("observaciones"),
        "comensales": super_plato.get("comensales"),
        "duracion_min": super_plato.get("duracion"),
        "rating": super_plato.get("rating"),
        "calorias": round(energia) if isinstance(energia, (int, float)) else energia,
        "talla": talla_plato.get("talla"),
        "alergenos": plato.get("alergenos") or [],
        "imagen": f"{BASE_URL}{image_path}" if image_path else None,
        "ingredientes": ingredientes,
        "nutrientes": _nutrients(plato),
    }


def _meal_detail(slot: str, ingesta: Any) -> dict:
    """Expand a raw meal-slot ("ingesta") object into its list of platos.

    A meal slot can carry more than one dish (e.g. desayuno = coffee +
    sandwich), one per "subingesta".
    """
    if not isinstance(ingesta, dict):
        return {"franja": slot}

    platos = []
    for sub in ingesta.get("subingestas") or []:
        if not isinstance(sub, dict):
            continue
        platos.append(_plato_detail(sub))

    return {
        "franja": slot,
        "hora": ingesta.get("hora"),
        "platos": platos,
    }


def build_device_info(entry: ConfigEntry) -> DeviceInfo:
    email = entry.data.get(CONF_EMAIL, "Paciente")
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Nutriplan Bridge ({email})",
        manufacturer="Nutriplan Bridge",
        model=email,
        entry_type="service",
    )


def seguimiento_history(data: Any) -> list[dict]:
    """Every body measurement, cleaned and sorted oldest -> newest."""
    items = data.get("seguimientos") if isinstance(data, dict) else None
    items = [s for s in items if isinstance(s, dict) and s.get("createdTimestamp")] if isinstance(items, list) else []
    try:
        items.sort(key=lambda s: float(s["createdTimestamp"]))
    except (TypeError, ValueError):
        return []
    return [_clean_seguimiento(i) for i in items]


def shape_meals(dieta: Any) -> list[dict]:
    """All meal slots of one raw "dieta" (one day/schedule), shaped for the card."""
    if not isinstance(dieta, dict):
        return []
    return [_meal_detail(slot, dieta[slot]) for slot in MEAL_SLOT_KEYS if dieta.get(slot)]


def shape_day(index: int, dieta: Any) -> dict:
    """One day of a plan: its meals plus the nutritional total of the day."""
    meals = shape_meals(dieta)
    platos = [p for m in meals for p in (m.get("platos") or [])]
    return {
        "dieta_index": index,
        "horario": dieta.get("nombreHorario") if isinstance(dieta, dict) else None,
        "comidas": meals,
        "total_platos": len(platos),
        "resumen_nutricional": _sum_nutrients([p.get("nutrientes") or {} for p in platos]),
    }


def plan_summaries(plans: Any) -> list[dict]:
    """Compact list of every plan the patient has (newest start date first)
    so the card can offer going back to previous plans."""
    if not isinstance(plans, list):
        return []
    out = []
    for p in plans:
        if not isinstance(p, dict) or p.get("id") is None:
            continue
        start = plan_start_date(p)
        out.append(
            {
                "id": p.get("id"),
                "titulo": p.get("title"),
                "creado": p.get("created"),
                "enviado": p.get("sentByEmailAt"),
                "inicio": start.isoformat() if start else None,
            }
        )
    out.sort(key=lambda p: (p.get("inicio") or "", str(p.get("enviado") or "")), reverse=True)
    return out


DEFAULT_FRANJA_LABELS = {
    "desayuno": "Desayuno",
    "tentempie1": "Tentempié",
    "tentempie2": "Tentempié",
    "comida": "Comida",
    "merienda1": "Merienda",
    "merienda2": "Merienda",
    "cena": "Cena",
    "recena1": "Recena",
    "recena2": "Recena",
}


def franja_labels(data: Any) -> dict[str, str]:
    """Meal-slot labels: the dietitian's own names when sent, else defaults."""
    labels = dict(DEFAULT_FRANJA_LABELS)
    dietista = data.get("dietista") if isinstance(data, dict) else None
    items = dietista.get("nombreIngestas") if isinstance(dietista, dict) else None
    for item in items or []:
        if isinstance(item, dict) and item.get("ingestaType") and item.get("ingestaName"):
            labels[item["ingestaType"]] = item["ingestaName"]
    return labels


def parse_hora(value: Any) -> time | None:
    """Meal "hora" -> time of day ("07:00", "07:00:00" or a full ISO datetime)."""
    if not value:
        return None
    text = str(value)
    parsed = dt_util.parse_time(text)
    if parsed is not None:
        return parsed
    dt = dt_util.parse_datetime(text)
    return dt.time() if dt is not None else None


def shopping_list(days: list[dict]) -> list[dict]:
    """Aggregate every ingredient of the given shaped days (see shape_day)
    into one shopping list: summed grams per food, grouped by category."""
    totals: dict[str, dict] = {}
    for day in days:
        for meal in day.get("comidas") or []:
            for plato in meal.get("platos") or []:
                for ing in plato.get("ingredientes") or []:
                    nombre = (ing.get("nombre") or "").strip()
                    if not nombre:
                        continue
                    try:
                        cantidad = float(ing.get("cantidad") or 0)
                    except (TypeError, ValueError):
                        cantidad = 0.0
                    item = totals.setdefault(
                        nombre.lower(),
                        {
                            "nombre": nombre,
                            "cantidad_g": 0.0,
                            "veces": 0,
                            "grupo": ing.get("grupo"),
                            "categoria": ing.get("categoria"),
                        },
                    )
                    item["cantidad_g"] += cantidad
                    item["veces"] += 1
    items = list(totals.values())
    for item in items:
        item["cantidad_g"] = round(item["cantidad_g"])
    items.sort(key=lambda i: (i.get("categoria") or "~", i["nombre"].lower()))
    return items


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: DietoProDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            DietoProCurrentPlanSensor(coordinator, entry),
            DietoProTodayMealsSensor(coordinator, entry),
            DietoProNextAppointmentSensor(coordinator, entry),
            DietoProSeguimientosSensor(coordinator, entry),
            DietoProDietistaSensor(coordinator, entry),
            DietoProChatSensor(coordinator, entry),
            DietoProPerfilSensor(coordinator, entry),
            DietoProEniSensor(coordinator, entry),
            DietoProValoracionesSensor(coordinator, entry),
            DietoProCharlasSensor(coordinator, entry),
            DietoProTodayEnergySensor(coordinator, entry),
            DietoProEatenSensor(coordinator, entry),
            DietoProNextMealSensor(coordinator, entry),
            *(
                DietoProMetricSensor(coordinator, entry, *spec)
                for spec in METRIC_SENSORS
            ),
            *(DietoProTodayNutrientSensor(coordinator, entry, *spec) for spec in NUTRIENT_SENSORS),
            *(DietoProDerivedSensor(coordinator, entry, *spec) for spec in DERIVED_SENSORS),
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
        email = entry.data.get(CONF_EMAIL, "Paciente")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            # Every account used to get the literal same device name
            # ("Nutriplan Bridge"), so with more than one account configured
            # HA disambiguated their entity_ids with numeric suffixes
            # (..._comidas_hoy vs ..._comidas_hoy_2) - the card's
            # entity-by-suffix auto-detection could then only ever find one
            # of them. Including the email makes every device (and its
            # entity_ids) unique from the start.
            name=f"Nutriplan Bridge ({email})",
            manufacturer="Nutriplan Bridge",
            model=email,
            entry_type="service",
        )


class DietoProCurrentPlanSensor(DietoProEntity):
    _attr_icon = "mdi:food-apple"
    _unrecorded_attributes = frozenset({"raw", "planes"})

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
            "actualizado": plan.get("updated"),
            "modificado_por_paciente": plan.get("updatedByPaciente"),
            "inicio": (plan_start_date(plan).isoformat() if plan and plan_start_date(plan) else None),
            "total_planes": len(plans) if isinstance(plans, list) else 0,
            "planes": plan_summaries(plans),
            "num_dietas": len(plan.get("dietas") or []) if plan.get("dietas") else 0,
            "raw": plan,
        }


class DietoProTodayMealsSensor(DietoProEntity):
    """Meal slots (desayuno/comida/cena/...) scheduled for today's weekday in the current plan."""

    # "comidas" (every dish with recipe, ingredients and ~30 nutrients) plus
    # "raw" is far beyond the recorder's 16 KB attribute limit - HA logs a
    # warning on every update and refuses to store the state. The card reads
    # the live state, so there's no reason to push these to the database.
    _unrecorded_attributes = frozenset({"comidas", "resumen_nutricional", "raw"})

    _attr_icon = "mdi:silverware-fork-knife"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "comidas_hoy", "Comidas de hoy")

    def _dietas(self) -> list[dict]:
        plan = (self.coordinator.data or {}).get("plan_detail")
        dietas = plan.get("dietas") if isinstance(plan, dict) else None
        return dietas if isinstance(dietas, list) else []

    def _todays_dieta_indexed(self) -> tuple[int, dict] | tuple[None, None]:
        """Returns (index-within-plan.dietas, dieta).

        This used to match today's weekday name against "nombreHorario", but
        that field turned out to NOT be a weekday at all - it's the work
        shift name (e.g. "Turno partido"), the same for every day on
        single-dieta accounts. That's why it never advanced past day 0 for
        anyone with more than one dieta.

        Tracing the real cambiar_plato mutation confirmed the "dieta" field
        it takes is a 0-6 weekday index (Monday=0), so that's what dieta
        selection uses now too - the same index doubles as what the action
        needs, no separate lookup required. len(dietas) % 7 acts as a safety
        net in case an account ever has fewer than 7 entries.
        """
        dietas = self._dietas()
        if not dietas:
            return None, None
        plan = (self.coordinator.data or {}).get("plan_detail")
        start = plan_start_date(plan) if isinstance(plan, dict) else None
        index = dieta_index_for(dt_util.now().date(), len(dietas), start)
        return index, dietas[index]

    def _todays_dieta(self) -> dict | None:
        return self._todays_dieta_indexed()[1]

    def _meals(self) -> list[dict]:
        return shape_meals(self._todays_dieta())

    @property
    def native_value(self) -> int:
        return len(self._meals())

    @property
    def entity_picture(self) -> str | None:
        for meal in self._meals():
            for plato in meal.get("platos") or []:
                if plato.get("imagen"):
                    return plato["imagen"]
        return None

    @property
    def extra_state_attributes(self) -> dict:
        dieta_index, dieta = self._todays_dieta_indexed()
        dieta = dieta or {}
        plan = (self.coordinator.data or {}).get("plan_detail") or {}
        meals = self._meals()
        all_platos = [p for m in meals for p in (m.get("platos") or [])]
        return {
            "horario": dieta.get("nombreHorario"),
            "franjas": [m["franja"] for m in meals],
            "total_platos": len(all_platos),
            "resumen_nutricional": _sum_nutrients([p.get("nutrientes") or {} for p in all_platos]),
            "comidas": meals,
            "plan_id": plan.get("id"),
            "dieta_index": dieta_index,
            "num_dietas": len(self._dietas()),
            "config_entry_id": self._entry.entry_id,
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
        if when is None:
            when = _scan_cita_date(cita)
        return when.isoformat() if when else "programada"

    @property
    def extra_state_attributes(self) -> dict:
        cita = self._cita() or {}
        wrapper = (self.coordinator.data or {}).get("cita")
        wrapper = wrapper if isinstance(wrapper, dict) else {}
        deadline = wrapper.get("citaCancelationDeadline")
        return {
            "cita_id": cita.get("id"),
            "duracion_min": cita.get("duracion"),
            "observaciones": cita.get("observaciones") or None,
            "confirmada_por_dietista": cita.get("confirmedByDietista"),
            "confirmada_por_paciente": cita.get("confirmedByPaciente"),
            "pedida_por_paciente": cita.get("askedByPaciente"),
            # What this account is allowed to do (the buttons in the card follow these):
            "requiere_confirmacion_paciente": wrapper.get("citasConfirmedByPaciente"),
            "pendiente_confirmacion_dietista": wrapper.get("pendingDietistaConfirmation"),
            "reserva_habilitada": wrapper.get("citaBookingEnabled"),
            "reserva_max_dias": wrapper.get("citaBookingMaxAdvance"),
            "reserva_duracion_min": wrapper.get("citaBookingDuration"),
            "cancelacion_habilitada": (deadline not in (None, "disabled")) if "citaCancelationDeadline" in wrapper else None,
            "cancelacion_limite": deadline,
            "raw": cita or None,
        }

    _unrecorded_attributes = frozenset({"raw"})


def _clean_seguimiento(item: dict) -> dict:
    """Field names confirmed against a real 19-entry history. Two of the
    fields the decompiled transformSeguimientos() implied ("pesoMasaMagra",
    "pesoAgua") never actually appeared in that real data - the account
    tracks "porcentajeAgua" (a %) instead, and lean mass isn't sent at all.
    Both are derived here from peso/pesoGrasa/porcentajeAgua when possible,
    since the API already gives us everything needed to compute them; if the
    account ever DOES send them directly, the direct value wins.
    """
    peso = item.get("peso")
    peso_grasa = item.get("pesoGrasa")
    porcentaje_agua = item.get("porcentajeAgua")

    peso_masa_magra = item.get("pesoMasaMagra")
    if peso_masa_magra is None and isinstance(peso, (int, float)) and isinstance(peso_grasa, (int, float)):
        peso_masa_magra = round(peso - peso_grasa, 2)

    peso_agua = item.get("pesoAgua")
    if peso_agua is None and isinstance(peso, (int, float)) and isinstance(porcentaje_agua, (int, float)):
        peso_agua = round(peso * porcentaje_agua / 100, 2)

    return {
        "id": item.get("id"),
        "fecha": _parse_datetime(item.get("createdTimestamp")),
        "peso": peso,
        "imc": item.get("imc"),
        "peso_grasa": peso_grasa,
        "porcentaje_grasa": item.get("porcentajeGrasa"),
        "porcentaje_agua": porcentaje_agua,
        "peso_masa_magra": peso_masa_magra,
        "peso_agua": peso_agua,
        "perimetro_cintura": item.get("perimetroCintura"),
        "perimetro_cintura_umbilical": item.get("perimetroCinturaUmbilical"),
        "perimetro_cadera": item.get("perimetroCadera"),
        "observaciones": item.get("observaciones"),
    }


def _delta(curr: Any, prev: Any) -> float | None:
    if isinstance(curr, (int, float)) and isinstance(prev, (int, float)):
        return round(curr - prev, 2)
    return None


class DietoProSeguimientosSensor(DietoProEntity):
    """Body-composition tracking entries (peso, imc, pesoGrasa, ...)."""

    _attr_icon = "mdi:chart-line"
    _unrecorded_attributes = frozenset({"historial", "raw"})

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "seguimientos", "Seguimientos")

    def _seguimientos(self) -> list[dict]:
        data = (self.coordinator.data or {}).get("seguimientos")
        return data if isinstance(data, list) else []

    def _sorted_items(self) -> list[dict]:
        items = [s for s in self._seguimientos() if isinstance(s, dict) and s.get("createdTimestamp")]
        items.sort(key=lambda s: float(s["createdTimestamp"]))
        return items

    @property
    def native_value(self) -> int:
        return len(self._seguimientos())

    @property
    def extra_state_attributes(self) -> dict:
        items = self._sorted_items()
        historial = [_clean_seguimiento(s) for s in items]
        latest = historial[-1] if historial else {}
        previous = historial[-2] if len(historial) >= 2 else {}
        return {
            "ultimo_peso": latest.get("peso"),
            "ultimo_imc": latest.get("imc"),
            "ultimo_peso_grasa": latest.get("peso_grasa"),
            "ultimo_porcentaje_grasa": latest.get("porcentaje_grasa"),
            "ultimo_porcentaje_agua": latest.get("porcentaje_agua"),
            "ultimo_peso_masa_magra": latest.get("peso_masa_magra"),
            "ultimo_peso_agua": latest.get("peso_agua"),
            "ultimo_perimetro_cintura": latest.get("perimetro_cintura"),
            "ultimo_perimetro_cintura_umbilical": latest.get("perimetro_cintura_umbilical"),
            "ultimo_perimetro_cadera": latest.get("perimetro_cadera"),
            "ultimas_observaciones": latest.get("observaciones"),
            "fecha_ultimo": latest.get("fecha"),
            "delta_peso": _delta(latest.get("peso"), previous.get("peso")),
            "delta_imc": _delta(latest.get("imc"), previous.get("imc")),
            "delta_peso_grasa": _delta(latest.get("peso_grasa"), previous.get("peso_grasa")),
            "delta_porcentaje_grasa": _delta(latest.get("porcentaje_grasa"), previous.get("porcentaje_grasa")),
            "historial": historial,
            "raw": self._seguimientos(),
        }


class DietoProDietistaSensor(DietoProEntity):
    """The assigned dietitian/coach - GET /api/paciente/dietista.

    Real field names (verified against a real response, not guessed):
    firstName, familyName, email, mobilePhone, avatar (relative image path),
    numColegiacion, and "nombreIngestas" - a per-account list of
    {ingestaType, ingestaName} mapping each meal-slot key (e.g. "tentempie1")
    to the human label this particular dietista uses for it (e.g. "Media
    mañana"), which can differ between accounts/dietistas.
    """

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
        nombre = " ".join(
            part for part in (dietista.get("firstName"), dietista.get("familyName")) if part
        ).strip()
        return nombre or _first_present(dietista, ("nombre", "nombreCompleto", "name")) or "Sin asignar"

    @property
    def entity_picture(self) -> str | None:
        avatar = (self._dietista() or {}).get("avatar")
        return f"{BASE_URL}{avatar}" if avatar else None

    @property
    def extra_state_attributes(self) -> dict:
        dietista = self._dietista() or {}
        avatar = dietista.get("avatar")
        titulacion = dietista.get("titulacion")
        return {
            "email": dietista.get("email"),
            "telefono": dietista.get("mobilePhone"),
            "num_colegiacion": dietista.get("numColegiacion"),
            "chat_disponible": dietista.get("chatAvailable"),
            "idioma": dietista.get("locale"),
            "avatar": f"{BASE_URL}{avatar}" if avatar else None,
            "titulacion_pdf": f"{BASE_URL}{titulacion}" if titulacion else None,
            "nombres_franjas": {
                item.get("ingestaType"): item.get("ingestaName")
                for item in dietista.get("nombreIngestas") or []
                if isinstance(item, dict)
            },
            "raw": dietista,
        }


class DietoProChatSensor(DietoProEntity):
    """Unread chat messages with the dietista - GET /api/paciente/chat/unread + /chat/messages."""

    _attr_icon = "mdi:chat-processing"

    @property
    def icon(self) -> str:
        return "mdi:chat-processing" if self._unread_count() > 0 else "mdi:chat-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "mensajes_sin_leer", "Mensajes sin leer")

    def _unread_count(self) -> int:
        data = (self.coordinator.data or {}).get("chat_unread")
        if isinstance(data, (int, float)):
            return int(data)
        if isinstance(data, dict):
            value = _first_present(data, ("unread", "count", "total", "unreadCount"))
            if isinstance(value, (int, float)):
                return int(value)
        return 0

    @property
    def native_value(self) -> int:
        return self._unread_count()

    @property
    def extra_state_attributes(self) -> dict:
        messages = (self.coordinator.data or {}).get("chat_messages")
        return {"mensajes": messages, "raw_unread": (self.coordinator.data or {}).get("chat_unread")}


class DietoProPerfilSensor(DietoProEntity):
    """The patient's own profile - GET /api/paciente/current-user.

    Field names confirmed against a real response: "fullname" (a single
    combined field here, unlike dietista's separate firstName/familyName),
    "phone" (not "mobilePhone" like dietista uses), "dni", "address",
    "locale", "isDietista", "online", "hideSeguimientos".
    """

    _attr_icon = "mdi:account"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "mi_perfil", "Mi perfil")

    def _perfil(self) -> dict | None:
        data = (self.coordinator.data or {}).get("current_user")
        return data if isinstance(data, dict) else None

    @property
    def native_value(self) -> str | None:
        perfil = self._perfil()
        if not perfil:
            return None
        return _first_present(perfil, ("fullname", "nombre", "name")) or None

    @property
    def extra_state_attributes(self) -> dict:
        perfil = self._perfil() or {}
        return {
            "email": perfil.get("email"),
            "telefono": perfil.get("phone"),
            "dni": perfil.get("dni"),
            "direccion": perfil.get("address"),
            "idioma": perfil.get("locale"),
            "en_linea": perfil.get("online"),
            "es_paciente": perfil.get("paciente"),
            "es_dietista": perfil.get("isDietista"),
            "seguimientos_ocultos": perfil.get("hideSeguimientos"),
            "raw": perfil,
        }


def _name(value: Any) -> Any:
    """Many ENI fields are {"id": n, "nombre": "..."} option objects - pull the label."""
    return value.get("nombre") if isinstance(value, dict) else value


def _names(value: Any) -> list:
    if not isinstance(value, list):
        return []
    return [_name(item) for item in value if isinstance(item, dict)]


def _age_years(birthdate: Any) -> int | None:
    born = _parse_datetime(birthdate)
    if born is None:
        return None
    today = dt_util.now()
    years = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return years


class DietoProEniSensor(DietoProEntity):
    """Initial nutrition survey (onboarding wizard) - GET /api/paciente/eni.

    Confirmed against a real response: it's not a simple "% complete"
    tracker, it's the full survey answer sheet (objetivo, antropometria,
    alergias, farmacos, habitos...) plus the resulting energy/macro targets
    DietoPro computed from it. "lastViewedStep" reaching "eniFinish" is what
    marks it as completed - the boolean flags this sensor originally guessed
    at (completed/finished/isCompleted) don't exist.
    """

    _attr_icon = "mdi:clipboard-text-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "estado_eni", "Encuesta inicial")

    def _eni(self) -> dict | None:
        data = (self.coordinator.data or {}).get("eni")
        return data if isinstance(data, dict) else None

    @property
    def native_value(self) -> str:
        eni = self._eni()
        if eni is None:
            return "sin_iniciar"
        return "completada" if eni.get("lastViewedStep") == "eniFinish" else "en_progreso"

    @property
    def extra_state_attributes(self) -> dict:
        eni = self._eni()
        if not eni:
            return {"raw": None}

        altura_cm = eni.get("altura")
        peso = eni.get("peso")
        imc_calculado = None
        if isinstance(altura_cm, (int, float)) and altura_cm and isinstance(peso, (int, float)):
            imc_calculado = round(peso / ((altura_cm / 100) ** 2), 2)

        macros = eni.get("porcentajeMacronutrientes") if isinstance(eni.get("porcentajeMacronutrientes"), dict) else {}
        objetivo = eni.get("objetivoTratamiento") if isinstance(eni.get("objetivoTratamiento"), dict) else {}

        return {
            "ultimo_paso": eni.get("lastViewedStep"),
            "objetivo_tratamiento": objetivo.get("nombre"),
            "objetivo_tratamiento_descripcion": objetivo.get("descripcion"),
            "motivo_visita": eni.get("motivoVisita"),
            "sexo": _name(eni.get("sexo")),
            "fecha_nacimiento": eni.get("fechaNacimiento"),
            "edad": _age_years(eni.get("fechaNacimiento")),
            "altura_cm": altura_cm,
            "peso_kg": peso,
            "peso_corregido_kg": eni.get("pesoCorregido"),
            "peso_sugerido_kg": eni.get("pesoSugerido"),
            "imc_calculado": imc_calculado,
            "porcentaje_grasa_actual": eni.get("porcentajeGrasaActual"),
            "perimetro_cintura": eni.get("perimetroCintura"),
            "perimetro_cadera": eni.get("perimetroCadera"),
            "perimetro_muneca": eni.get("perimetroMuneca"),
            "valor_energetico_dia": eni.get("valorEnergetico"),
            "valor_energetico_min": eni.get("valorEnergeticoMin"),
            "valor_energetico_max": eni.get("valorEnergeticoMax"),
            "valor_energetico_por_dia": eni.get("valorEnergeticoPorDia"),
            "porcentaje_macros_por_dia": eni.get("porcentajeMacronutrientesPorDia"),
            "objetivo_proteinas_pct": macros.get("proteinas"),
            "objetivo_grasas_pct": macros.get("grasas"),
            "objetivo_carbohidratos_pct": macros.get("glucidos"),
            "ingesta_diaria_recomendada": _nutrients(eni.get("ingestaRecomendadaPlan") or {}),
            "agua_dia_litros": _name(eni.get("aguaDia")),
            "estilo_alimentacion": _name(eni.get("estiloAlimentacion")),
            "actividad_fisica": _name(eni.get("actividadFisica")),
            "deportista": _name(eni.get("deportista")),
            "turno_laboral": _name(eni.get("turnoLaboral")),
            "comentarios_horarios": eni.get("comentariosHorarios"),
            "dificultad_cocina": _name(eni.get("dificultadCocina")),
            "temporada": _name(eni.get("temporada")),
            "menstruacion": _name(eni.get("menstruacion")),
            "consumo_alcohol": _name(eni.get("consumoAlcohol")),
            "consumo_tabaco": _name(eni.get("consumoTabaco")),
            "grado_motivacion": _name(eni.get("gradoMotivacion")),
            "antecedentes_familiares": _name(eni.get("antecedentesFamiliares")),
            "trastornos_alimentacion": _name(eni.get("trastornosAlimentacion")),
            "intentos_previos": _name(eni.get("intentosPrevios")),
            "ya_recortando": _name(eni.get("yaRecortando")),
            "comer_deprisa": _name(eni.get("comerDeprisa")),
            "picar_entre_horas": _name(eni.get("picarHoras")),
            "tipo_trabajo": _name(eni.get("tipoTrabajo")),
            "patologias": _names(eni.get("patologias")),
            "farmacos": [f.get("nombre") for f in eni.get("farmacos") or [] if isinstance(f, dict)],
            "alergias": _names(eni.get("alimentosAlergias")) + _names(eni.get("gruposAlimentosAlergias")),
            "intolerancias": _names(eni.get("alimentosIntolerancias")) + _names(eni.get("gruposAlimentosIntolerancias")),
            "aversiones": _names(eni.get("alimentosAversiones")) + _names(eni.get("gruposAlimentosAversiones")),
            "raw": eni,
        }


class DietoProValoracionesSensor(DietoProEntity):
    """Ratings the patient has given to dishes - GET /api/paciente/rating."""

    _attr_icon = "mdi:star"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "valoraciones", "Valoraciones")

    def _ratings(self) -> list:
        data = (self.coordinator.data or {}).get("ratings")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            inner = _first_present(data, ("ratings", "items", "data"))
            if isinstance(inner, list):
                return inner
        return []

    @property
    def native_value(self) -> int:
        return len(self._ratings())

    @property
    def extra_state_attributes(self) -> dict:
        return {"raw": self._ratings()}


class DietoProCharlasSensor(DietoProEntity):
    """Consultation talks with the dietista - GET /api/paciente/charla.

    Partially confirmed from decompiled source (a "charlaPaciente"-style
    wrapper with a nested "charla" object): "charla.titulo" is the talk's
    title, and "fechaImparticion" (ISO date, parsed with Luxon in the app)
    is its delivery date - seen as a sibling field on the wrapper, not
    nested inside "charla" itself. Never confirmed against a real response,
    so this stays defensive and always keeps "raw" available.
    """

    _attr_icon = "mdi:phone-message"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "charlas", "Charlas")

    def _charlas(self) -> Any:
        return (self.coordinator.data or {}).get("charlas")

    def _items(self) -> list[dict]:
        data = self._charlas()
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            return [data]
        return []

    def _cleaned(self) -> list[dict]:
        cleaned = []
        for item in self._items():
            charla = item.get("charla") if isinstance(item.get("charla"), dict) else {}
            tema = item.get("tema") if isinstance(item.get("tema"), dict) else {}
            cleaned.append(
                {
                    "id": item.get("id"),
                    "titulo": charla.get("titulo") or item.get("titulo"),
                    "tema": tema.get("titulo"),
                    "fecha": _parse_datetime(item.get("fechaImparticion")),
                    "enlace": charla.get("link1") or None,
                    "enlace_nombre": charla.get("nombreLink1") or None,
                    "observaciones": charla.get("observaciones") or None,
                    "visto": item.get("vistoEnApp"),
                }
            )
        return cleaned

    @property
    def native_value(self) -> int:
        return len(self._items())

    @property
    def extra_state_attributes(self) -> dict:
        cleaned = self._cleaned()
        return {
            "charlas": cleaned,
            "sin_ver": sum(1 for c in cleaned if c.get("visto") is False),
            "raw": self._charlas(),
        }


class DietoProTodayEnergySensor(DietoProTodayMealsSensor):
    """Total calories (and macros) planned for today - a real number, so it
    can be graphed, used in automations and kept in long-term statistics."""

    _attr_icon = "mdi:fire"
    _attr_native_unit_of_measurement = "kcal"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset()

    def __init__(self, coordinator, entry) -> None:
        DietoProEntity.__init__(self, coordinator, entry, "calorias_hoy", "Calorías de hoy")

    @property
    def entity_picture(self) -> None:
        return None

    def _totals(self) -> dict:
        platos = [p for m in self._meals() for p in (m.get("platos") or [])]
        return _sum_nutrients([p.get("nutrientes") or {} for p in platos])

    @property
    def native_value(self) -> int | None:
        kcal = self._totals().get("energia_kcal")
        return round(kcal) if kcal else None

    @property
    def extra_state_attributes(self) -> dict:
        totals = self._totals()
        prot, gras, carb = (totals.get(k) or 0 for k in ("proteinas_g", "grasas_g", "carbohidratos_g"))
        kcal_macros = prot * 4 + carb * 4 + gras * 9
        pct = (lambda g, f: round(g * f * 100 / kcal_macros)) if kcal_macros else (lambda g, f: None)
        return {
            "proteinas_g": totals.get("proteinas_g"),
            "grasas_g": totals.get("grasas_g"),
            "carbohidratos_g": totals.get("carbohidratos_g"),
            "fibra_g": totals.get("fibra_g"),
            "proteinas_pct": pct(prot, 4),
            "grasas_pct": pct(gras, 9),
            "carbohidratos_pct": pct(carb, 4),
            "dieta_index": self._todays_dieta_indexed()[0],
        }


MEAL_ICONS = {
    "desayuno": "mdi:coffee",
    "tentempie1": "mdi:food-apple-outline",
    "tentempie2": "mdi:food-apple-outline",
    "comida": "mdi:food",
    "merienda1": "mdi:cookie",
    "merienda2": "mdi:cookie",
    "cena": "mdi:silverware-fork-knife",
    "recena1": "mdi:moon-waning-crescent",
    "recena2": "mdi:moon-waning-crescent",
}


class DietoProNextMealSensor(DietoProTodayMealsSensor):
    """The next meal still to come today (by its scheduled "hora")."""

    _attr_icon = "mdi:clock-outline"
    _unrecorded_attributes = frozenset({"platos"})

    def __init__(self, coordinator, entry) -> None:
        DietoProEntity.__init__(self, coordinator, entry, "proxima_comida", "Próxima comida")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # "Next" depends on the clock, not only on data: re-evaluate every
        # minute instead of waiting for the next (15 min) poll.
        self.async_on_remove(async_track_time_interval(self.hass, self._async_tick, timedelta(minutes=1)))

    @callback
    def _async_tick(self, _now) -> None:
        # Must be a @callback: HA runs a plain function/lambda in a worker thread,
        # and async_write_ha_state() is not allowed from a thread (it raised
        # "calls async_write_ha_state from a thread" every minute).
        self.async_write_ha_state()

    @property
    def entity_picture(self) -> None:
        return None

    def _next(self) -> tuple[dict, time] | tuple[None, None]:
        now_t = dt_util.now().time()
        best: tuple[dict, time] | None = None
        for meal in self._meals():
            hora = parse_hora(meal.get("hora"))
            if hora is not None and hora >= now_t and (best is None or hora < best[1]):
                best = (meal, hora)
        return best if best else (None, None)

    @property
    def icon(self) -> str:
        meal, _ = self._next()
        return MEAL_ICONS.get(meal["franja"], "mdi:clock-outline") if meal else "mdi:check-circle-outline"

    @property
    def native_value(self) -> str:
        meal, _ = self._next()
        if meal is None:
            return "Nada más hoy"
        return franja_labels(self.coordinator.data).get(meal["franja"], meal["franja"])

    @property
    def extra_state_attributes(self) -> dict:
        meal, hora = self._next()
        if meal is None:
            return {}
        platos = meal.get("platos") or []
        now = dt_util.now()
        start = datetime.combine(now.date(), hora, tzinfo=now.tzinfo)
        return {
            "franja": meal["franja"],
            "hora": hora.strftime("%H:%M"),
            "en_minutos": max(0, int((start - now).total_seconds() // 60)),
            "platos": [p.get("nombre") for p in platos],
            "calorias": sum(p.get("calorias") or 0 for p in platos),
        }


# (key, name, field in _clean_seguimiento, unit, icon, device_class)
METRIC_SENSORS = (
    ("peso", "Peso", "peso", UnitOfMass.KILOGRAMS, "mdi:scale-bathroom", SensorDeviceClass.WEIGHT),
    ("imc", "IMC", "imc", None, "mdi:human", None),
    ("porcentaje_grasa", "Grasa corporal", "porcentaje_grasa", "%", "mdi:water-percent", None),
    ("masa_magra", "Masa magra", "peso_masa_magra", UnitOfMass.KILOGRAMS, "mdi:arm-flex", SensorDeviceClass.WEIGHT),
    ("perimetro_cintura", "Perímetro de cintura", "perimetro_cintura", "cm", "mdi:tape-measure", None),
)


class DietoProMetricSensor(DietoProEntity):
    """Latest body measurement as its own numeric sensor (weight, BMI, body
    fat %...), so Home Assistant keeps history/statistics for it."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, key, name, field, unit, icon, device_class) -> None:
        super().__init__(coordinator, entry, key, name)
        self._field = field
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_device_class = device_class

    def _latest(self) -> dict:
        history = seguimiento_history(self.coordinator.data)
        return history[-1] if history else {}

    @property
    def native_value(self) -> float | None:
        value = self._latest().get(self._field)
        return value if isinstance(value, (int, float)) else None

    @property
    def extra_state_attributes(self) -> dict:
        return {"fecha_medicion": self._latest().get("fecha")}


# (key, name, field in _sum_nutrients, unit, icon)
NUTRIENT_SENSORS = (
    ("proteinas_hoy", "Proteínas de hoy", "proteinas_g", "g", "mdi:food-steak"),
    ("grasas_hoy", "Grasas de hoy", "grasas_g", "g", "mdi:oil"),
    ("carbohidratos_hoy", "Hidratos de hoy", "carbohidratos_g", "g", "mdi:bread-slice"),
    ("fibra_hoy", "Fibra de hoy", "fibra_g", "g", "mdi:grain"),
)


class DietoProTodayNutrientSensor(DietoProTodayEnergySensor):
    """One of today's planned nutrient totals as its own numeric sensor."""

    def __init__(self, coordinator, entry, key, name, field, unit, icon) -> None:
        DietoProEntity.__init__(self, coordinator, entry, key, name)
        self._field = field
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon

    @property
    def native_value(self) -> float | None:
        value = self._totals().get(self._field)
        return round(value, 1) if value else None

    @property
    def extra_state_attributes(self) -> dict:
        return {}


def _numeric(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _eni(data: dict) -> dict:
    eni = data.get("eni")
    return eni if isinstance(eni, dict) else {}


def _target_kcal(data: dict) -> float | None:
    value = _numeric(_eni(data).get("valorEnergetico"))
    return round(value) if value else None


def _target_weight(data: dict) -> float | None:
    eni = _eni(data)
    return _numeric(eni.get("pesoSugerido")) or _numeric(eni.get("pesoCorregido"))


def _weight_to_target(data: dict) -> float | None:
    history = seguimiento_history(data)
    target = _target_weight(data)
    weight = _numeric(history[-1].get("peso")) if history else None
    return round(weight - target, 1) if weight is not None and target else None


def _weight_change_30d(data: dict) -> float | None:
    history = [h for h in seguimiento_history(data) if _numeric(h.get("peso")) is not None and h.get("fecha")]
    if len(history) < 2:
        return None
    latest = history[-1]
    cutoff = latest["fecha"] - timedelta(days=30)
    older = [h for h in history if h["fecha"] <= cutoff]
    base = older[-1] if older else history[0]
    return round(latest["peso"] - base["peso"], 1) if base is not latest else None


def _days_since_measurement(data: dict) -> int | None:
    history = [h for h in seguimiento_history(data) if h.get("fecha")]
    return (dt_util.now() - history[-1]["fecha"]).days if history else None


# (key, name, function(data), unit, icon, device_class, state_class)
DERIVED_SENSORS = (
    ("objetivo_calorico", "Objetivo calórico diario", _target_kcal, "kcal", "mdi:target", None, None),
    ("peso_objetivo", "Peso objetivo", _target_weight, UnitOfMass.KILOGRAMS, "mdi:flag-checkered", SensorDeviceClass.WEIGHT, None),
    ("peso_restante", "Peso hasta el objetivo", _weight_to_target, UnitOfMass.KILOGRAMS, "mdi:scale-balance", SensorDeviceClass.WEIGHT, SensorStateClass.MEASUREMENT),
    ("variacion_peso_30d", "Variación de peso (30 días)", _weight_change_30d, UnitOfMass.KILOGRAMS, "mdi:trending-down", SensorDeviceClass.WEIGHT, SensorStateClass.MEASUREMENT),
    ("dias_sin_medir", "Días desde la última medición", _days_since_measurement, "d", "mdi:calendar-end", None, SensorStateClass.MEASUREMENT),
)


class DietoProDerivedSensor(DietoProEntity):
    """Values computed from several endpoints (survey targets vs. measurements)."""

    def __init__(self, coordinator, entry, key, name, func, unit, icon, device_class, state_class) -> None:
        super().__init__(coordinator, entry, key, name)
        self._func = func
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_device_class = device_class
        self._attr_state_class = state_class

    @property
    def icon(self) -> str:
        if self._attr_unique_id.endswith("variacion_peso_30d"):
            value = self.native_value
            if isinstance(value, (int, float)) and value:
                return "mdi:trending-down" if value < 0 else "mdi:trending-up"
            return "mdi:trending-neutral"
        return self._attr_icon

    @property
    def native_value(self):
        try:
            return self._func(self.coordinator.data or {})
        except (TypeError, ValueError, KeyError):
            return None


def normalize_slots(data: Any) -> list[dict]:
    """Free slots as a flat list of {fecha, hora, ...}.

    The exact response shape of available-slots is only known from the
    app's code (it reads "slots"), not from a real response, so this accepts
    the plausible layouts: {date: [times]}, {date: [{time, ...}]} or a flat
    list of {date, time} objects - and always keeps what it could not map.
    """
    slots = data.get("slots") if isinstance(data, dict) else data
    out: list[dict] = []

    def add(fecha: Any, item: Any) -> None:
        if isinstance(item, dict):
            hora = _first_present(item, ("time", "hora", "start", "from", "startTime"))
            fecha = _first_present(item, ("date", "fecha", "day")) or fecha
            entry = {"fecha": fecha, "hora": hora}
            extra = {k: v for k, v in item.items() if k not in ("time", "hora", "start", "from", "startTime", "date", "fecha", "day")}
            if extra:
                entry["extra"] = extra
            out.append(entry)
        else:
            out.append({"fecha": fecha, "hora": item})

    if isinstance(slots, dict):
        for fecha, items in slots.items():
            for item in items if isinstance(items, list) else [items]:
                add(fecha, item)
    elif isinstance(slots, list):
        for item in slots:
            add(None, item)
    return [slot for slot in out if slot.get("hora") is not None]


class DietoProEatenSensor(DietoProTodayMealsSensor):
    """Meals ticked as eaten today (stored in Home Assistant, shared by every
    device) plus adherence over the last week."""

    _attr_icon = "mdi:check-circle-outline"
    _unrecorded_attributes = frozenset({"historial"})

    def __init__(self, coordinator, entry) -> None:
        DietoProEntity.__init__(self, coordinator, entry, "comidas_tomadas_hoy", "Comidas tomadas hoy")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        store = getattr(self.coordinator, "eaten", None)
        if store is not None:
            self.async_on_remove(store.add_listener(self._on_eaten_changed))

    @callback
    def _on_eaten_changed(self) -> None:
        self.async_write_ha_state()

    @property
    def entity_picture(self) -> None:
        return None

    def _eaten_on(self, day) -> set[str]:
        store = getattr(self.coordinator, "eaten", None)
        return store.get(day) if store is not None else set()

    def _planned_on(self, day) -> list[dict]:
        dietas = self._dietas()
        plan = (self.coordinator.data or {}).get("plan_detail")
        start = plan_start_date(plan) if isinstance(plan, dict) else None
        index = dieta_index_for(day, len(dietas), start)
        return [m for m in shape_meals(dietas[index]) if m.get("platos")] if index is not None else []

    def _day(self, day) -> tuple[list[str], list[str], list[dict]]:
        planned = self._planned_on(day)
        planned_keys = [m["franja"] for m in planned]
        eaten = [f for f in sorted(self._eaten_on(day)) if f in planned_keys]
        return eaten, planned_keys, planned

    @property
    def native_value(self) -> int:
        return len(self._day(dt_util.now().date())[0])

    @property
    def extra_state_attributes(self) -> dict:
        today = dt_util.now().date()
        eaten, planned_keys, planned = self._day(today)
        kcal = lambda meals: sum(  # noqa: E731
            p.get("calorias") or 0 for m in meals for p in (m.get("platos") or []) if isinstance(p.get("calorias"), (int, float))
        )
        historial: dict[str, dict] = {}
        week_eaten = week_planned = 0
        for offset in range(13, -1, -1):
            day = today - timedelta(days=offset)
            d_eaten, d_planned, _ = self._day(day)
            if d_planned:
                historial[day.isoformat()] = {"tomadas": len(d_eaten), "planificadas": len(d_planned)}
            if offset < 7:
                week_eaten += len(d_eaten)
                week_planned += len(d_planned)
        pct = lambda a, b: round(100 * a / b) if b else None  # noqa: E731
        return {
            "tomadas": eaten,
            "planificadas": planned_keys,
            "kcal_tomadas": round(kcal([m for m in planned if m["franja"] in eaten])),
            "kcal_planificadas": round(kcal(planned)),
            "cumplimiento_hoy": pct(len(eaten), len(planned_keys)),
            "cumplimiento_semana": pct(week_eaten, week_planned),
            "tomadas_semana": week_eaten,
            "planificadas_semana": week_planned,
            "historial": historial,
        }


IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif")
MAX_CHAT_FILE_BYTES = 15 * 1024 * 1024


def chat_file_kind(filename: str, content_type: str | None = None) -> str:
    """"images" or "documents": the chat has a separate endpoint for each."""
    if (content_type or "").startswith("image/") or filename.lower().endswith(IMAGE_EXTENSIONS):
        return "images"
    return "documents"
