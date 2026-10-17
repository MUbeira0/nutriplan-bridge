"""The integration inside a real Home Assistant: setup, entities, services, error handling."""
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.e2e


def test_setup_creates_the_expected_entities(ha, entry):
    ids = {
        ("sensor", "_plan_actual"), ("sensor", "_comidas_de_hoy"), ("sensor", "_proxima_cita"), ("sensor", "_seguimientos"),
        ("sensor", "_dietista"), ("sensor", "_mensajes_sin_leer"), ("sensor", "_encuesta_inicial"), ("sensor", "_charlas"),
        ("sensor", "_calorias_de_hoy"), ("sensor", "_proxima_comida"), ("sensor", "_peso"), ("sensor", "_comidas_tomadas_hoy"),
        ("binary_sensor", "_mensajes_pendientes"), ("binary_sensor", "_cita_sin_confirmar"),
        ("calendar", "_comidas"), ("calendar", "_citas"), ("todo", "_lista_de_la_compra"),
        ("button", "_actualizar_ahora"),
    }
    for domain, suffix in ids:
        ha.find(domain, suffix)


def test_current_plan_is_the_one_of_this_week(ha, entry):
    plan = ha.state(ha.find("sensor", "_plan_actual"))
    this_monday = date.today() - timedelta(days=date.today().weekday())
    assert plan["attributes"]["plan_id"] == 102
    assert this_monday.strftime("%d/%m/%Y") in plan["state"]
    # the next week's plan is known but not "current"
    assert [p["id"] for p in plan["attributes"]["planes"]] == [103, 102, 101]


def test_loose_food_is_named_estimated_and_not_swappable_as_a_plato(ha, entry):
    meals = ha.state(ha.find("sensor", "_comidas_de_hoy"))["attributes"]["comidas"]
    loose = next(p for m in meals for p in m["platos"] if p.get("tipo") == "alimento")
    assert loose["nombre"] == "Pollo, pechuga, solomillo (130 g)"
    assert loose["plato_id"] is None and loose["alimento_id"] == 4321
    assert loose["calorias"] == 156 and loose["calorias_estimadas"] is True
    assert all(p["nombre"] for m in meals for p in m["platos"]), "a dish came out unnamed"


def test_numbers_make_sense(ha, entry):
    assert int(ha.state(ha.find("sensor", "_calorias_de_hoy"))["state"]) > 1000
    assert float(ha.state(ha.find("sensor", "_peso"))["state"]) == 91.2
    assert ha.state(ha.find("sensor", "_proxima_comida"))["state"] != "unknown"
    cita = ha.state(ha.find("sensor", "_proxima_cita"))
    assert cita["attributes"]["duracion_min"] == 15 and cita["attributes"]["reserva_habilitada"] is False
    assert ha.state(ha.find("binary_sensor", "_mensajes_pendientes"))["state"] == "on"


def test_calendars_and_shopping_list(ha, entry):
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=1)).isoformat()
    events = ha.api("GET", f"/api/calendars/{ha.find('calendar', '_comidas')}?start={start}T00:00:00Z&end={end}T00:00:00Z").json()
    assert len(events) >= 5 and any("Almuerzo" in e["summary"] for e in events)
    todo = ha.state(ha.find("todo", "_lista_de_la_compra"))
    assert int(todo["state"]) > 5


def test_no_errors_or_thread_warnings_in_the_log(ha, entry):
    log = ha.log_text()
    assert "from a thread" not in log, "async_write_ha_state called from a thread"
    nutri_errors = [line for line in log.splitlines() if "nutriplan_bridge" in line and (" ERROR " in line or "Traceback" in line)]
    assert not nutri_errors, "\n".join(nutri_errors[:10])


def test_read_only_services(ha, entry):
    week = ha.service("obtener_comidas", response=True).json()["service_response"]
    assert week["plan_id"] == 102 and week["num_dietas"] == 7 and week["es_actual"] is True
    shop = ha.service("lista_compra", {"dias": [0]}, response=True).json()["service_response"]
    assert shop["total"] >= 5 and all(i["cantidad_g"] > 0 for i in shop["items"])
    slots = ha.service("citas_disponibles", response=True).json()["service_response"]
    assert slots["total"] == 2 and slots["huecos"][0]["hora"] == "10:00"
    opts = ha.service("opciones_alimento", {"alimento_id": 4321, "cantidad": 130}, response=True).json()["service_response"]
    assert opts["opciones"][0] == {"alimento_id": 5001, "nombre": "Pavo, pechuga", "cantidad": 130, "medida_casera": "1 filete", "grupo": "Carnes", "categoria": "Proteicos"}
    assert ha.service("medicion_abierta", response=True).json()["service_response"]["abierta"] is True


