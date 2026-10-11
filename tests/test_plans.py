"""Which plan applies today, and which entry of plan.dietas applies on a date."""
import datetime as dtm

from custom_components.nutriplan_bridge import coordinator as c

D = dtm.date


def test_plan_start_from_title():
    assert c.plan_start_date({"title": "Plan dietético semanal de X 12/10/2026"}) == D(2026, 10, 12)


def test_plan_start_prefers_explicit_field():
    assert c.plan_start_date({"title": "Plan 12/10/2026", "fechaInicio": "2026-10-05"}) == D(2026, 10, 5)


def test_plan_start_falls_back_to_created():
    assert c.plan_start_date({"title": "Sin fecha", "created": "2026-09-01T10:00:00"}) == D(2026, 9, 1)


def test_plan_start_invalid_title_date():
    assert c.plan_start_date({"title": "Plan 45/13/2026", "created": "2026-09-01"}) == D(2026, 9, 1)


def test_pick_ignores_future_plan_and_missing_email_flag(data):
    # Monday 12/10: the plan starting today wins even though it was never
    # "sent by email"; next week's plan (19/10) must not be picked yet.
    assert c._pick_current_plan_id(data["plans"]) == 3


def test_pick_sunday_still_previous_plan(data, monkeypatch):
    import datetime as dtm2
    from homeassistant.util import dt as dt_util

    monkeypatch.setattr(dt_util, "now", lambda tz=None: dtm2.datetime(2026, 10, 11, 12, 0, tzinfo=dt_util.DEFAULT_TIME_ZONE))
    assert c._pick_current_plan_id(data["plans"]) == 2


def test_pick_all_future_takes_soonest():
    plans = [{"id": 1, "title": "Plan 20/12/2026"}, {"id": 2, "title": "Plan 15/12/2026"}]
    assert c._pick_current_plan_id(plans) == 2


def test_pick_without_dates_uses_latest_sent_then_first():
    plans = [
        {"id": 1, "title": "a", "sentByEmailAt": "2026-01-01T00:00:00"},
        {"id": 2, "title": "b", "sentByEmailAt": "2026-02-01T00:00:00"},
    ]
    # no usable date in either title; "created" missing -> falls to sent order
    assert c._pick_current_plan_id(plans) == 2
    assert c._pick_current_plan_id([{"id": 9, "title": "x"}]) == 9
    assert c._pick_current_plan_id([]) is None


def test_dieta_index_weekly_is_weekday():
    assert c.dieta_index_for(D(2026, 10, 12), 7, D(2026, 10, 12)) == 0
    assert c.dieta_index_for(D(2026, 10, 18), 7, D(2026, 10, 12)) == 6


def test_dieta_index_single():
    assert c.dieta_index_for(D(2026, 10, 14), 1, None) == 0


def test_dieta_index_two_week_plan_advances():
    start = D(2026, 10, 12)
    assert c.dieta_index_for(D(2026, 10, 12), 14, start) == 0
    assert c.dieta_index_for(D(2026, 10, 19), 14, start) == 7
    assert c.dieta_index_for(D(2026, 10, 26), 14, start) == 0


def test_dieta_index_two_week_plan_starting_midweek_uses_week_monday():
    # starts on a Wednesday: index counts from that week's Monday
    assert c.dieta_index_for(D(2026, 10, 14), 14, D(2026, 10, 14)) == 2


def test_unread_count_shapes():
    assert c.unread_count({"chat_unread": 3}) == 3
    assert c.unread_count({"chat_unread": {"count": 2}}) == 2
    assert c.unread_count({"chat_unread": None}) == 0
    assert c.unread_count(None) == 0
