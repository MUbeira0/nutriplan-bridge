"""The Nutriplan Bridge integration."""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from http import HTTPStatus
from pathlib import Path

import voluptuous as vol
from aiohttp import web

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import KEY_HASS, HomeAssistantView, StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .api import DietoProApiClient, DietoProApiError, DietoProAuthError
from .const import CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL, DOMAIN, PLATFORMS
from .coordinator import DietoProDataUpdateCoordinator, plan_start_date
from .eaten import EatenStore
from .sensor import MAX_CHAT_FILE_BYTES, MEAL_SLOT_KEYS, chat_file_kind, _plato_detail, normalize_slots, shape_day, shopping_list

_LOGGER = logging.getLogger(__name__)

CARD_URL_PATH = "/nutriplan_bridge_files/nutriplan-bridge-card.js"

SERVICE_MARK_CHAT_READ = "marcar_chat_leido"
SERVICE_RATE_DISH = "valorar_plato"
SERVICE_PLATO_OPTIONS = "opciones_plato"
SERVICE_CHANGE_PLATO = "cambiar_plato"
SERVICE_MARK_EATEN = "marcar_comida"
SERVICE_OPEN_MEASUREMENT = "medicion_abierta"
SERVICE_REGISTER_MEASUREMENT = "registrar_medicion"
SERVICE_CHAT_FILE = "enviar_archivo_chat"

# service field -> field name expected by DietoPro, in the order the app's wizard asks for them
MEASUREMENT_FIELDS = (
    ("peso", "peso", 20, 400),
    ("porcentaje_grasa", "porcentajeGrasa", 1, 80),
    ("porcentaje_agua", "porcentajeAgua", 1, 90),
    ("porcentaje_masa_magra", "porcentajeMasaMagra", 1, 99),
    ("perimetro_cintura", "perimetroCintura", 20, 300),
    ("perimetro_cadera", "perimetroCadera", 20, 300),
    ("perimetro_pecho", "perimetroPecho", 20, 300),
    ("perimetro_pierna", "perimetroPierna", 10, 200),
    ("perimetro_brazo", "perimetroBrazo", 5, 100),
)
SERVICE_ALIMENTO_OPTIONS = "opciones_alimento"
SERVICE_CHANGE_ALIMENTO = "cambiar_alimento"
SERVICE_GET_MEALS = "obtener_comidas"
SERVICE_SHOPPING = "lista_compra"
SERVICE_SEND_MESSAGE = "enviar_mensaje"
SERVICE_SLOTS = "citas_disponibles"
SERVICE_BOOK = "solicitar_cita"
SERVICE_CONFIRM_CITA = "confirmar_cita"
SERVICE_CANCEL_CITA = "cancelar_cita"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_ENTRY_SCHEMA = {vol.Optional("config_entry_id"): cv.string}

MARK_CHAT_READ_SCHEMA = vol.Schema(_ENTRY_SCHEMA)

RATE_DISH_SCHEMA = vol.Schema(
    {
        **_ENTRY_SCHEMA,
        vol.Required("super_plato_id"): vol.Any(cv.positive_int, cv.string),
        vol.Required("rating"): vol.All(vol.Coerce(float), vol.Range(min=1, max=5)),
    }
)

PLATO_OPTIONS_SCHEMA = vol.Schema(
    {
        **_ENTRY_SCHEMA,
        vol.Required("plato_id"): vol.Any(cv.positive_int, cv.string),
        vol.Required("franja"): cv.string,
    }
)

GET_MEALS_SCHEMA = vol.Schema(
    {
        **_ENTRY_SCHEMA,
        vol.Optional("plan_id"): vol.Any(cv.positive_int, cv.string),
    }
)

SEND_MESSAGE_SCHEMA = vol.Schema({**_ENTRY_SCHEMA, vol.Required("texto"): vol.All(cv.string, vol.Length(min=1, max=2000))})

SLOTS_SCHEMA = vol.Schema(
    {**_ENTRY_SCHEMA, vol.Optional("desde"): cv.date, vol.Optional("hasta"): cv.date}
)

BOOK_SCHEMA = vol.Schema(
    {
        **_ENTRY_SCHEMA,
        vol.Required("fecha"): cv.date,
        vol.Required("hora"): cv.string,
        vol.Optional("nota"): cv.string,
    }
)

CITA_ID_SCHEMA = vol.Schema({**_ENTRY_SCHEMA, vol.Optional("cita_id"): vol.Any(cv.positive_int, cv.string)})

SHOPPING_SCHEMA = vol.Schema(
    {
        **_ENTRY_SCHEMA,
        vol.Optional("plan_id"): vol.Any(cv.positive_int, cv.string),
        vol.Optional("dias"): vol.All(cv.ensure_list, [vol.Coerce(int)]),
        vol.Optional("todo_entity"): cv.entity_id,
    }
)

ALIMENTO_OPTIONS_SCHEMA = vol.Schema(
    {
        **_ENTRY_SCHEMA,
        vol.Required("alimento_id"): vol.Any(cv.positive_int, cv.string),
        vol.Required("cantidad"): vol.Coerce(float),
    }
)

