"""Sensors, binary sensors, calendars, to-do list and services on realistic data."""
import asyncio
import datetime as dtm
from unittest.mock import MagicMock

import pytest

import custom_components.nutriplan_bridge as integration
from custom_components.nutriplan_bridge import binary_sensor, calendar, sensor, todo

NOW = dtm.datetime(2026, 10, 12, 9, 0)


def test_parse_hora_variants():
    assert sensor.parse_hora("07:00") == dtm.time(7, 0)
    assert sensor.parse_hora("07:00:30") == dtm.time(7, 0, 30)
    assert sensor.parse_hora("2026-01-01T07:30:00") == dtm.time(7, 30)
    assert sensor.parse_hora(None) is None
    assert sensor.parse_hora("nope") is None


def test_franja_labels_use_dietitian_names(data):
    labels = sensor.franja_labels(data)
    assert labels["comida"] == "Almuerzo"
    assert labels["desayuno"] == "Desayuno"


def test_shopping_list_sums_and_groups(data):
    days = [sensor.shape_day(i, d) for i, d in enumerate(data["plan_detail"]["dietas"][:2])]
    items = sensor.shopping_list(days)
    assert len(items) == 1
    assert items[0]["nombre"] == "Pan"
    assert items[0]["cantidad_g"] == 300  # 3 meals x 2 days x 50 g
    assert items[0]["categoria"] == "Farináceos"


def test_today_meals_and_energy(coord, entry):
    meals = sensor.DietoProTodayMealsSensor(coord, entry)
    assert meals.native_value == 3
    assert meals.extra_state_attributes["dieta_index"] == 0  # Monday
    energy = sensor.DietoProTodayEnergySensor(coord, entry)
    assert energy.native_value == 1400
    assert energy.extra_state_attributes["proteinas_g"] == 30.0


def test_next_meal_is_the_first_still_to_come(coord, entry):
    # 09:00: the 07:00 breakfast is over, so the next one is lunch at 14:30
    nxt = sensor.DietoProNextMealSensor(coord, entry)
    assert nxt.native_value == "Almuerzo"
    attrs = nxt.extra_state_attributes
    assert attrs["hora"] == "14:30"
    assert attrs["en_minutos"] == 5 * 60 + 30
    assert attrs["platos"] == ["Lasaña"]


def test_numeric_sensors(coord, entry):
    values = {
        spec[0]: sensor.DietoProMetricSensor(coord, entry, *spec).native_value for spec in sensor.METRIC_SENSORS
    }
    assert values["peso"] == 88.5
    assert values["masa_magra"] == 59.5
    derived = {spec[0]: sensor.DietoProDerivedSensor(coord, entry, *spec).native_value for spec in sensor.DERIVED_SENSORS}
    assert derived["objetivo_calorico"] == 1800
    assert derived["peso_objetivo"] == 80.0
    assert derived["peso_restante"] == 8.5
    assert derived["variacion_peso_30d"] == -1.5
    assert derived["dias_sin_medir"] == 9


def test_sensors_survive_empty_data(entry):
    from tests.conftest import FakeCoordinator

    empty = FakeCoordinator({})
    assert sensor.DietoProTodayEnergySensor(empty, entry).native_value is None
    assert sensor.DietoProNextMealSensor(empty, entry).native_value == "Nada más hoy"
    for spec in sensor.DERIVED_SENSORS:
        assert sensor.DietoProDerivedSensor(empty, entry, *spec).native_value is None


def test_plan_sensor_lists_plans_newest_start_first(coord, entry):
    planes = sensor.DietoProCurrentPlanSensor(coord, entry).extra_state_attributes["planes"]
    assert [p["id"] for p in planes] == [4, 3, 2]


def test_binary_sensors(coord, entry):
    assert binary_sensor.UnreadMessagesBinary(coord, entry).is_on
    assert binary_sensor.AppointmentSoonBinary(coord, entry).is_on
    assert binary_sensor.NewPlanBinary(coord, entry).is_on
    assert binary_sensor.MeasurementDueBinary(coord, entry).is_on


def test_meals_calendar(coord, entry):
    cal = calendar.DietoProMealsCalendar(coord, entry)
    monday = dtm.date(2026, 10, 12)
    events = cal._events_between(monday, monday + dtm.timedelta(days=7))
    assert len(events) == 21
    ev = cal.event
    assert ev is not None and ev.summary.startswith("Almuerzo")  # 07:00 breakfast already over


def test_appointment_calendar(coord, entry):
    ev = calendar.DietoProAppointmentCalendar(coord, entry).event
    assert ev is not None and ev.summary == "Cita con el dietista"


def test_todo_list(coord, entry):
    t = todo.ShoppingTodo(MagicMock(), coord, entry)
    t._rebuild()
    items = t.todo_items
    assert len(items) == 1
    assert items[0].summary == "Pan (1050 g)"


class Call:
    def __init__(self, d):
        self.data = d


def _hass(data):
    h, c = MagicMock(), MagicMock()
    c.data = data
    h.data = {"nutriplan_bridge": {"abc": c}}
    return h


def test_obtener_comidas_service(data):
    r = asyncio.run(integration._async_handle_get_meals(_hass(data), Call({})))
    assert r["plan_id"] == 3 and r["num_dietas"] == 7 and r["es_actual"] is True
    assert r["promedio_kcal"] == 1400
    assert r["inicio"] == "2026-10-12"


def test_lista_compra_service_filters_days(data):
    r = asyncio.run(integration._async_handle_shopping(_hass(data), Call({"dias": [0, 1, 99]})))
    assert r["dias"] == [0, 1] and r["total"] == 1


def test_change_plato_validates_before_calling_api(data):
    from homeassistant.exceptions import HomeAssistantError

    bad = Call({"plan_id": "x", "dieta": 0, "franja": "comida", "plato_actual_id": 1, "nuevo_plato_id": 2})
    with pytest.raises(HomeAssistantError):
        asyncio.run(integration._async_handle_change_plato(_hass(data), bad))
