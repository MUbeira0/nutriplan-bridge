"""The card is registered as a Lovelace resource (loaded by the frontend itself)."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import custom_components.nutriplan_bridge as integration

URL = "/nutriplan_bridge_files/v0.32.0/nutriplan-bridge-card.js"


def make(items, mode="storage", as_dict=True):
    resources = MagicMock()
    resources.async_get_info = AsyncMock()
    resources.async_items = MagicMock(return_value=items)
    resources.async_create_item = AsyncMock()
    resources.async_delete_item = AsyncMock()
    data = {"mode": mode, "resources": resources} if as_dict else MagicMock(mode=mode, resources=resources)
    hass = MagicMock()
    hass.data = {"lovelace": data}
    return hass, resources


def run(hass):
    return asyncio.run(integration._async_register_lovelace_resource(hass, URL))


def test_creates_the_resource_once():
    hass, res = make([{"id": "a", "type": "module", "url": "/local/other-card.js"}])
    assert run(hass) is True
    res.async_create_item.assert_awaited_once_with({"res_type": "module", "url": URL})
    res.async_delete_item.assert_not_called()


def test_is_idempotent():
    hass, res = make([{"id": "a", "type": "module", "url": URL}])
    assert run(hass) is True
    res.async_create_item.assert_not_called()


def test_replaces_an_older_versioned_copy_but_never_touches_the_users_own_entries():
    hass, res = make(
        [
            {"id": "old", "type": "module", "url": "/nutriplan_bridge_files/v0.31.0/nutriplan-bridge-card.js"},
            {"id": "manual", "type": "module", "url": "/nutriplan_bridge_files/nutriplan-bridge-card.js"},  # added by hand
            {"id": "other", "type": "module", "url": "/hacsfiles/some-card.js"},
        ]
    )
    assert run(hass) is True
    res.async_delete_item.assert_awaited_once_with("old")
    res.async_create_item.assert_awaited_once()


def test_attribute_style_lovelace_data_also_works():
    hass, res = make([], as_dict=False)
    assert run(hass) is True
    res.async_create_item.assert_awaited_once()


def test_yaml_mode_cannot_be_edited_so_the_caller_falls_back():
    hass, res = make([], mode="yaml")
    del res.async_create_item  # a YAML resource collection has no create method
    assert run(hass) is False


def test_works_when_the_mode_field_is_missing_as_on_a_recent_home_assistant():
    hass, res = make([], mode=None, as_dict=False)
    assert run(hass) is True
    res.async_create_item.assert_awaited_once()


def test_no_lovelace_at_all():
    hass = MagicMock()
    hass.data = {}
    assert run(hass) is False


def test_a_failure_never_breaks_setup():
    hass, res = make([])
    res.async_create_item = AsyncMock(side_effect=RuntimeError("boom"))
    assert run(hass) is False
