"""Repairs notices, reauth on bad credentials, and the "eaten" store/sensor."""
import asyncio
import datetime as dtm
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.nutriplan_bridge import coordinator as coord_mod
from custom_components.nutriplan_bridge import eaten, sensor
from custom_components.nutriplan_bridge.api import DietoProApiError, DietoProAuthError

CLIENT_CALLS = (
    "async_get_plans", "async_get_plan_detail", "async_get_cita", "async_get_seguimientos", "async_get_dietista",
    "async_get_current_user", "async_get_eni", "async_get_ratings", "async_get_charlas", "async_get_chat_unread",
    "async_get_chat_messages",
)


def make(client_behaviour):
    client = MagicMock()
    for name in CLIENT_CALLS:
        setattr(client, name, AsyncMock(**client_behaviour(name)))
    # Built without __init__: recent Home Assistant versions insist on a fully set-up
    # core (frame helper, config entry context) just to construct a coordinator, and
    # these tests only exercise _async_update_data's own logic.
    c = object.__new__(coord_mod.DietoProDataUpdateCoordinator)
    c.client, c.hass, c._entry_id, c._failures, c.eaten, c.data = client, MagicMock(), "abc", 0, None, None
    return c


@pytest.fixture
def issues(monkeypatch):
    create, delete = MagicMock(), MagicMock()
    monkeypatch.setattr(coord_mod.ir, "async_create_issue", create)
    monkeypatch.setattr(coord_mod.ir, "async_delete_issue", delete)
    return create, delete


def run(c):
    return asyncio.run(c._async_update_data())


def test_bad_credentials_start_the_reauth_flow(issues):
    c = make(lambda n: {"side_effect": DietoProAuthError("401")} if n == "async_get_plans" else {"return_value": {}})
    with pytest.raises(ConfigEntryAuthFailed):
        run(c)


def test_unreachable_notice_only_after_three_failed_refreshes_in_a_row(issues):
    create, delete = issues
    c = make(lambda n: {"side_effect": DietoProApiError("down")})
    for attempt in range(1, 4):
        with pytest.raises(UpdateFailed):
            run(c)
        assert create.call_count == (1 if attempt >= 3 else 0)
    args, kwargs = create.call_args
    assert args[2] == "api_unreachable_abc" and kwargs["translation_key"] == "api_unreachable"
    assert kwargs["translation_placeholders"] == {"failures": "3"} and kwargs["is_fixable"] is False


def test_notice_is_cleared_when_data_comes_back(issues):
    create, delete = issues
    state = {"down": True}

    def behaviour(name):
        return {"side_effect": DietoProApiError("down")} if state["down"] else {"return_value": {"id": 1, "dietas": []} if name == "async_get_plan_detail" else []}

    c = make(behaviour)
    for _ in range(3):
        with pytest.raises(UpdateFailed):
            run(c)
    state["down"] = False
    for name in CLIENT_CALLS:
        getattr(c.client, name).side_effect = None
        getattr(c.client, name).return_value = {"id": 1, "dietas": []} if name == "async_get_plan_detail" else []
    run(c)
    assert any(call.args[2] == "api_unreachable_abc" for call in delete.call_args_list)
    assert c._failures == 0


def test_plan_without_dietas_raises_a_format_notice(issues):
    create, delete = issues
    c = make(lambda n: {"return_value": {"id": 1} if n == "async_get_plan_detail" else []})
    run(c)
    assert any(call.args[2] == "plan_format_abc" for call in create.call_args_list)


def test_normal_plan_does_not_raise_a_format_notice(issues):
    create, delete = issues
    c = make(lambda n: {"return_value": {"id": 1, "dietas": []} if n == "async_get_plan_detail" else []})
    run(c)
    assert not any(call.args[2] == "plan_format_abc" for call in create.call_args_list)


# ----------------------------------------------------------------- eaten store + sensor
class FakeStore:
    def __init__(self, *a, **k):
        self.saved = None

    async def async_load(self):
        return {"days": {"2026-10-12": ["desayuno"], "2000-01-01": ["cena"]}}

    def async_delay_save(self, data_func, delay):
        self.saved = data_func()


def test_store_loads_prunes_marks_notifies_and_saves(monkeypatch):
    monkeypatch.setattr(eaten, "Store", FakeStore)
    store = eaten.EatenStore(MagicMock(), "abc")
    asyncio.run(store.async_load())
    assert store.get(dtm.date(2026, 10, 12)) == {"desayuno"}
    assert store.get(dtm.date(2000, 1, 1)) == set()  # older than KEEP_DAYS -> dropped

    seen = []
    off = store.add_listener(lambda: seen.append(1))
    store.mark(dtm.date(2026, 10, 12), "comida", True)
    assert store.get(dtm.date(2026, 10, 12)) == {"desayuno", "comida"} and seen == [1]
    assert store._store.saved == {"days": {"2026-10-12": ["comida", "desayuno"]}}
    store.mark(dtm.date(2026, 10, 12), "desayuno", False)
    store.mark(dtm.date(2026, 10, 12), "comida", False)
    assert store.get(dtm.date(2026, 10, 12)) == set()
    assert store._store.saved == {"days": {}}
    off()
    store.mark(dtm.date(2026, 10, 12), "cena", True)
    assert len(seen) == 3  # no more notifications after unsubscribe


def test_eaten_sensor_counts_and_adherence(coord, entry, monkeypatch):
    monkeypatch.setattr(eaten, "Store", FakeStore)
    store = eaten.EatenStore(MagicMock(), "abc")
    store._days = {}
    coord.eaten = store
    # Monday 12/10/2026 plan, 3 planned meals/day (desayuno, comida, cena), 700+300+400 kcal
    store.mark(dtm.date(2026, 10, 12), "desayuno", True)
    store.mark(dtm.date(2026, 10, 12), "comida", True)
    store.mark(dtm.date(2026, 10, 12), "tentempie1", True)  # not in the plan -> ignored
    s = sensor.DietoProEatenSensor(coord, entry)
    assert s.native_value == 2
    a = s.extra_state_attributes
    assert a["tomadas"] == ["comida", "desayuno"]
    assert a["planificadas"] == ["desayuno", "comida", "cena"]
    assert a["kcal_tomadas"] == 1000 and a["kcal_planificadas"] == 1400
    assert a["cumplimiento_hoy"] == 67
    # last 7 days: the plan repeats weekly, so 7 days x 3 meals = 21 planned
    assert a["planificadas_semana"] == 21 and a["tomadas_semana"] == 2 and a["cumplimiento_semana"] == 10
    assert a["historial"]["2026-10-12"] == {"tomadas": 2, "planificadas": 3}


def test_eaten_sensor_without_store_is_harmless(coord, entry):
    s = sensor.DietoProEatenSensor(coord, entry)
    assert s.native_value == 0 and s.extra_state_attributes["cumplimiento_hoy"] == 0


def test_mark_eaten_service_validates_the_slot(data):
    import custom_components.nutriplan_bridge as integration
    from homeassistant.exceptions import HomeAssistantError

    h, c = MagicMock(), MagicMock()
    c.data = data
    c.eaten = MagicMock()
    h.data = {"nutriplan_bridge": {"abc": c}}

    class Call:
        def __init__(self, d):
            self.data = d

    asyncio.run(integration._async_handle_mark_eaten(h, Call({"franja": "comida", "tomada": True})))
    c.eaten.mark.assert_called_once_with(dtm.date(2026, 10, 12), "comida", True)
    with pytest.raises(HomeAssistantError):
        asyncio.run(integration._async_handle_mark_eaten(h, Call({"franja": "brunch", "tomada": True})))
