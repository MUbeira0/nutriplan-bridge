"""The Nutriplan Bridge integration."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DietoProApiClient, DietoProApiError, DietoProAuthError
from .const import CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL, DOMAIN, PLATFORMS
from .coordinator import DietoProDataUpdateCoordinator
from .sensor import _plato_detail

_LOGGER = logging.getLogger(__name__)

CARD_URL_PATH = "/nutriplan_bridge_files/nutriplan-bridge-card.js"

SERVICE_MARK_CHAT_READ = "marcar_chat_leido"
SERVICE_RATE_DISH = "valorar_plato"
SERVICE_PLATO_OPTIONS = "opciones_plato"
SERVICE_CHANGE_PLATO = "cambiar_plato"

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

CHANGE_PLATO_SCHEMA = vol.Schema(
    {
        **_ENTRY_SCHEMA,
        # Types matter here, not just values: the decompiled mutation call
        # explicitly wraps planId and dieta in Number(...) before sending,
        # but passes currentId straight through unconverted - and a real
        # subingesta "id" is confirmed to be a numeric STRING
        # ("14256879280"), not a number. Coercing it to an int (as this
        # schema originally did) sends DietoPro's backend a type it doesn't
        # actually send itself, which is a plausible cause of the 500 seen
        # testing this live. platoId is likewise sent unconverted, but a
        # real plato "id" (the size/talla variant) is a genuine number, so
        # that one stays coerced to int.
        vol.Required("plan_id"): cv.positive_int,
        vol.Required("dieta"): cv.positive_int,
        vol.Required("franja"): cv.string,
        vol.Required("subingesta_id"): cv.string,
        vol.Required("nuevo_plato_id"): cv.positive_int,
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


async def _async_handle_plato_options(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator = _resolve_coordinator(hass, call)
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
    return {"opciones": options}


async def _async_handle_change_plato(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator = _resolve_coordinator(hass, call)
    try:
        result = await coordinator.client.async_change_plato(
            call.data["plan_id"],
            call.data["dieta"],
            call.data["franja"],
            call.data["subingesta_id"],
            call.data["nuevo_plato_id"],
        )
    except (DietoProApiError, DietoProAuthError) as err:
        raise _friendly_error("cambiar el plato", err) from err
    await coordinator.async_request_refresh()
    return {"resultado": result}


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the bundled Lovelace card and the two write services once,
    regardless of how many accounts are configured."""
    global _card_registered, _services_registered
    if not _card_registered:
        www_dir = Path(__file__).parent / "www"
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL_PATH, str(www_dir / "nutriplan-bridge-card.js"), False)]
        )
        add_extra_js_url(hass, f"{CARD_URL_PATH}?v={_integration_version()}")
        _card_registered = True

    if not _services_registered:

        async def _mark_chat_read(call: ServiceCall) -> None:
            await _async_handle_mark_chat_read(hass, call)

        async def _rate_dish(call: ServiceCall) -> None:
            await _async_handle_rate_dish(hass, call)

        async def _plato_options(call: ServiceCall) -> ServiceResponse:
            return await _async_handle_plato_options(hass, call)

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
        raise ConfigEntryAuthFailed(str(err)) from err

    interval = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    coordinator = DietoProDataUpdateCoordinator(hass, client, interval)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

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
