"""Data update coordinator for DietoPro."""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DietoProApiClient, DietoProApiError, DietoProAuthError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


_TITLE_DATE_RE = re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})")
_PLAN_START_KEYS = ("startDate", "fechaInicio", "inicio", "start", "fechaDesde", "desde")


def plan_start_date(plan: dict) -> date | None:
    """The day a plan starts applying.

    A plan's "created"/"sentByEmailAt" are when the dietitian wrote/sent it,
    which is usually BEFORE it takes effect (a plan titled "12/10/2026" is
    typically sent days earlier). The title carries the real start date
    ("Plan dietético semanal de X 12/10/2026"), so: an explicit start field if
    the API ever sends one, else the dd/mm/yyyy in the title, else the
    creation date.
    """
    for key in _PLAN_START_KEYS:
        parsed = _as_date(plan.get(key))
        if parsed:
            return parsed
    match = _TITLE_DATE_RE.search(str(plan.get("title") or ""))
    if match:
        day, month, year = (int(g) for g in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            pass
    return _as_date(plan.get("created"))


def _as_date(value: object) -> date | None:
    if not value:
        return None
    parsed = dt_util.parse_datetime(str(value))
    if parsed is not None:
        return parsed.date()
    return dt_util.parse_date(str(value))


def unread_count(data: dict | None) -> int:
    """Unread chat messages out of the raw coordinator data."""
    raw = (data or {}).get("chat_unread")
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, dict):
        for key in ("unread", "count", "total", "unreadCount"):
            if isinstance(raw.get(key), (int, float)):
                return int(raw[key])
    return 0


def dieta_index_for(day: date, count: int, start: date | None) -> int | None:
    """Which entry of plan.dietas applies on a given date.

    The "dieta" index is a weekday index (Monday=0) for the usual 7-dieta
    weekly plan. A plan with 14/21/... dietas spans several weeks, so there
    the index must advance week by week from the plan's start (counting from
    the Monday of that start week) - using only the weekday meant the second
    week of such a plan could never be shown.
    """
    if count <= 0:
        return None
    if count == 1:
        return 0
    if count > 7 and count % 7 == 0 and start is not None:
        monday = start - timedelta(days=start.weekday())
        return (day - monday).days % count
    return day.weekday() % count


def _pick_current_plan_id(plans: list) -> object | None:
    """The plan that applies TODAY: among the plans already sent to the
    patient, the one with the latest start date that is not in the future.

    The previous logic ("most recently sent") picked next week's plan as soon
    as it was sent, days before it took effect. If nothing has started yet,
    fall back to the soonest upcoming one; if no plan has a usable date, to
    the app's own fallback (latest sent, else the first in the list).
    """
    plans = [p for p in plans if isinstance(p, dict) and p.get("id") is not None]
    if not plans:
        return None

    # No longer restricted to plans "sent by email": a plan published
    # in-app without an email has no sentByEmailAt, and filtering on it kept
    # the previous week's plan on screen after the new one took effect.
    today = dt_util.now().date()
    dated = [(plan_start_date(p), p) for p in plans]
    dated = [(d, p) for d, p in dated if d is not None]
    if dated:
        started = [(d, p) for d, p in dated if d <= today]
        if started:
            return max(started, key=lambda item: (item[0], str(item[1].get("sentByEmailAt") or "")))[1]["id"]
        return min(dated, key=lambda item: item[0])[1]["id"]

    with_sent = [p for p in plans if p.get("sentByEmailAt")]
    if with_sent:
        with_sent.sort(key=lambda p: p["sentByEmailAt"], reverse=True)
        return with_sent[0]["id"]
    return plans[0]["id"]


class DietoProDataUpdateCoordinator(DataUpdateCoordinator[dict]):
    """Fetches plan (+ detail), next appointment, seguimientos and dietista."""

    def __init__(
        self, hass: HomeAssistant, client: DietoProApiClient, update_interval_minutes: int, entry_id: str | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="DietoPro",
            update_interval=timedelta(minutes=update_interval_minutes),
        )
        self.client = client
        self._entry_id = entry_id

    def _fire_change_events(self, new: dict) -> None:
        """Bus events for automations: a different plan took effect, a new
        chat message arrived, or the appointment changed. Only fired when
        there is previous data to compare with (never on the first load)."""
        old = self.data
        if not old or not self._entry_id:
            return
        base = {"entry_id": self._entry_id}

        def plan(d: dict) -> dict:
            detail = d.get("plan_detail")
            return detail if isinstance(detail, dict) else {}

        old_id, new_id = plan(old).get("id"), plan(new).get("id")
        if old_id is not None and new_id is not None and old_id != new_id:
            self.hass.bus.async_fire(
                f"{DOMAIN}_plan_changed",
                {**base, "plan_id": new_id, "previous_plan_id": old_id, "title": plan(new).get("title")},
            )
        if unread_count(new) > unread_count(old):
            self.hass.bus.async_fire(f"{DOMAIN}_new_message", {**base, "unread": unread_count(new)})
        if new.get("cita") != old.get("cita") and new.get("cita"):
            self.hass.bus.async_fire(f"{DOMAIN}_appointment_changed", {**base, "cita": new.get("cita")})

    async def _async_update_data(self) -> dict:
        results: dict = {}

        try:
            results["plans"] = await self.client.async_get_plans()
        except DietoProAuthError as err:
            raise UpdateFailed(f"Authentication with DietoPro failed: {err}") from err
        except DietoProApiError as err:
            _LOGGER.warning("DietoPro: could not fetch plans: %s", err)
            results["plans"] = None

        plan_id = _pick_current_plan_id(results.get("plans") or [])
        try:
            results["plan_detail"] = await self.client.async_get_plan_detail(plan_id)
        except (DietoProApiError, DietoProAuthError) as err:
            _LOGGER.debug("DietoPro: could not fetch plan detail: %s", err)
            results["plan_detail"] = None

        for key, coro in (
            ("cita", self.client.async_get_cita()),
            ("seguimientos", self.client.async_get_seguimientos()),
            ("dietista", self.client.async_get_dietista()),
            ("current_user", self.client.async_get_current_user()),
            ("eni", self.client.async_get_eni()),
            ("ratings", self.client.async_get_ratings()),
            ("charlas", self.client.async_get_charlas()),
            ("chat_unread", self.client.async_get_chat_unread()),
            ("chat_messages", self.client.async_get_chat_messages()),
        ):
            try:
                results[key] = await coro
            except DietoProAuthError as err:
                raise UpdateFailed(f"Authentication with DietoPro failed: {err}") from err
            except DietoProApiError as err:
                _LOGGER.warning("DietoPro: could not fetch %s: %s", key, err)
                results[key] = None

        if all(value is None for value in results.values()):
            # Every endpoint failed (DietoPro down, network cut...): without
            # this the update "succeeded" with all-None data, so sensors read
            # unknown and the card claimed "Sin plan activo" / "Sin comidas".
            raise UpdateFailed("DietoPro did not return data for any endpoint")

        self._fire_change_events(results)
        return results
