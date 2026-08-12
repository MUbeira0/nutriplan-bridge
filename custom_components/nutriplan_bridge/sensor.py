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

    talla_plato = plato.get("tallaPlato") if isinstance(plato.get("tallaPlato"), dict) else {}
    image_path = super_plato.get("imagePath") or super_plato.get("thumbnail")
    energia = plato.get("energia")
    return {
        "subingesta_id": sub.get("id"),
        "plato_id": plato.get("id"),
        "super_plato_id": super_plato.get("id"),
        "nombre": super_plato.get("nombre"),
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
        if len(dietas) == 1:
            return 0, dietas[0]
        index = dt_util.now().weekday() % len(dietas)
        return index, dietas[index]

    def _todays_dieta(self) -> dict | None:
        return self._todays_dieta_indexed()[1]

    def _meals(self) -> list[dict]:
        dieta = self._todays_dieta()
        if not isinstance(dieta, dict):
            return []
        return [_meal_detail(slot, dieta[slot]) for slot in MEAL_SLOT_KEYS if dieta.get(slot)]

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
        return when.isoformat() if when else "programada"

    @property
    def extra_state_attributes(self) -> dict:
        return {"raw": self._cita()}


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
            cleaned.append(
                {
                    "titulo": charla.get("titulo") or item.get("titulo"),
                    "fecha": _parse_datetime(item.get("fechaImparticion")),
                }
            )
        return cleaned

    @property
    def native_value(self) -> int:
        return len(self._items())

    @property
    def extra_state_attributes(self) -> dict:
        return {"charlas": self._cleaned(), "raw": self._charlas()}
