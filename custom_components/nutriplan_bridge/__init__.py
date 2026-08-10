"""The Nutriplan Bridge integration."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DietoProApiClient, DietoProApiError, DietoProAuthError
from .const import CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL, DOMAIN, PLATFORMS
from .coordinator import DietoProDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

CARD_URL_PATH = "/nutriplan_bridge_files/nutriplan-bridge-card.js"

_card_registered = False


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


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the bundled Lovelace card once, regardless of how many accounts are configured."""
    global _card_registered
    if not _card_registered:
        www_dir = Path(__file__).parent / "www"
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL_PATH, str(www_dir / "nutriplan-bridge-card.js"), False)]
        )
        add_extra_js_url(hass, f"{CARD_URL_PATH}?v={_integration_version()}")
        _card_registered = True
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
