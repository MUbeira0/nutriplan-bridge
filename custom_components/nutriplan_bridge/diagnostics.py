"""Diagnostics: a redacted snapshot to attach to bug reports (Settings ->
Devices & services -> Nutriplan Bridge -> Download diagnostics)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import _pick_current_plan_id, dieta_index_for, plan_start_date
from .sensor import MEAL_SLOT_KEYS, _plato_detail

TO_REDACT = {
    "email", "password", "dni", "address", "phone", "mobilePhone", "fullname",
    "firstName", "familyName", "token", "refresh_token", "avatar", "titulacion",
    "numColegiacion", "username",
}


def _shape(value: Any, depth: int = 0) -> Any:
    """Structure only (keys + list lengths), no values - for the sections
    that are large or sensitive (health survey, chat)."""
    if isinstance(value, dict):
        return {k: _shape(v, depth + 1) for k, v in value.items()} if depth < 3 else "{...}"
    if isinstance(value, list):
        return [f"list[{len(value)}]", _shape(value[0], depth + 1)] if value and depth < 3 else f"list[{len(value)}]"
    return type(value).__name__


def _unnamed_platos(dietas: list) -> list[dict]:
    """Structure (keys, no personal values) of platos that still end up with no
    name, to see what the API sends for them."""
    out: list[dict] = []
    for index, dieta in enumerate(dietas):
        for slot in MEAL_SLOT_KEYS:
            ingesta = dieta.get(slot) if isinstance(dieta, dict) else None
            for sub in (ingesta or {}).get("subingestas") or [] if isinstance(ingesta, dict) else []:
                if isinstance(sub, dict) and not _plato_detail(sub).get("nombre"):
                    plato = sub.get("plato") if isinstance(sub.get("plato"), dict) else {}
                    out.append(
                        {
                            "dieta": index,
                            "franja": slot,
                            "sub_keys": sorted(sub.keys()),
                            "plato_keys": sorted(plato.keys()),
                            "superPlato": type(plato.get("superPlato")).__name__,
                            "ingredientes": len(plato.get("alimentoCantidades") or []),
                        }
                    )
    return out[:10]


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}
    plans = data.get("plans") if isinstance(data.get("plans"), list) else []
    detail = data.get("plan_detail") if isinstance(data.get("plan_detail"), dict) else {}
    dietas = detail.get("dietas") if isinstance(detail.get("dietas"), list) else []
    today = dt_util.now().date()
    start = plan_start_date(detail) if detail else None

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "today": today.isoformat(),
        "weekday": today.weekday(),
        "plans": async_redact_data(plans, TO_REDACT),
        "picked_plan_id": _pick_current_plan_id(plans),
        "plan_detail_id": detail.get("id"),
        "plan_detail_title": detail.get("title"),
        "plan_detail_keys": sorted(detail.keys()),
        "plan_start_date": start.isoformat() if start else None,
        "num_dietas": len(dietas),
        "todays_dieta_index": dieta_index_for(today, len(dietas), start),
        "dieta_keys": [sorted(d.keys()) if isinstance(d, dict) else None for d in dietas[:2]],
        "dieta_horarios": [d.get("nombreHorario") if isinstance(d, dict) else None for d in dietas],
        "unnamed_platos": _unnamed_platos(dietas),
        "cita": async_redact_data(data.get("cita"), TO_REDACT),
        "structure": {k: _shape(v) for k, v in data.items() if k in ("seguimientos", "eni", "chat_messages", "charlas", "ratings")},
    }
