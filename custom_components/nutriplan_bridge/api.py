"""Async client for the DietoPro (dietopro.com) patient API.

The endpoint list, HTTP verbs and JSON field names below are not guesses: they
were extracted by decompiling the app's own compiled bundle
(assets/index.android.bundle, a Hermes-bytecode React Native/RTK-Query app)
with hermes-dec, and cross-checked live against https://dietopro.com (route
existence + allowed HTTP verb only - no real account was used or required).

Confirmed from the decompiled RTK Query API slice (+ live route/verb checks):
- POST /api/login_check            body: FormData(_username, _password)
- GET  /api/paciente/plans          -> JSON array of plan summaries
- GET  /api/paciente/plan           -> current plan detail (no id = latest)
- GET  /api/paciente/plan?id={id}   -> a specific plan's detail
- GET  /api/paciente/cita           -> {"cita": {...} | null}
- GET  /api/paciente/seguimientos   -> JSON array of measurements
- GET  /api/paciente/dietista       -> assigned dietitian object
- GET  /api/paciente/current-user   -> the patient's own profile
- GET  /api/paciente/eni            -> initial nutrition survey status
- GET  /api/paciente/rating         -> ratings the patient has given
- GET  /api/paciente/charla         -> "charlas" (consultation notes/calls)
- GET  /api/paciente/chat/unread    -> unread chat message count
- GET  /api/paciente/chat/messages  -> chat message history
- PUT  /api/paciente/chat/reset-unread     -> marks the chat as read
- PUT  /api/paciente/superplato/{id}/rating -> body {"rating": n}, rate a dish
- GET  /api/paciente/platos?change={platoId}&ingesta={franja} -> alternative
  dishes for a meal slot (traced from the ChangePlatoOptions screen)
- PATCH /api/paciente/plans/{planId} -> body {"currentId", "platoId",
  "ingesta", "dieta"}, swaps a dish (traced the actual useChangePlatoMutation
  call site - see async_change_plato's docstring for the one remaining
  uncertainty around the "dieta" field)
- POST /api/paciente/token/refresh  -> route confirmed to exist (POST-only);
  request/response body assumed to follow gesdinet/jwt-refresh-token-bundle
  defaults since no authenticated session was available to confirm it. If it
  ever stops working the client transparently falls back to a full re-login.
- "/api/paciente/pagos" was guessed from the useGetPagosQuery hook name but
  does not exist (verified live: 404), so payments are NOT implemented.

The backend (Symfony/PHP) serializes JSON in snake_case; the app applies a
recursive camelize() to every response before using it. This client does the
same, so all consumers (coordinator/sensors) can rely on the exact camelCase
field names observed in the decompiled source (e.g. "sentByEmailAt",
"nombreHorario", "pesoGrasa").
"""
from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp

from .const import BASE_URL

_LOGGER = logging.getLogger(__name__)

_SNAKE_RE = re.compile(r"_([a-z0-9])")


def _camelize_key(key: str) -> str:
    return _SNAKE_RE.sub(lambda m: m.group(1).upper(), key)


def camelize(value: Any) -> Any:
    """Recursively convert snake_case dict keys to camelCase, mirroring the app's own camelize()."""
    if isinstance(value, list):
        return [camelize(item) for item in value]
    if isinstance(value, dict):
        return {_camelize_key(k): camelize(v) for k, v in value.items()}
    return value


class DietoProAuthError(Exception):
    """Raised when login/authentication fails."""


class DietoProApiError(Exception):
    """Raised on any other API failure."""