# Untyped on purpose (same reason as CHANGE_PLATO_SCHEMA): bad values are
# validated by hand so they produce a clear message instead of a raw schema error.
CHANGE_ALIMENTO_SCHEMA = vol.Schema(
    {
        **_ENTRY_SCHEMA,
        vol.Required("plan_id"): object,
        vol.Required("dieta"): object,
        vol.Required("franja"): cv.string,
        vol.Required("alimento_actual_id"): object,
        vol.Required("cantidad_actual"): object,
        vol.Required("nuevo_alimento_id"): object,
        vol.Required("nueva_cantidad"): object,
    }
)

MARK_EATEN_SCHEMA = vol.Schema(
    {
        **_ENTRY_SCHEMA,
        vol.Required("franja"): cv.string,
        vol.Optional("tomada", default=True): cv.boolean,
        vol.Optional("fecha"): cv.date,
    }
)

REGISTER_MEASUREMENT_SCHEMA = vol.Schema(
    {
        **_ENTRY_SCHEMA,
        **{vol.Optional(key): vol.Coerce(float) for key, _api, _lo, _hi in MEASUREMENT_FIELDS},
        vol.Optional("finalizar", default=True): cv.boolean,
    }
)

CHAT_FILE_SCHEMA = vol.Schema(
    {**_ENTRY_SCHEMA, vol.Required("ruta"): cv.string, vol.Optional("texto", default=""): cv.string}
)

CHANGE_PLATO_SCHEMA = vol.Schema(
    {
        **_ENTRY_SCHEMA,
        # Deliberately untyped here (Voluptuous accepts anything): the
        # exact required types are documented in api.py's async_change_plato
        # (currentId/plato_actual_id and dieta/plan_id/platoId are all
        # numbers), but enforcing that at the schema level means a bad
        # value raises a raw, unhandled voluptuous error ("expected int for
        # dictionary value @ ...") straight in the card - seen live when one
        # option in a real response had no usable id. That's now filtered
        # out server-side too (see _async_handle_plato_options), but schema
        # validation runs before any of this integration's own code does,
        # so it can never route through _friendly_error - hence validating
        # by hand in _async_handle_change_plato() instead, where a bad value
        # (e.g. a stale card that hasn't picked up that filter yet) always
        # produces a clear, actionable message rather than a crash.
        vol.Required("plan_id"): object,
        vol.Required("dieta"): object,
        vol.Required("franja"): cv.string,
        vol.Required("plato_actual_id"): object,
        vol.Required("nuevo_plato_id"): object,
    }
)

_card_registered = False
_services_registered = False


def _integration_version() -> str:
    """Read manifest.json's version so the card URL's cache-buster changes
    automatically on every release - it used to be a hardcoded "1" that
    never changed across releases, so browsers kept serving a stale cached
    copy of the card even after updating through HACS."""
    manifest_path = Path(__file__).parent / "manifest.json"
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))["version"]
    except (OSError, KeyError, ValueError):
        return "0"


RESOURCE_PREFIX = "/nutriplan_bridge_files/v"


async def _async_register_lovelace_resource(hass: HomeAssistant, url: str) -> bool:
    """Register the card as a Lovelace *resource* (what HACS does for cards).

    Home Assistant also offers add_extra_js_url(), which injects the script into the page
    before the frontend has started. In a real recent Home Assistant that script runs, but the
    element it defines is gone by the time the dashboard renders ("Error de configuración" on
    the card) - found with an end-to-end run. A resource is loaded by Lovelace itself once the
    frontend is up, so it always works. Returns False when that is not possible (YAML-mode
    dashboards: those users keep the extra-JS fallback or add the resource by hand).
    """
    data = hass.data.get("lovelace")
    resources = data.get("resources") if isinstance(data, dict) else getattr(data, "resources", None)
    mode = data.get("mode") if isinstance(data, dict) else getattr(data, "mode", None)
    if resources is None or mode != "storage" or not hasattr(resources, "async_create_item"):
        _LOGGER.debug(
            "Nutriplan Bridge: not registering a Lovelace resource (data=%s, mode=%r, resources=%s)",
            type(data).__name__, mode, type(resources).__name__,
        )
        return False
    try:
        await resources.async_get_info()  # loads the stored resources once
        present = False
        for item in list(resources.async_items() or []):
            item_url = str(item.get("url", ""))
            if item_url == url:
                present = True
            elif item_url.startswith(RESOURCE_PREFIX):
                # an older versioned copy registered by this integration
                await resources.async_delete_item(item["id"])
        if not present:
            await resources.async_create_item({"res_type": "module", "url": url})
        _LOGGER.debug("Nutriplan Bridge: card registered as Lovelace resource %s", url)
        return True
    except Exception:  # noqa: BLE001 - never break setup over a dashboard resource
        _LOGGER.exception("Nutriplan Bridge: could not register the card as a Lovelace resource")
        return False


