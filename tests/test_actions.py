"""Write/lookup actions (chat, appointments) with a mocked API client."""
import asyncio
import datetime as dtm
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

import custom_components.nutriplan_bridge as integration
from custom_components.nutriplan_bridge import sensor
from custom_components.nutriplan_bridge.api import DietoProApiError


class Call:
    def __init__(self, d):
        self.data = d


def hass_with(data, client=None):
    h, c = MagicMock(), MagicMock()
    c.data = data
    c.client = client or MagicMock()
    c.async_request_refresh = AsyncMock()
    h.data = {"nutriplan_bridge": {"abc": c}}
    return h, c


def run(coro):
    return asyncio.run(coro)


def test_normalize_slots_dict_of_times():
    out = sensor.normalize_slots({"slots": {"2026-10-13": ["10:00", "10:30"], "2026-10-14": ["09:00"]}})
    assert out == [
        {"fecha": "2026-10-13", "hora": "10:00"},
        {"fecha": "2026-10-13", "hora": "10:30"},
        {"fecha": "2026-10-14", "hora": "09:00"},
    ]


def test_normalize_slots_list_of_objects_keeps_extras():
    out = sensor.normalize_slots({"slots": [{"date": "2026-10-13", "time": "10:00", "duration": 30}]})
    assert out == [{"fecha": "2026-10-13", "hora": "10:00", "extra": {"duration": 30}}]


def test_normalize_slots_empty_and_garbage():
    assert sensor.normalize_slots(None) == []
    assert sensor.normalize_slots({"slots": {}}) == []
    assert sensor.normalize_slots({"slots": "x"}) == []


def test_send_message_calls_api_and_refreshes(data):
    client = MagicMock(async_send_message=AsyncMock())
    h, c = hass_with(data, client)
    run(integration._async_handle_send_message(h, Call({"texto": "hola"})))
    client.async_send_message.assert_awaited_once_with("hola")
    c.async_request_refresh.assert_awaited_once()


def test_send_message_api_error_becomes_friendly_error(data):
    client = MagicMock(async_send_message=AsyncMock(side_effect=DietoProApiError("POST x -> 500: boom")))
    h, _ = hass_with(data, client)
    with pytest.raises(HomeAssistantError) as exc:
        run(integration._async_handle_send_message(h, Call({"texto": "hola"})))
    assert "boom" not in str(exc.value)  # raw API text stays in the log, not the UI


def test_slots_defaults_to_next_seven_days(data):
    client = MagicMock(async_get_available_slots=AsyncMock(return_value={"slots": {"2026-10-13": ["10:00"]}}))
    h, _ = hass_with(data, client)
    res = run(integration._async_handle_slots(h, Call({})))
    client.async_get_available_slots.assert_awaited_once_with("2026-10-12", "2026-10-18")
    assert res["total"] == 1 and res["huecos"][0]["hora"] == "10:00"


def test_book_sends_iso_date_time_and_note(data):
    client = MagicMock(async_request_booking=AsyncMock(return_value={"ok": True}))
    h, c = hass_with(data, client)
    res = run(integration._async_handle_book(h, Call({"fecha": dtm.date(2026, 10, 13), "hora": "17:30", "nota": "x"})))
    client.async_request_booking.assert_awaited_once_with("2026-10-13", "17:30", "x")
    assert res == {"resultado": {"ok": True}}
    c.async_request_refresh.assert_awaited_once()


def test_cancel_uses_next_appointment_id_by_default(data):
    data["cita"]["cita"]["id"] = 55
    client = MagicMock(async_cancel_cita=AsyncMock(return_value={}))
    h, _ = hass_with(data, client)
    res = run(integration._async_handle_cancel_cita(h, Call({})))
    client.async_cancel_cita.assert_awaited_once_with(55)
    assert res["cita_id"] == 55


def test_cancel_without_any_appointment_is_a_clear_error(data):
    data["cita"] = {"cita": None}
    h, _ = hass_with(data, MagicMock())
    with pytest.raises(HomeAssistantError):
        run(integration._async_handle_cancel_cita(h, Call({})))


def test_confirm_with_explicit_id(data):
    client = MagicMock(async_confirm_cita=AsyncMock(return_value={}))
    h, _ = hass_with(data, client)
    run(integration._async_handle_confirm_cita(h, Call({"cita_id": 9})))
    client.async_confirm_cita.assert_awaited_once_with(9)
