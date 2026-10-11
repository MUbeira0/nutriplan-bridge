"""API client behaviour with a fake aiohttp session (no network)."""
import asyncio
import json

import aiohttp
import pytest

from custom_components.nutriplan_bridge import api


class FakeResp:
    def __init__(self, status, payload=None, ctype="application/json"):
        self.status = status
        self._payload = payload
        self.content_type = ctype

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kw):
        self.calls.append(("POST", url))
        return self.responses.pop(0)

    def request(self, method, url, **kw):
        self.calls.append((method, url))
        return self.responses.pop(0)


def run(coro):
    return asyncio.run(coro)


def client(responses):
    session = FakeSession(responses)
    return api.DietoProApiClient(session, "a@b.c", "pw"), session


LOGIN_OK = FakeResp(200, {"token": "t", "refresh_token": "r"})


def test_camelize_is_recursive():
    assert api.camelize({"sent_by_email_at": [{"peso_grasa": 1}]}) == {"sentByEmailAt": [{"pesoGrasa": 1}]}


def test_login_ok_sets_token():
    c, _ = client([LOGIN_OK])
    run(c.async_login())
    assert c._token == "t"


@pytest.mark.parametrize("status", [400, 401, 403])
def test_login_bad_credentials_is_auth_error(status):
    c, _ = client([FakeResp(status)])
    with pytest.raises(api.DietoProAuthError):
        run(c.async_login())


@pytest.mark.parametrize("status", [500, 502, 503, 429])
def test_login_server_trouble_is_not_auth_error(status):
    c, _ = client([FakeResp(status)])
    with pytest.raises(api.DietoProApiError) as exc:
        run(c.async_login())
    assert not isinstance(exc.value, api.DietoProAuthError)


def test_get_404_means_nothing_to_return():
    c, _ = client([LOGIN_OK, FakeResp(404)])
    assert run(c.async_get_cita()) is None


def test_write_404_is_an_error_not_silent_success():
    c, _ = client([LOGIN_OK, FakeResp(404, {"error": "x"})])
    with pytest.raises(api.DietoProApiError):
        run(c.async_change_plato(1, 0, "comida", 2, 3))


def test_any_json_content_type_is_parsed_and_camelized():
    c, _ = client([LOGIN_OK, FakeResp(200, {"sent_by_email_at": "x"}, "application/problem+json")])
    assert run(c.async_get_cita()) == {"sentByEmailAt": "x"}


def test_401_triggers_refresh_then_retry():
    c, session = client(
        [LOGIN_OK, FakeResp(401), FakeResp(200, {"token": "t2"}), FakeResp(200, {"cita": None})]
    )
    assert run(c.async_get_cita()) == {"cita": None}
    assert c._token == "t2"
    assert ("POST", "https://dietopro.com/api/paciente/token/refresh") in session.calls


def test_401_with_failed_refresh_falls_back_to_login():
    c, session = client([LOGIN_OK, FakeResp(401), FakeResp(401), LOGIN_OK, FakeResp(200, {"cita": None})])
    assert run(c.async_get_cita()) == {"cita": None}
    assert [m for m, _ in session.calls].count("POST") == 3  # login, refresh, login again


def test_connection_error_is_wrapped():
    class Boom:
        def post(self, *a, **k):
            raise aiohttp.ClientError("down")

    c = api.DietoProApiClient(Boom(), "a", "b")
    with pytest.raises(api.DietoProApiError):
        run(c.async_login())