def test_eaten_meals_are_stored_in_ha_and_update_the_sensor(ha, entry):
    eaten = ha.find("sensor", "_comidas_tomadas_hoy")
    assert ha.service("marcar_comida", {"franja": "desayuno"}).status_code == 200
    ha.wait_for(lambda: ha.state(eaten)["attributes"]["tomadas"] == ["desayuno"], what="eaten sensor to update")
    assert ha.state(eaten)["state"] == "1"
    assert ha.service("marcar_comida", {"franja": "desayuno", "tomada": False}).status_code == 200
    ha.wait_for(lambda: ha.state(eaten)["state"] == "0", what="eaten sensor to clear")
    assert ha.service("marcar_comida", {"franja": "brunch"}).status_code >= 400  # unknown slot is refused


def test_write_services_send_exactly_what_the_app_sends(ha, entry, fake):
    fake.state.requests.clear()
    assert ha.service("enviar_mensaje", {"texto": "Hola dietista"}).status_code == 200
    assert fake.state.calls("POST", "/api/paciente/chat/messages")[-1]["body"] == {"text": "Hola dietista"}

    assert ha.service("solicitar_cita", {"fecha": "2026-12-01", "hora": "10:00", "nota": "gracias"}, response=True).status_code == 200
    assert fake.state.calls("POST", "/api/paciente/citas/booking-request")[-1]["body"] == {"date": "2026-12-01", "time": "10:00", "note": "gracias"}

    assert ha.service("cancelar_cita", response=True).status_code == 200  # defaults to the next appointment's id
    assert fake.state.calls("POST", "/api/paciente/citas/555/cancel")

    r = ha.service("cambiar_alimento", {"plan_id": 102, "dieta": 0, "franja": "tentempie1", "alimento_actual_id": 4321, "cantidad_actual": 130,
                                         "nuevo_alimento_id": 5001, "nueva_cantidad": 130}, response=True)
    assert r.status_code == 200, r.text
    assert fake.state.calls("PATCH", "/api/paciente/changes/102/alimento")[-1]["body"] == {
        "currentId": 4321, "currentQuantity": 130, "alimentoId": 5001, "alimentoQuantity": 130, "ingesta": "tentempie1", "dieta": 0}

    r = ha.service("cambiar_plato", {"plan_id": 102, "dieta": 0, "franja": "comida", "plato_actual_id": 200, "nuevo_plato_id": 7001}, response=True)
    assert r.status_code == 200, r.text
    assert fake.state.calls("PATCH", "/api/paciente/plans/102")[-1]["body"] == {"currentId": 200, "platoId": 7001, "ingesta": "comida", "dieta": 0}


def test_manual_measurement_goes_through_the_wizard_steps(ha, entry, fake):
    fake.state.requests.clear()
    r = ha.service("registrar_medicion", {"peso": 90.5, "perimetro_cintura": 103, "finalizar": True}, response=True)
    assert r.status_code == 200, r.text
    steps = [(c["path"], c["body"]) for c in fake.state.requests if c["method"] == "PUT" and c["path"].endswith("/step")]
    assert steps == [("/api/paciente/seguimientos/77/step", {"peso": 90.5}), ("/api/paciente/seguimientos/77/step", {"perimetroCintura": 103.0})]
    assert fake.state.calls("PATCH", "/api/paciente/finish/seguimiento")
    # no open measurement -> refused, nothing sent
    fake.state.open_measurement = False
    fake.state.requests.clear()
    assert ha.service("registrar_medicion", {"peso": 90.0}, response=True).status_code >= 400
    assert not [c for c in fake.state.requests if c["method"] == "PUT"]
    fake.state.open_measurement = True


def test_dietopro_outage_raises_a_repairs_notice_and_recovers(ha, entry, fake):
    press = lambda: ha.service("press", {"entity_id": ha.find("button", "_actualizar_ahora")}, domain="button")  # noqa: E731
    fake.state.fail_with = 503
    for _ in range(4):
        press()
    issues = lambda: [i["issue_id"] for i in ha.ws({"type": "repairs/list_issues"})["result"]["issues"] if i["domain"] == "nutriplan_bridge"]  # noqa: E731
    ha.wait_for(lambda: any(i.startswith("api_unreachable") for i in issues()), timeout=60, what="the Repairs notice")
    fake.state.fail_with = None
    ha.wait_for(lambda: (press() and False) or not issues(), timeout=60, interval=2, what="the notice to clear")
    # data is back too
    assert ha.state(ha.find("sensor", "_plan_actual"))["state"] != "unavailable"


def test_rejected_credentials_open_the_reauth_flow(ha, entry, fake):
    fake.state.reject_auth = True
    press = lambda: ha.service("press", {"entity_id": ha.find("button", "_actualizar_ahora")}, domain="button")  # noqa: E731
    press()

    def reauth_started():
        flows = ha.api("GET", "/api/config/config_entries/flow").json()
        return [f for f in flows if f["handler"] == "nutriplan_bridge" and f["context"].get("source") == "reauth"]

    flow = ha.wait_for(reauth_started, timeout=60, what="the reauth flow")[0]
    fake.state.reject_auth = False
    step = ha.api("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", json={"password": fake.state.valid_password}).json()
    assert step["type"] == "abort" and step["reason"] == "reauth_successful", step
    ha.wait_for(lambda: ha.state(ha.find("sensor", "_plan_actual"))["state"] != "unavailable", what="entities to recover after reauth")
