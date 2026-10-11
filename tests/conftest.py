"""Shared fixtures: a fixed "now" (Monday 12 Oct 2026, 09:00 Madrid) and a
realistic fake of the data the coordinator holds."""
import datetime as dtm
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from homeassistant.util import dt as dt_util  # noqa: E402

TZ = dt_util.get_time_zone("Europe/Madrid")
NOW = dtm.datetime(2026, 10, 12, 9, 0, tzinfo=TZ)  # a Monday


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", TZ)
    monkeypatch.setattr(dt_util, "now", lambda time_zone=None: NOW)
    return NOW


def _plato(i, name, kcal):
    return {
        "id": i,
        "energia": kcal,
        "alergenos": [],
        "proteinasTotales": 10,
        "grasasTotales": 5,
        "glucidosTotales": 20,
        "alimentoCantidades": [
            {
                "alimento": {
                    "nombre": "Pan",
                    "grupoAlimento": {"nombre": "Cereales"},
                    "superGrupoAlimento": {"nombre": "Farináceos"},
                },
                "cantidad": 50,
                "medidaCasera": "2 rebanadas",
            }
        ],
        "superPlato": {"id": i, "nombre": name, "receta": "r", "rating": 3, "imagePath": "/x.jpg"},
    }


def _ingesta(hora, i, name, kcal):
    return {"hora": hora, "subingestas": [{"id": str(i), "plato": _plato(i, name, kcal)}]}


def make_dieta(i):
    return {
        "nombreHorario": "Turno partido",
        "desayuno": _ingesta("07:00", 10 + i, "Café", 300),
        "comida": _ingesta("14:30", 20 + i, "Lasaña", 700),
        "cena": _ingesta("21:00", 30 + i, "Yogur", 400),
    }


@pytest.fixture
def data():
    ts = lambda days: str(int((NOW - dtm.timedelta(days=days)).timestamp()))  # noqa: E731
    return {
        "plans": [
            {"id": 3, "title": "Plan semanal 12/10/2026", "created": "2026-10-01T10:00:00", "sentByEmailAt": None},
            {"id": 2, "title": "Plan semanal 05/10/2026", "created": "2026-09-28T10:00:00", "sentByEmailAt": "2026-09-29T10:00:00"},
            {"id": 4, "title": "Plan semanal 19/10/2026", "created": "2026-10-08T10:00:00", "sentByEmailAt": None},
        ],
        "plan_detail": {
            "id": 3,
            "title": "Plan semanal 12/10/2026",
            "created": "2026-10-01T10:00:00",
            "dietas": [make_dieta(i) for i in range(7)],
        },
        "cita": {"cita": {"fecha": (NOW + dtm.timedelta(hours=5)).isoformat()}},
        "seguimientos": [
            {"id": 1, "createdTimestamp": ts(40), "peso": 90, "imc": 30, "pesoGrasa": 30},
            {"id": 2, "createdTimestamp": ts(9), "peso": 88.5, "imc": 29.5, "pesoGrasa": 29},
        ],
        "dietista": {"firstName": "L", "familyName": "G", "nombreIngestas": [{"ingestaType": "comida", "ingestaName": "Almuerzo"}]},
        "eni": {"valorEnergetico": 1800, "pesoSugerido": 80, "lastViewedStep": "eniFinish"},
        "chat_unread": 2,
        "chat_messages": [],
        "current_user": {},
        "ratings": [],
        "charlas": [],
    }


class FakeEntry:
    entry_id = "abc"
    data = {"email": "a@b.c"}
    options = {}


class FakeCoordinator:
    def __init__(self, data):
        self.data = data
        self.last_update_success = True
        self.hass = None

    def async_add_listener(self, *args, **kwargs):
        return lambda: None


@pytest.fixture
def entry():
    return FakeEntry()


@pytest.fixture
def coord(data):
    return FakeCoordinator(data)