class DietoProApiClient:
    """Handles login, token refresh and requests to the DietoPro API."""

    def __init__(self, session: aiohttp.ClientSession, email: str, password: str) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._token: str | None = None
        self._refresh_token: str | None = None

    async def async_login(self) -> None:
        """Log in with email/password (mirrors the app: multipart FormData, not JSON)."""
        form = aiohttp.FormData()
        form.add_field("_username", self._email)
        form.add_field("_password", self._password)
        try:
            async with self._session.post(f"{BASE_URL}/api/login_check", data=form) as resp:
                if resp.status != 200:
                    raise DietoProAuthError(f"Login failed with status {resp.status}")
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise DietoProApiError(f"Cannot reach {BASE_URL}: {err}") from err

        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            raise DietoProAuthError("Login response did not contain a token")

        self._token = token
        self._refresh_token = data.get("refresh_token")

    async def _async_refresh(self) -> bool:
        """Try to refresh the JWT. Returns True on success."""
        if not self._refresh_token:
            return False
        try:
            async with self._session.post(
                f"{BASE_URL}/api/paciente/token/refresh",
                json={"refresh_token": self._refresh_token},
            ) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json(content_type=None)
        except aiohttp.ClientError:
            return False

        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            return False
        self._token = token
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        return True

    async def _async_request(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self._token:
            await self.async_login()

        url = f"{BASE_URL}{path}"
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {self._token}"}
            try:
                async with self._session.request(method, url, headers=headers, **kwargs) as resp:
                    if resp.status == 401 and attempt == 0:
                        if not await self._async_refresh():
                            await self.async_login()
                        continue
                    if resp.status == 404:
                        # Route exists conceptually but there is nothing to return for this account
                        return None
                    if resp.status >= 400:
                        text = await resp.text()
                        raise DietoProApiError(f"{method} {path} -> {resp.status}: {text[:200]}")
                    if resp.content_type == "application/json":
                        data = await resp.json(content_type=None)
                        return camelize(data)
                    return await resp.text()
            except aiohttp.ClientError as err:
                raise DietoProApiError(f"Cannot reach {BASE_URL}{path}: {err}") from err

        raise DietoProAuthError(f"{method} {path}: still unauthorized after re-login")

    # --- Endpoints used by this integration -----------------------------------
    async def async_get_plans(self) -> Any:
        """List of plan summaries (id, created, sentByEmailAt, title)."""
        return await self._async_request("GET", "/api/paciente/plans")

    async def async_get_plan_detail(self, plan_id: Any = None) -> Any:
        """Full plan detail, including the nested 'dietas' (daily meal schedules)."""
        path = "/api/paciente/plan"
        if plan_id is not None:
            path = f"{path}?id={plan_id}"
        return await self._async_request("GET", path)

    async def async_get_cita(self) -> Any:
        """Next appointment - returns {"cita": {...} | None}."""
        return await self._async_request("GET", "/api/paciente/cita")

    async def async_get_seguimientos(self) -> Any:
        """List of body-composition measurements (peso, imc, pesoGrasa, ...)."""
        return await self._async_request("GET", "/api/paciente/seguimientos")

    async def async_get_dietista(self) -> Any:
        return await self._async_request("GET", "/api/paciente/dietista")

    async def async_get_current_user(self) -> Any:
        """The patient's own profile."""
        return await self._async_request("GET", "/api/paciente/current-user")

    async def async_get_eni(self) -> Any:
        """Status of the initial nutrition survey (onboarding wizard)."""
        return await self._async_request("GET", "/api/paciente/eni")

    async def async_get_ratings(self) -> Any:
        """Ratings the patient has given to dishes."""
        return await self._async_request("GET", "/api/paciente/rating")

    async def async_get_charlas(self) -> Any:
        """Consultation notes/calls with the dietista."""
        return await self._async_request("GET", "/api/paciente/charla")

    async def async_get_chat_unread(self) -> Any:
        return await self._async_request("GET", "/api/paciente/chat/unread")

    async def async_get_chat_messages(self) -> Any:
        return await self._async_request("GET", "/api/paciente/chat/messages")

    async def async_mark_chat_read(self) -> Any:
        return await self._async_request("PUT", "/api/paciente/chat/reset-unread")

    async def async_rate_dish(self, super_plato_id: Any, rating: float) -> Any:
        """Rate a dish (1-5). super_plato_id is the "id" exposed on each
        plato in the comidas_hoy sensor's "comidas" attribute."""
        return await self._async_request(
            "PUT",
            f"/api/paciente/superplato/{super_plato_id}/rating",
            json={"rating": rating},
        )

    async def async_get_plato_options(self, plato_id: Any, franja: str) -> Any:
        """Alternative dishes available to swap into a given meal slot.

        Confirmed from the decompiled ChangePlatoOptions screen: builds the
        URL by hand as "?change={plato_id}&ingesta={franja}" (not a JSON
        body), where plato_id is the CURRENT dish's own "plato_id" (the
        specific size/talla variant id exposed per plato in comidas_hoy,
        not "super_plato_id").
        """
        return await self._async_request("GET", f"/api/paciente/platos?change={plato_id}&ingesta={franja}")

    async def async_change_plato(
        self, plan_id: int, dieta: int, franja: str, current_subingesta_id: str, new_plato_id: int
    ) -> Any:
        """Swap a dish in the live plan for another one.

        Confirmed by tracing the actual useChangePlatoMutation() call site in
        the decompiled ChangePlatoOptions screen: PATCH /api/paciente/plans/
        {plan_id} with body {"currentId", "platoId", "ingesta", "dieta"} (the
        mutation argument minus "planId", which goes in the URL - literally
        omit(args, ["planId"]) in the app's own code).

        Types matter, not just field names: the app's own code wraps planId
        and dieta in Number(...) before sending, but passes currentId and
        platoId through unconverted. A real subingesta "id" is a numeric
        STRING ("14256879280"), while a real plato "id" (the size/talla
        variant) is a genuine number - so current_subingesta_id must stay a
        str and new_plato_id an int, matching what the app itself would
        naturally send. Sending currentId as a number (this client's first
        version coerced it) is a plausible cause of a real 500 seen testing
        this live - DietoPro's backend erroring outright rather than
        rejecting the request, consistent with a type mismatch.

        - current_subingesta_id: the dish being replaced - "subingesta_id"
          on the plato entry in comidas_hoy's "comidas" attribute.
        - new_plato_id: the replacement - "plato_id" on one of the options
          returned by async_get_plato_options().
        - dieta: 0-6 weekday index (Monday=0), exposed by comidas_hoy as
          "dieta_index". Also doubles as the index used to pick which of
          plan.dietas[] applies to today (see sensor.py's
          _todays_dieta_indexed) - both derived from this same mutation's
          "dieta" argument, so they're guaranteed to stay consistent with
          each other even though this specific field's semantics were only
          inferred from the decompiled call site, not a real multi-dieta
          response. This mutates your live plan - double check the result
          in the app the first time you use it.
        """
        return await self._async_request(
            "PATCH",
            f"/api/paciente/plans/{plan_id}",
            json={
                "currentId": current_subingesta_id,
                "platoId": new_plato_id,
                "ingesta": franja,
                "dieta": dieta,
            },
        )
