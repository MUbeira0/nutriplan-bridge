"""Data update coordinator for DietoPro."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DietoProApiClient, DietoProApiError, DietoProAuthError

_LOGGER = logging.getLogger(__name__)


def _pick_current_plan_id(plans: list) -> object | None:
    """Mirror the app's own fallback logic (decompiled from getPlan's queryFn):
    among plans that were sent by email, take the most recent one; otherwise
    just take the first plan in the list.
    """
    if not plans:
        return None
    sent = [p for p in plans if isinstance(p, dict) and p.get("sentByEmailAt")]
    if sent:
        sent.sort(key=lambda p: p["sentByEmailAt"], reverse=True)
        return sent[0].get("id")
    first = plans[0]
    return first.get("id") if isinstance(first, dict) else None


class DietoProDataUpdateCoordinator(DataUpdateCoordinator[dict]):
    """Fetches plan (+ detail), next appointment, seguimientos and dietista."""

    def __init__(self, hass: HomeAssistant, client: DietoProApiClient, update_interval_minutes: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="DietoPro",
            update_interval=timedelta(minutes=update_interval_minutes),
        )
        self.client = client

    async def _async_update_data(self) -> dict:
        results: dict = {}

        try:
            results["plans"] = await self.client.async_get_plans()
        except DietoProAuthError as err:
            raise UpdateFailed(f"Authentication with DietoPro failed: {err}") from err
        except DietoProApiError as err:
            _LOGGER.debug("DietoPro: could not fetch plans: %s", err)
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
        ):
            try:
                results[key] = await coro
            except DietoProAuthError as err:
                raise UpdateFailed(f"Authentication with DietoPro failed: {err}") from err
            except DietoProApiError as err:
                _LOGGER.debug("DietoPro: could not fetch %s: %s", key, err)
                results[key] = None

        return results
