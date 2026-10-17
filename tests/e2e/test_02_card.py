"""The Lovelace card inside the real Home Assistant frontend, driven by a real browser.

This is what catches the things mocks cannot: the card's JS really being auto-registered,
the websocket calls the card makes, the frontend's own elements and styling.
"""
import json
import os
import time
from pathlib import Path

import pytest

from .conftest import BASE, CLIENT_ID

playwright_sync = pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.e2e
ARTIFACTS = Path(os.environ.get("E2E_ARTIFACTS", "e2e-artifacts"))


@pytest.fixture(scope="module")
def page(ha, entry):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    # A dashboard with just the card (auto-detected entities, nothing configured).
    saved = ha.ws({"type": "lovelace/config/save", "config": {"title": "e2e", "views": [{"title": "Nutriplan", "path": "nutri", "cards": [{"type": "custom:nutriplan-bridge-card"}]}]}})
    assert saved.get("success"), saved

    tokens = {
        "access_token": ha.token, "token_type": "Bearer", "expires_in": 1800, "refresh_token": ha.refresh_token,
        "hassUrl": BASE, "clientId": CLIENT_ID, "expires": int((time.time() + 1700) * 1000),
    }
    with playwright_sync.sync_playwright() as p:
        channel = os.environ.get("E2E_BROWSER_CHANNEL") or None
        browser = p.chromium.launch(channel=channel)
        context = browser.new_context(viewport={"width": 520, "height": 1700}, locale="es-ES", timezone_id="Europe/Madrid")
        context.add_init_script(f"localStorage.setItem('hassTokens', {json.dumps(json.dumps(tokens))});")
        pg = context.new_page()
        pg.set_default_timeout(60000)
        pg.errors = []
        pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.on("dialog", lambda d: d.accept())  # the card asks window.confirm() before writing to the account
        pg.goto(f"{BASE}/lovelace/nutri")
        pg.wait_for_selector("nutriplan-bridge-card .meal-card", timeout=120000)
        yield pg
        pg.screenshot(path=str(ARTIFACTS / "final.png"), full_page=True)
        browser.close()


def card_text(page) -> str:
    return page.locator("nutriplan-bridge-card ha-card").inner_text()


def test_card_was_registered_automatically_and_renders(page):
    assert page.evaluate("customElements.get('nutriplan-bridge-card') !== undefined")
    text = card_text(page)
    assert "Plan dietético semanal de Test Paciente" in text
    assert "Sin especificar" not in text, text
    assert "esperando datos" not in text and "error al renderizar" not in text
    for letter in "LMXJVSD":
        assert page.locator(f"nutriplan-bridge-card .day-chip:text-is('{letter}')").count() == 1
    page.screenshot(path=str(ARTIFACTS / "card_day.png"), full_page=True)


def test_only_offers_the_actions_this_account_allows(page):
    text = card_text(page)
    # fake account: booking disabled, cancellation "disabled", no patient confirmation
    assert "Pedir cita" not in text and "Cancelar cita" not in text and "Confirmar cita" not in text
    assert "Registrar medición" not in text  # opt-in only


def test_loose_food_shows_its_name_and_can_be_swapped(page, fake):
    page.locator("nutriplan-bridge-card .meal-header[data-franja='tentempie1']").click()
    assert "Pollo, pechuga, solomillo (130 g)" in card_text(page)
    page.locator("nutriplan-bridge-card .plato-summary").first.click()
    page.locator("nutriplan-bridge-card .cambiar-btn", has_text="Cambiar alimento").click()
    page.wait_for_selector("nutriplan-bridge-card .alimento-pick")
    assert "Pavo, pechuga" in card_text(page)
    fake.state.requests.clear()
    page.locator("nutriplan-bridge-card .alimento-pick").first.click()
    deadline = time.time() + 30
    while time.time() < deadline and not fake.state.calls("PATCH", "/api/paciente/changes/"):
        time.sleep(0.3)
    sent = fake.state.calls("PATCH", "/api/paciente/changes/102/alimento")
    assert sent and sent[-1]["body"]["alimentoId"] == 5001 and sent[-1]["body"]["currentId"] == 4321


def test_ticking_a_meal_is_stored_in_home_assistant(page, ha):
    eaten = ha.find("sensor", "_comidas_tomadas_hoy")
    page.locator("nutriplan-bridge-card .eaten-toggle[data-franja='comida']").click()
    ha.wait_for(lambda: "comida" in ha.state(eaten)["attributes"]["tomadas"], timeout=30, what="HA to receive the tick from the card")
    page.wait_for_selector("nutriplan-bridge-card .eaten-toggle.done")
    ha.service("marcar_comida", {"franja": "comida", "tomada": False})


def test_week_view_and_day_navigation(page):
    page.locator("nutriplan-bridge-card .week-toggle").click()
    page.wait_for_selector("nutriplan-bridge-card .week-day")
    assert page.locator("nutriplan-bridge-card .week-day").count() == 7
    page.locator("nutriplan-bridge-card .week-day").nth(3).click()
    page.wait_for_selector("nutriplan-bridge-card .meal-card")
    assert "JUEVES" in card_text(page).upper()
    page.screenshot(path=str(ARTIFACTS / "card_thursday.png"), full_page=True)


def test_previous_plan_is_read_only(page):
    page.locator("nutriplan-bridge-card .nav-btn").first.click()
    page.wait_for_selector("nutriplan-bridge-card .readonly-note")
    assert "solo lectura" in card_text(page)
    assert page.locator("nutriplan-bridge-card .cambiar-btn").count() == 0
    page.locator("nutriplan-bridge-card .link-btn", has_text="Volver al plan actual").click()


def test_chat_panel_sends_a_message(page, fake):
    page.locator("nutriplan-bridge-card .chat-toggle").click()
    page.locator("nutriplan-bridge-card .chat-input").fill("Mensaje desde el navegador")
    fake.state.requests.clear()
    page.locator("nutriplan-bridge-card .chat-send").click()
    deadline = time.time() + 30
    while time.time() < deadline and not fake.state.calls("POST", "/api/paciente/chat/messages"):
        time.sleep(0.3)
    assert fake.state.calls("POST", "/api/paciente/chat/messages")[-1]["body"] == {"text": "Mensaje desde el navegador"}


def test_chat_attachment_goes_through_the_upload_endpoint(page, fake, tmp_path):
    img = tmp_path / "comida.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    fake.state.requests.clear()
    page.locator("nutriplan-bridge-card .chat-file").set_input_files(str(img))
    deadline = time.time() + 30
    while time.time() < deadline and not fake.state.calls("POST", "/api/paciente/chat/images"):
        time.sleep(0.3)
    sent = fake.state.calls("POST", "/api/paciente/chat/images")
    assert sent and sent[-1]["body"]["file"] == "comida.jpg"


def test_no_javascript_errors_from_the_card(page):
    ours = [e for e in page.errors if "nutriplan" in e.lower()]
    assert not ours, ours