def _resolve_coordinator(hass: HomeAssistant, call: ServiceCall) -> DietoProDataUpdateCoordinator:
    coordinators: dict[str, DietoProDataUpdateCoordinator] = hass.data.get(DOMAIN, {})
    entry_id = call.data.get("config_entry_id")
    if entry_id:
        coordinator = coordinators.get(entry_id)
        if coordinator is None:
            raise HomeAssistantError(f"Unknown config_entry_id: {entry_id}")
        return coordinator
    if len(coordinators) == 1:
        return next(iter(coordinators.values()))
    if not coordinators:
        raise HomeAssistantError("No Nutriplan Bridge account is configured")
    options = ", ".join(sorted(coordinators))
    raise HomeAssistantError(
        f"Multiple Nutriplan Bridge accounts are configured; pass config_entry_id to pick one "
        f"(configured: {options})"
    )


def _friendly_error(action: str, err: Exception) -> HomeAssistantError:
    """DietoProApiError messages embed up to 200 chars of the raw API
    response, which is useful in the HA log but unreadable dumped straight
    into a Lovelace card (a wall of red JSON). Log the full detail here and
    hand the UI a short, actionable sentence instead. A 500 specifically
    means DietoPro's OWN server errored processing the request (not that it
    rejected it with a reason), so that's called out separately - trying
    the same change in the official app is the fastest way to tell whether
    it's this integration's payload or a problem on DietoPro's end.
    """
    _LOGGER.error("Nutriplan Bridge: %s failed: %s", action, err)
    if isinstance(err, DietoProAuthError):
        return HomeAssistantError(f"No se pudo {action}: la sesión con DietoPro no es válida. Revisa el registro de Home Assistant para más detalle.")
    if "-> 500" in str(err):
        return HomeAssistantError(
            f"No se pudo {action}: el servidor de DietoPro ha dado un error interno (no un simple rechazo). "
            "Prueba la misma acción en la app oficial - si allí también falla, no es un problema de esta "
            "integración. El detalle técnico está en el registro de Home Assistant."
        )
    return HomeAssistantError(f"No se pudo {action}. El detalle técnico está en el registro de Home Assistant.")


def _require_int(call: ServiceCall, key: str, label: str) -> int:
    value = call.data.get(key)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HomeAssistantError(
            f'El campo "{label}" no es un número válido ({value!r}). '
            "Cierra y vuelve a abrir \"Cambiar plato\" para refrescar las opciones e inténtalo de nuevo."
        ) from None


def _require_str(call: ServiceCall, key: str, label: str) -> str:
    value = call.data.get(key)
    if value in (None, ""):
        raise HomeAssistantError(
            f'Falta el campo "{label}". '
            "Cierra y vuelve a abrir \"Cambiar plato\" para refrescar las opciones e inténtalo de nuevo."
        )
    return str(value)


async def _async_handle_mark_chat_read(hass: HomeAssistant, call: ServiceCall) -> None:
    coordinator = _resolve_coordinator(hass, call)
    try:
        await coordinator.client.async_mark_chat_read()
    except (DietoProApiError, DietoProAuthError) as err:
        raise _friendly_error("marcar el chat como leído", err) from err
    await coordinator.async_request_refresh()


async def _async_handle_rate_dish(hass: HomeAssistant, call: ServiceCall) -> None:
    coordinator = _resolve_coordinator(hass, call)
    try:
        await coordinator.client.async_rate_dish(call.data["super_plato_id"], call.data["rating"])
    except (DietoProApiError, DietoProAuthError) as err:
        raise _friendly_error("valorar el plato", err) from err
    await coordinator.async_request_refresh()


def _find_item_list(data: object) -> list:
    """The real shape of GET /api/paciente/platos?change=...&ingesta=... was
    never confirmed against a live response (no test account available), so
    instead of guessing one specific wrapper key (which turned out to only
    ever surface a handful of items - almost certainly the wrong nested
    array, or the wrong key entirely, for at least some accounts), this
    walks the whole response and returns whichever array looks most like
    "a list of dishes": most items carrying an "id" field, then longest.
    """
    candidates: list[list] = []

    def _visit(value: object) -> None:
        if isinstance(value, list):
            candidates.append(value)
            for item in value:
                _visit(item)
        elif isinstance(value, dict):
            for item in value.values():
                _visit(item)

    _visit(data)
    if not candidates:
        return []

    def _score(arr: list) -> tuple[int, int]:
        with_id = sum(1 for item in arr if isinstance(item, dict) and "id" in item)
        return (with_id, len(arr))

    return max(candidates, key=_score)


def _require_number(call: ServiceCall, key: str, label: str) -> int | float:
    value = call.data.get(key)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HomeAssistantError(f'El campo "{label}" no es un número válido ({value!r}).') from None
    return int(number) if number.is_integer() else number


