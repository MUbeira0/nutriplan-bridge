"""Manual body measurement + photos/documents to the chat."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

import custom_components.nutriplan_bridge as integration
from custom_components.nutriplan_bridge import api, sensor
from custom_components.nutriplan_bridge.api import DietoProApiError

from .test_api import LOGIN_OK, FakeResp, client, run


class Call:
    def __init__(self, d):
        self.data = d


def hass_with(data, client_mock):
    h, c = MagicMock(), MagicMock()
    c.data = data
    c.client = client_mock
    c.async_request_refresh = AsyncMock()
    h.data = {"nutriplan_bridge": {"abc": c}}
    return h, c


def go(coro):
    return asyncio.run(coro)


def measurement_client(open_value):
    return MagicMock(
        async_get_open_seguimiento=AsyncMock(return_value=open_value),
        async_put_seguimiento_step=AsyncMock(return_value={}),
        async_finish_seguimiento=AsyncMock(return_value={}),
    )


# ----------------------------------------------------------------------------- API layer
def test_open_measurement_get_and_404_means_none():
    c, session = client([LOGIN_OK, FakeResp(200, {"id": 9}), FakeResp(404)])
    assert run(c.async_get_open_seguimiento()) == {"id": 9}
    assert session.calls[-1] == ("GET", "https://dietopro.com/api/paciente/seguimiento")
    assert run(c.async_get_open_seguimiento()) is None


def test_step_is_a_put_with_the_field_as_key():
    c, session = client([LOGIN_OK, FakeResp(200, {})])
    run(c.async_put_seguimiento_step(9, "peso", 91.2))
    assert session.calls[-1] == ("PUT", "https://dietopro.com/api/paciente/seguimientos/9/step")
    assert session.last_kwargs["json"] == {"peso": 91.2}


def test_finish_is_a_patch_without_body():
    c, session = client([LOGIN_OK, FakeResp(200, {})])
    run(c.async_finish_seguimiento())
    assert session.calls[-1] == ("PATCH", "https://dietopro.com/api/paciente/finish/seguimiento")
    assert "json" not in session.last_kwargs


def test_chat_file_is_multipart_to_the_right_endpoint_and_rebuilt_on_each_attempt():
    c, session = client([LOGIN_OK, FakeResp(401), FakeResp(200, {"token": "t2"}), FakeResp(200, {})])
    run(c.async_send_chat_file("images", "comida.jpg", b"\xff\xd8abc", "image/jpeg", "mi comida"))
    assert session.calls[-1] == ("POST", "https://dietopro.com/api/paciente/chat/images")
    # a multipart body can only be read once, so a callable is passed and called per attempt
    assert hasattr(session.last_kwargs["data"], "_fields")  # a real, fresh aiohttp.FormData
    names = [f[0]["name"] for f in session.last_kwargs["data"]._fields]
    assert names == ["text", "file"]


# ----------------------------------------------------------------------------- services
def test_open_measurement_service(data):
    h, _ = hass_with(data, measurement_client({"id": 55}))
    assert go(integration._async_handle_open_measurement(h, Call({})))["abierta"] is True
    h, _ = hass_with(data, measurement_client(None))
    assert go(integration._async_handle_open_measurement(h, Call({})))["abierta"] is False
    h, _ = hass_with(data, measurement_client({"seguimiento": {"id": 7}}))
    assert go(integration._async_handle_open_measurement(h, Call({})))["id"] == 7  # wrapped shape too


def test_register_sends_each_field_in_the_apps_order_then_finishes(data):
    cl = measurement_client({"id": 55})
    h, c = hass_with(data, cl)
    res = go(
        integration._async_handle_register_measurement(
            h, Call({"perimetro_cintura": 100.0, "peso": 91.24, "porcentaje_grasa": 30.0, "finalizar": True})
        )
    )
    sent = [(a.args[1], a.args[2]) for a in cl.async_put_seguimiento_step.await_args_list]
    assert sent == [("peso", 91.24), ("porcentajeGrasa", 30.0), ("perimetroCintura", 100.0)]
    assert all(a.args[0] == 55 for a in cl.async_put_seguimiento_step.await_args_list)
    cl.async_finish_seguimiento.assert_awaited_once()
    assert res == {"id": 55, "enviados": {"peso": 91.24, "porcentaje_grasa": 30.0, "perimetro_cintura": 100.0}, "finalizada": True}
    c.async_request_refresh.assert_awaited_once()


def test_register_without_finishing(data):
    cl = measurement_client({"id": 55})
    h, _ = hass_with(data, cl)
    res = go(integration._async_handle_register_measurement(h, Call({"peso": 90.0, "finalizar": False})))
    cl.async_finish_seguimiento.assert_not_called()
    assert res["finalizada"] is False


def test_register_needs_an_open_measurement(data):
    cl = measurement_client(None)
    h, _ = hass_with(data, cl)
    with pytest.raises(HomeAssistantError) as exc:
        go(integration._async_handle_register_measurement(h, Call({"peso": 90.0, "finalizar": True})))
    assert "abierta" in str(exc.value)
    cl.async_put_seguimiento_step.assert_not_called()


@pytest.mark.parametrize("payload", [{"finalizar": True}, {"peso": 5000.0, "finalizar": True}, {"porcentaje_grasa": 0.2, "finalizar": True}])
def test_register_rejects_empty_or_absurd_values_before_touching_the_api(data, payload):
    cl = measurement_client({"id": 55})
    h, _ = hass_with(data, cl)
    with pytest.raises(HomeAssistantError):
        go(integration._async_handle_register_measurement(h, Call(payload)))
    cl.async_get_open_seguimiento.assert_not_called()


def test_register_reports_what_was_saved_when_a_step_fails(data):
    cl = measurement_client({"id": 55})
    cl.async_put_seguimiento_step = AsyncMock(side_effect=[{}, DietoProApiError("PUT -> 422")])
    h, _ = hass_with(data, cl)
    with pytest.raises(HomeAssistantError) as exc:
        go(integration._async_handle_register_measurement(h, Call({"peso": 90.0, "porcentaje_grasa": 30.0, "finalizar": True})))
    assert "porcentaje_grasa" in str(exc.value) and "peso" in str(exc.value)
    cl.async_finish_seguimiento.assert_not_called()


def test_register_if_closing_fails_the_values_are_still_reported_as_saved(data):
    cl = measurement_client({"id": 55})
    cl.async_finish_seguimiento = AsyncMock(side_effect=DietoProApiError("PATCH -> 500"))
    h, _ = hass_with(data, cl)
    with pytest.raises(HomeAssistantError) as exc:
        go(integration._async_handle_register_measurement(h, Call({"peso": 90.0, "finalizar": True})))
    assert "no se pudo cerrar" in str(exc.value)


# ----------------------------------------------------------------------------- chat files
@pytest.mark.parametrize(
    "name,ctype,kind",
    [("a.JPG", None, "images"), ("a.png", None, "images"), ("x.heic", None, "images"), ("n", "image/webp", "images"), ("a.pdf", None, "documents"), ("a.docx", "application/x", "documents")],
)
def test_chat_file_kind(name, ctype, kind):
    assert sensor.chat_file_kind(name, ctype) == kind


def test_chat_file_service_reads_an_allowed_path_and_sends_it(data, tmp_path):
    f = tmp_path / "comida.jpg"
    f.write_bytes(b"\xff\xd8data")
    cl = MagicMock(async_send_chat_file=AsyncMock(return_value={}))
    h, c = hass_with(data, cl)
    h.config.is_allowed_path = lambda p: True
    h.async_add_executor_job = AsyncMock(side_effect=lambda fn: fn())
    res = go(integration._async_handle_chat_file(h, Call({"ruta": str(f), "texto": "mi comida"})))
    cl.async_send_chat_file.assert_awaited_once_with("images", "comida.jpg", b"\xff\xd8data", None, "mi comida")
    assert res == {"enviado": "comida.jpg", "tipo": "images"}


def test_chat_file_service_refuses_paths_outside_the_allowed_dirs(data):
    h, _ = hass_with(data, MagicMock())
    h.config.is_allowed_path = lambda p: False
    with pytest.raises(HomeAssistantError):
        go(integration._async_handle_chat_file(h, Call({"ruta": "/etc/passwd", "texto": ""})))


def test_chat_file_rejects_empty_and_oversized(data):
    h, c = hass_with(data, MagicMock())
    with pytest.raises(HomeAssistantError):
        go(integration._async_send_chat_file(h, c, "a.jpg", b"", None, ""))
    with pytest.raises(HomeAssistantError):
        go(integration._async_send_chat_file(h, c, "a.jpg", b"x" * (sensor.MAX_CHAT_FILE_BYTES + 1), None, ""))
