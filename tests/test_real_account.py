"""Behaviour pinned to the shape of a REAL account (from a downloaded diagnostics dump)."""
import datetime as dtm

from custom_components.nutriplan_bridge import binary_sensor, calendar, sensor


def test_appointment_attributes_follow_the_real_payload(coord, entry):
    attrs = sensor.DietoProNextAppointmentSensor(coord, entry).extra_state_attributes
    assert attrs["cita_id"] == 1382339
    assert attrs["duracion_min"] == 15
    assert attrs["confirmada_por_dietista"] is True
    # this account: booking off, cancellation "disabled", no patient confirmation needed
    assert attrs["reserva_habilitada"] is False
    assert attrs["cancelacion_habilitada"] is False
    assert attrs["requiere_confirmacion_paciente"] is False
    assert attrs["reserva_max_dias"] == 14


def test_appointment_date_is_read_from_fecha(coord, entry):
    assert sensor.DietoProNextAppointmentSensor(coord, entry).native_value.startswith("2026-10-12T")


def test_calendar_event_uses_real_duration(coord, entry):
    ev = calendar.DietoProAppointmentCalendar(coord, entry).event
    assert ev.end - ev.start == dtm.timedelta(minutes=15)


def test_unconfirmed_binary(coord, entry):
    b = binary_sensor.AppointmentUnconfirmedBinary(coord, entry)
    assert b.is_on is False  # dietitian already confirmed, patient confirmation not required
    coord.data["cita"]["citasConfirmedByPaciente"] = True
    assert b.is_on is True and b.extra_state_attributes["falta_tu_confirmacion"] is True
    coord.data["cita"]["cita"]["confirmedByPaciente"] = True
    coord.data["cita"]["cita"]["askedByPaciente"] = True
    coord.data["cita"]["cita"]["confirmedByDietista"] = False
    assert b.is_on is True and b.extra_state_attributes["falta_confirmacion_dietista"] is True


def test_no_appointment_is_not_unconfirmed(coord, entry):
    coord.data["cita"] = {"cita": None}
    assert binary_sensor.AppointmentUnconfirmedBinary(coord, entry).is_on is False


def test_charlas_expose_theme_link_and_unseen(coord, entry):
    attrs = sensor.DietoProCharlasSensor(coord, entry).extra_state_attributes
    assert attrs["sin_ver"] == 1
    c = attrs["charlas"][0]
    assert (c["titulo"], c["tema"], c["enlace"], c["visto"]) == ("Hidratación", "Hábitos", "https://example.com/x", False)


def test_plan_sensor_exposes_modification_info(coord, entry):
    attrs = sensor.DietoProCurrentPlanSensor(coord, entry).extra_state_attributes
    assert attrs["modificado_por_paciente"] is False and attrs["actualizado"]


def test_plan_without_title_still_orders_by_created():
    from custom_components.nutriplan_bridge.sensor import plan_summaries

    out = plan_summaries([{"id": 1, "created": "2026-02-11T21:58:29+00:00"}, {"id": 2, "created": "2026-02-17T21:32:16+00:00", "sentByEmailAt": "x"}])
    assert [p["id"] for p in out] == [2, 1]