def normalize_alimento_options(raw: object, requested: float) -> list[dict]:
    """Equivalent foods as the app shows them: first household measure, quantity
    = midpoint of its min/max (falls back to the requested quantity)."""
    options = []
    for item in _find_item_list(raw):
        if not isinstance(item, dict):
            continue
        try:
            alimento_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        medidas = item.get("medidasCaseras")
        first = medidas[0] if isinstance(medidas, list) and medidas and isinstance(medidas[0], dict) else {}
        top, low = first.get("cantidadMaxima"), first.get("cantidadMinima")
        cantidad = round((top + low) / 2, 1) if isinstance(top, (int, float)) and isinstance(low, (int, float)) else requested
        grupo = item.get("grupoAlimento") if isinstance(item.get("grupoAlimento"), dict) else {}
        super_grupo = grupo.get("superGrupoAlimento") if isinstance(grupo.get("superGrupoAlimento"), dict) else {}
        options.append(
            {
                "alimento_id": alimento_id,
                "nombre": item.get("nombre"),
                "cantidad": int(cantidad) if float(cantidad).is_integer() else cantidad,
                "medida_casera": first.get("medida"),
                "grupo": grupo.get("nombre"),
                "categoria": super_grupo.get("nombre"),
            }
        )
    return options


async def _async_handle_alimento_options(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator = _resolve_coordinator(hass, call)
    alimento_id = _require_int(call, "alimento_id", "ID del alimento")
    cantidad = call.data["cantidad"]
    try:
        raw = await coordinator.client.async_get_alimento_options(alimento_id, cantidad)
    except (DietoProApiError, DietoProAuthError) as err:
        raise _friendly_error("buscar alimentos equivalentes", err) from err
    return {"opciones": normalize_alimento_options(raw, cantidad)}


async def _async_handle_change_alimento(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator = _resolve_coordinator(hass, call)
    plan_id = _require_int(call, "plan_id", "ID del plan")
    dieta = _require_int(call, "dieta", "Índice de la dieta")
    franja = _require_str(call, "franja", "Franja")
    actual_id = _require_int(call, "alimento_actual_id", "ID del alimento actual")
    nuevo_id = _require_int(call, "nuevo_alimento_id", "ID del nuevo alimento")
    cantidad_actual = _require_number(call, "cantidad_actual", "Cantidad actual")
    nueva_cantidad = _require_number(call, "nueva_cantidad", "Cantidad nueva")
    try:
        result = await coordinator.client.async_change_alimento(
            plan_id, dieta, franja, actual_id, cantidad_actual, nuevo_id, nueva_cantidad
        )
    except (DietoProApiError, DietoProAuthError) as err:
        raise _friendly_error("cambiar el alimento", err) from err
    await coordinator.async_request_refresh()
    return {"resultado": result}


async def _async_handle_mark_eaten(hass: HomeAssistant, call: ServiceCall) -> None:
    """Tick (or untick) a meal as eaten. Local to Home Assistant: nothing is sent to DietoPro."""
    coordinator = _resolve_coordinator(hass, call)
    if coordinator.eaten is None:
        raise HomeAssistantError("El registro de comidas tomadas no está disponible todavía.")
    franja = call.data["franja"]
    if franja not in MEAL_SLOT_KEYS:
        raise HomeAssistantError(f'Franja desconocida: "{franja}". Usa una de: {", ".join(MEAL_SLOT_KEYS)}.')
    coordinator.eaten.mark(call.data.get("fecha") or dt_util.now().date(), franja, call.data["tomada"])


def _open_measurement_id(raw: object) -> object | None:
    """The id of the open measurement out of GET /seguimiento (plain object, or wrapped)."""
    if isinstance(raw, dict):
        inner = raw.get("seguimiento")
        if isinstance(inner, dict):
            raw = inner
        return raw.get("id")
    return None


async def _async_handle_open_measurement(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Read-only: is there a measurement open for you to fill in?"""
    coordinator = _resolve_coordinator(hass, call)
    try:
        raw = await coordinator.client.async_get_open_seguimiento()
    except (DietoProApiError, DietoProAuthError) as err:
        raise _friendly_error("consultar la medición abierta", err) from err
    return {"abierta": _open_measurement_id(raw) is not None, "id": _open_measurement_id(raw), "raw": raw}


async def _async_handle_register_measurement(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Fill in the measurement your dietitian opened (manual; writes to your real account)."""
    coordinator = _resolve_coordinator(hass, call)
    values: list[tuple[str, str, float]] = []
    for key, api_name, low, high in MEASUREMENT_FIELDS:
        if key in call.data:
            value = call.data[key]
            if not low <= value <= high:
                raise HomeAssistantError(f'"{key}" debe estar entre {low} y {high} (recibido {value}).')
            values.append((key, api_name, round(value, 2)))
    if not values:
        raise HomeAssistantError("Indica al menos un valor (peso, porcentaje_grasa, perimetro_cintura...).")

    try:
        seguimiento_id = _open_measurement_id(await coordinator.client.async_get_open_seguimiento())
    except (DietoProApiError, DietoProAuthError) as err:
        raise _friendly_error("consultar la medición abierta", err) from err
    if seguimiento_id is None:
        raise HomeAssistantError(
            "No hay ninguna medición abierta. Tu dietista tiene que abrirla en DietoPro; "
            "esta integración no puede crearla."
        )

    sent: dict[str, float] = {}
    for key, api_name, value in values:
        try:
            await coordinator.client.async_put_seguimiento_step(seguimiento_id, api_name, value)
        except (DietoProApiError, DietoProAuthError) as err:
            done = ", ".join(sent) or "ninguno"
            raise HomeAssistantError(
                f"Se detuvo al enviar {key}. Ya enviados: {done}. Revisa el registro de Home Assistant."
            ) from err
        sent[key] = value

    finished = False
    if call.data["finalizar"]:
        try:
            await coordinator.client.async_finish_seguimiento()
            finished = True
        except (DietoProApiError, DietoProAuthError) as err:
            raise HomeAssistantError(
                "Los valores se enviaron pero no se pudo cerrar la medición; ciérrala desde la app de DietoPro."
            ) from err
    await coordinator.async_request_refresh()
    return {"id": seguimiento_id, "enviados": sent, "finalizada": finished}


async def _async_send_chat_file(hass: HomeAssistant, coordinator: DietoProDataUpdateCoordinator, filename: str, content: bytes, content_type: str | None, text: str) -> str:
    if not content:
        raise HomeAssistantError("El archivo está vacío.")
    if len(content) > MAX_CHAT_FILE_BYTES:
        raise HomeAssistantError(f"El archivo supera el máximo de {MAX_CHAT_FILE_BYTES // (1024 * 1024)} MB.")
    kind = chat_file_kind(filename, content_type)
    try:
        await coordinator.client.async_send_chat_file(kind, filename, content, content_type, text)
    except (DietoProApiError, DietoProAuthError) as err:
        raise _friendly_error("enviar el archivo al chat", err) from err
    await coordinator.async_request_refresh()
    return kind


async def _async_handle_chat_file(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Send a photo/document that lives on the Home Assistant machine to the dietitian."""
    coordinator = _resolve_coordinator(hass, call)
    path = Path(call.data["ruta"])
    if not hass.config.is_allowed_path(str(path)):
        raise HomeAssistantError(
            "Esa ruta no está permitida. Añádela en allowlist_external_dirs o usa /config o /media."
        )
    try:
        content = await hass.async_add_executor_job(path.read_bytes)
    except OSError as err:
        raise HomeAssistantError(f"No se pudo leer el archivo: {err}") from err
    kind = await _async_send_chat_file(hass, coordinator, path.name, content, None, call.data["texto"])
    return {"enviado": path.name, "tipo": kind}


class ChatUploadView(HomeAssistantView):
    """Used by the card's attach button: the browser posts the file here and it is
    forwarded to DietoPro. Administrators only."""

    url = "/api/nutriplan_bridge/chat_upload"
    name = "api:nutriplan_bridge:chat_upload"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app[KEY_HASS]
        user = request.get("hass_user")
        if user is None or not user.is_admin:
            return self.json_message("Solo administradores", HTTPStatus.FORBIDDEN)
        fields: dict[str, str] = {}
        upload: tuple[str, str | None, bytes] | None = None
        reader = await request.multipart()
        while (part := await reader.next()) is not None:
            if part.name == "file":
                content = bytearray()
                while chunk := await part.read_chunk(64 * 1024):
                    content.extend(chunk)
                    if len(content) > MAX_CHAT_FILE_BYTES:
                        return self.json_message("Archivo demasiado grande", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                upload = (part.filename or "archivo", part.headers.get("Content-Type"), bytes(content))
            elif part.name:
                fields[part.name] = (await part.text())[:2000]
        if upload is None:
            return self.json_message("Falta el archivo", HTTPStatus.BAD_REQUEST)

        class _Call:
            data = {"config_entry_id": fields["config_entry_id"]} if fields.get("config_entry_id") else {}

        try:
            coordinator = _resolve_coordinator(hass, _Call())
            kind = await _async_send_chat_file(hass, coordinator, upload[0], upload[2], upload[1], fields.get("text", ""))
        except HomeAssistantError as err:
            return self.json_message(str(err), HTTPStatus.BAD_REQUEST)
        return self.json({"ok": True, "tipo": kind})


async def _async_handle_plato_options(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator = _resolve_coordinator(hass, call)
    try:
        int(call.data["plato_id"])
    except (TypeError, ValueError):
        raise HomeAssistantError(
            "Este elemento no es un plato sino un alimento suelto: usa la acción opciones_alimento."
        ) from None
    try:
        result = await coordinator.client.async_get_plato_options(call.data["plato_id"], call.data["franja"])
    except (DietoProApiError, DietoProAuthError) as err:
        raise _friendly_error("buscar alternativas de plato", err) from err

    raw_options = _find_item_list(result)
    # Reuse the exact same shaping logic comidas_hoy uses for every plato
    # (adds absolute image URLs, nutrientes, structured ingredientes...) so
    # the card can render an option's full detail with the same code it
    # already uses for today's meals, instead of duplicating guesswork.
    raw_options = [opt for opt in raw_options if isinstance(opt, dict)]
    options = [_plato_detail({"plato": opt}) for opt in raw_options]
    # cambiar_plato's "platoId" is confirmed to be this option's own
    # top-level "id" (traced from the actual mutation call site - see
    # api.py). _plato_detail() normally reads "plato_id" from the same
    # place when its input really is plato-shaped, but force it from the
    # raw item directly so selecting an option still works even if that
    # assumption about the response shape turns out to be wrong.
    for shaped, raw in zip(options, raw_options):
        shaped["plato_id"] = raw.get("id", shaped.get("plato_id"))
    # A real response hit while testing this live included at least one
    # entry with no usable id at all - shipping it let the card build a
    # "select" button with an empty value, which then failed cambiar_plato's
    # schema validation (a raw voluptuous error, bypassing _friendly_error
    # entirely since it happens before the handler even runs). An option
    # with no id can never be selected anyway, so drop it here instead.
    options = [opt for opt in options if isinstance(opt.get("plato_id"), int)]
    return {"opciones": options}


async def _load_plan(coordinator: DietoProDataUpdateCoordinator, plan_id: object) -> tuple[dict, bool]:
    """(plan detail, is_current) for plan_id - the cached current plan, or
    fetched from DietoPro for any other (previous/upcoming) one."""
    data = coordinator.data or {}
    current = data.get("plan_detail") if isinstance(data.get("plan_detail"), dict) else None

    if current is not None and (plan_id is None or str(plan_id) == str(current.get("id"))):
        return current, True
    if plan_id is None:
        raise HomeAssistantError("No hay ningún plan activo")
    try:
        detail = await coordinator.client.async_get_plan_detail(plan_id)
    except (DietoProApiError, DietoProAuthError) as err:
        raise _friendly_error("cargar el plan", err) from err
    if not isinstance(detail, dict):
        raise HomeAssistantError("DietoPro no ha devuelto ese plan")
    return detail, False


async def _async_handle_get_meals(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Every day (dieta) of a plan, current or another one, fully shaped.

    Lets the card browse other days of the week and other plans without the
    sensor having to carry all of that in its (size-limited) attributes.
    """
    coordinator = _resolve_coordinator(hass, call)
    detail, is_current = await _load_plan(coordinator, call.data.get("plan_id"))

    dietas = detail.get("dietas") if isinstance(detail.get("dietas"), list) else []
    dias = [shape_day(i, d) for i, d in enumerate(dietas)]
    kcal_days = [d["resumen_nutricional"]["energia_kcal"] for d in dias if d["resumen_nutricional"].get("energia_kcal")]
    return {
        "plan_id": detail.get("id"),
        "titulo": detail.get("title"),
        "creado": detail.get("created"),
        "inicio": plan_start_date(detail).isoformat() if plan_start_date(detail) else None,
        "es_actual": is_current,
        "num_dietas": len(dietas),
        "promedio_kcal": round(sum(kcal_days) / len(kcal_days)) if kcal_days else None,
        "dias": dias,
    }


async def _async_handle_shopping(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Shopping list for a plan (all its days, or just the chosen ones),
    optionally pushed straight into a Home Assistant to-do/shopping list."""
    coordinator = _resolve_coordinator(hass, call)
    detail, _ = await _load_plan(coordinator, call.data.get("plan_id"))
    dietas = detail.get("dietas") if isinstance(detail.get("dietas"), list) else []
    wanted = call.data.get("dias")
    indexes = range(len(dietas)) if not wanted else [i for i in wanted if 0 <= i < len(dietas)]
    items = shopping_list([shape_day(i, dietas[i]) for i in indexes])

    todo_entity = call.data.get("todo_entity")
    if todo_entity:
        for item in items:
            text = f"{item['nombre']} ({item['cantidad_g']} g)" if item["cantidad_g"] else item["nombre"]
            try:
                await hass.services.async_call(
                    "todo", "add_item", {"entity_id": todo_entity, "item": text}, blocking=True
                )
            except HomeAssistantError as err:
                raise HomeAssistantError(f"No se pudo añadir a {todo_entity}: {err}") from err

    return {
        "plan_id": detail.get("id"),
        "dias": list(indexes),
        "total": len(items),
        "enviados_a_lista": todo_entity if todo_entity else None,
        "items": items,
    }


async def _async_handle_send_message(hass: HomeAssistant, call: ServiceCall) -> None:
    coordinator = _resolve_coordinator(hass, call)
    try:
        await coordinator.client.async_send_message(call.data["texto"])
    except (DietoProApiError, DietoProAuthError) as err:
        raise _friendly_error("enviar el mensaje", err) from err
    await coordinator.async_request_refresh()


async def _async_handle_slots(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator = _resolve_coordinator(hass, call)
    start = call.data.get("desde") or dt_util.now().date()
    end = call.data.get("hasta") or start + timedelta(days=6)
    try:
        raw = await coordinator.client.async_get_available_slots(start.isoformat(), end.isoformat())
    except (DietoProApiError, DietoProAuthError) as err:
        raise _friendly_error("consultar los huecos libres", err) from err
    slots = normalize_slots(raw)
    return {"desde": start.isoformat(), "hasta": end.isoformat(), "total": len(slots), "huecos": slots, "raw": raw}


async def _async_handle_book(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator = _resolve_coordinator(hass, call)
    try:
        result = await coordinator.client.async_request_booking(
            call.data["fecha"].isoformat(), call.data["hora"], call.data.get("nota")
        )
    except (DietoProApiError, DietoProAuthError) as err:
        raise _friendly_error("solicitar la cita", err) from err
    await coordinator.async_request_refresh()
    return {"resultado": result}


def _cita_id(coordinator: DietoProDataUpdateCoordinator, call: ServiceCall) -> object:
    cita_id = call.data.get("cita_id")
    if cita_id is None:
        wrapper = (coordinator.data or {}).get("cita")
        cita = wrapper.get("cita") if isinstance(wrapper, dict) else None
        cita_id = cita.get("id") if isinstance(cita, dict) else None
    if cita_id is None:
        raise HomeAssistantError("No hay ninguna cita pendiente (indica cita_id si quieres otra).")
    return cita_id


async def _async_handle_confirm_cita(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator = _resolve_coordinator(hass, call)
    cita_id = _cita_id(coordinator, call)
    try:
        result = await coordinator.client.async_confirm_cita(cita_id)
    except (DietoProApiError, DietoProAuthError) as err:
        raise _friendly_error("confirmar la cita", err) from err
    await coordinator.async_request_refresh()
    return {"cita_id": cita_id, "resultado": result}


async def _async_handle_cancel_cita(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator = _resolve_coordinator(hass, call)
    cita_id = _cita_id(coordinator, call)
    try:
        result = await coordinator.client.async_cancel_cita(cita_id)
    except (DietoProApiError, DietoProAuthError) as err:
        raise _friendly_error("cancelar la cita", err) from err
    await coordinator.async_request_refresh()
    return {"cita_id": cita_id, "resultado": result}


async def _async_handle_change_plato(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator = _resolve_coordinator(hass, call)
    plan_id = _require_int(call, "plan_id", "ID del plan")
    dieta = _require_int(call, "dieta", "Índice de la dieta")
    franja = _require_str(call, "franja", "Franja")
    plato_actual_id = _require_int(call, "plato_actual_id", "ID del plato actual")
    nuevo_plato_id = _require_int(call, "nuevo_plato_id", "ID del nuevo plato")
    try:
        result = await coordinator.client.async_change_plato(plan_id, dieta, franja, plato_actual_id, nuevo_plato_id)
    except (DietoProApiError, DietoProAuthError) as err:
        raise _friendly_error("cambiar el plato", err) from err
    await coordinator.async_request_refresh()
    return {"resultado": result}


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the bundled Lovelace card and the two write services once,
    regardless of how many accounts are configured."""
    global _card_registered, _services_registered
    if not _card_registered:
        try:
            www_dir = Path(__file__).parent / "www"
            # Reading manifest.json is blocking I/O: do it in the executor, not in the event loop.
            version = await hass.async_add_executor_job(_integration_version)
            versioned = f"/nutriplan_bridge_files/v{version}/nutriplan-bridge-card.js"
            await hass.http.async_register_static_paths(
                [
                    StaticPathConfig(CARD_URL_PATH, str(www_dir / "nutriplan-bridge-card.js"), False),
                    StaticPathConfig(versioned, str(www_dir / "nutriplan-bridge-card.js"), False),
                ]
            )
            if not await _async_register_lovelace_resource(hass, versioned):
                add_extra_js_url(hass, versioned)
            _card_registered = True
        except Exception:  # noqa: BLE001
            # Registering the card must never take the rest of the
            # integration down with it (sensors/services should still work
            # even if this fails) - but a silent failure here is exactly
            # what made someone have to add the resource by hand instead of
            # it "just working". Log it loudly and tell them the fallback.
            _LOGGER.exception(
                "Nutriplan Bridge: could not auto-register the Lovelace card. "
                "Add it manually instead: Settings -> Dashboards -> Resources -> "
                "Add resource -> URL '%s', type 'JavaScript Module'.",
                CARD_URL_PATH,
            )

    if not _services_registered:

        async def _mark_chat_read(call: ServiceCall) -> None:
            await _async_handle_mark_chat_read(hass, call)

        async def _rate_dish(call: ServiceCall) -> None:
            await _async_handle_rate_dish(hass, call)

        async def _plato_options(call: ServiceCall) -> ServiceResponse:
            return await _async_handle_plato_options(hass, call)

        async def _get_meals(call: ServiceCall) -> ServiceResponse:
            return await _async_handle_get_meals(hass, call)

        async def _send_message(call: ServiceCall) -> None:
            await _async_handle_send_message(hass, call)

        async def _slots(call: ServiceCall) -> ServiceResponse:
            return await _async_handle_slots(hass, call)

        async def _book(call: ServiceCall) -> ServiceResponse:
            return await _async_handle_book(hass, call)

        async def _confirm_cita(call: ServiceCall) -> ServiceResponse:
            return await _async_handle_confirm_cita(hass, call)

        async def _cancel_cita(call: ServiceCall) -> ServiceResponse:
            return await _async_handle_cancel_cita(hass, call)

        async def _alimento_options(call: ServiceCall) -> ServiceResponse:
            return await _async_handle_alimento_options(hass, call)

        async def _change_alimento(call: ServiceCall) -> ServiceResponse:
            return await _async_handle_change_alimento(hass, call)

        async def _open_measurement(call: ServiceCall) -> ServiceResponse:
            return await _async_handle_open_measurement(hass, call)

        async def _register_measurement(call: ServiceCall) -> ServiceResponse:
            return await _async_handle_register_measurement(hass, call)

        async def _chat_file(call: ServiceCall) -> ServiceResponse:
            return await _async_handle_chat_file(hass, call)

        async def _mark_eaten(call: ServiceCall) -> None:
            await _async_handle_mark_eaten(hass, call)

        async def _shopping(call: ServiceCall) -> ServiceResponse:
            return await _async_handle_shopping(hass, call)

        async def _change_plato(call: ServiceCall) -> ServiceResponse:
            return await _async_handle_change_plato(hass, call)

        hass.services.async_register(DOMAIN, SERVICE_MARK_CHAT_READ, _mark_chat_read, schema=MARK_CHAT_READ_SCHEMA)
        hass.services.async_register(DOMAIN, SERVICE_RATE_DISH, _rate_dish, schema=RATE_DISH_SCHEMA)
        hass.services.async_register(
            DOMAIN,
            SERVICE_PLATO_OPTIONS,
            _plato_options,
            schema=PLATO_OPTIONS_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_MEALS,
            _get_meals,
            schema=GET_MEALS_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
        hass.services.async_register(DOMAIN, SERVICE_SEND_MESSAGE, _send_message, schema=SEND_MESSAGE_SCHEMA)
        hass.services.async_register(
            DOMAIN, SERVICE_SLOTS, _slots, schema=SLOTS_SCHEMA, supports_response=SupportsResponse.ONLY
        )
        hass.services.async_register(
            DOMAIN, SERVICE_BOOK, _book, schema=BOOK_SCHEMA, supports_response=SupportsResponse.ONLY
        )
        hass.services.async_register(
            DOMAIN, SERVICE_CONFIRM_CITA, _confirm_cita, schema=CITA_ID_SCHEMA, supports_response=SupportsResponse.ONLY
        )
        hass.services.async_register(
            DOMAIN, SERVICE_CANCEL_CITA, _cancel_cita, schema=CITA_ID_SCHEMA, supports_response=SupportsResponse.ONLY
        )
        hass.services.async_register(DOMAIN, SERVICE_MARK_EATEN, _mark_eaten, schema=MARK_EATEN_SCHEMA)
        hass.services.async_register(
            DOMAIN, SERVICE_OPEN_MEASUREMENT, _open_measurement, schema=MARK_CHAT_READ_SCHEMA, supports_response=SupportsResponse.ONLY
        )
        hass.services.async_register(
            DOMAIN, SERVICE_REGISTER_MEASUREMENT, _register_measurement, schema=REGISTER_MEASUREMENT_SCHEMA, supports_response=SupportsResponse.ONLY
        )
        hass.services.async_register(
            DOMAIN, SERVICE_CHAT_FILE, _chat_file, schema=CHAT_FILE_SCHEMA, supports_response=SupportsResponse.ONLY
        )
        hass.http.register_view(ChatUploadView())
        hass.services.async_register(
            DOMAIN,
            SERVICE_SHOPPING,
            _shopping,
            schema=SHOPPING_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ALIMENTO_OPTIONS, _alimento_options, schema=ALIMENTO_OPTIONS_SCHEMA, supports_response=SupportsResponse.ONLY
        )
        hass.services.async_register(
            DOMAIN, SERVICE_CHANGE_ALIMENTO, _change_alimento, schema=CHANGE_ALIMENTO_SCHEMA, supports_response=SupportsResponse.ONLY
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_CHANGE_PLATO,
            _change_plato,
            schema=CHANGE_PLATO_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
        _services_registered = True

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    client = DietoProApiClient(session, entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])

    try:
        await client.async_login()
    except DietoProAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except DietoProApiError as err:
        # A network/connectivity failure is not the same as bad credentials -
        # ConfigEntryAuthFailed would trigger HA's reauth flow (asking to
        # re-enter the password) for what might just be a temporary outage.
        # ConfigEntryNotReady makes HA retry setup automatically instead.
        raise ConfigEntryNotReady(str(err)) from err

    interval = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    coordinator = DietoProDataUpdateCoordinator(hass, client, interval, entry.entry_id)
    coordinator.eaten = EatenStore(hass, entry.entry_id)
    await coordinator.eaten.async_load()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    async def _refresh_at_midnight(_now) -> None:
        # Today's meals and the "current plan" depend on the date, but data
        # was only recomputed on the (default 15 min) poll - after midnight
        # the card kept showing yesterday's meals / last week's plan.
        await coordinator.async_request_refresh()

    entry.async_on_unload(async_track_time_change(hass, _refresh_at_midnight, hour=0, minute=0, second=15))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
