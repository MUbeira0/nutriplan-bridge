"""The card finds the integration's entities by the END of their entity id, and a fresh
install derives that id from the entity NAME. These must agree, or a new setup shows
"no se han encontrado entidades" (found by the end-to-end run)."""
from pathlib import Path

import pytest
from homeassistant.util import slugify

from custom_components.nutriplan_bridge import sensor

JS = (Path(__file__).resolve().parent.parent / "custom_components" / "nutriplan_bridge" / "www" / "nutriplan-bridge-card.js").read_text(encoding="utf-8")

# entity class -> the suffix key the card passes to findEntity()
USED_BY_CARD = {
    sensor.DietoProCurrentPlanSensor: "_plan_actual",
    sensor.DietoProTodayMealsSensor: "_comidas_hoy",
    sensor.DietoProNextAppointmentSensor: "_proxima_cita",
    sensor.DietoProSeguimientosSensor: "_seguimientos",
    sensor.DietoProDietistaSensor: "_dietista",
    sensor.DietoProChatSensor: "_mensajes_sin_leer",
    sensor.DietoProCharlasSensor: "_charlas",
    sensor.DietoProEniSensor: "_estado_eni",
    sensor.DietoProEatenSensor: "_comidas_tomadas_hoy",
}


@pytest.mark.parametrize("cls,key", USED_BY_CARD.items(), ids=lambda v: getattr(v, "__name__", v))
def test_card_can_find_the_entity_a_fresh_install_creates(cls, key, coord, entry):
    name_slug = "_" + slugify(cls(coord, entry)._attr_name)
    assert f'"{key}"' in JS, f"the card does not look for {key}"
    spellings = {key}
    if "SUFFIX_ALIASES" in JS and f"{key}: [" in JS:
        line = next(l for l in JS.splitlines() if l.strip().startswith(f"{key}: ["))
        spellings.update(part.strip().strip('",') for part in line.split("[", 1)[1].split("]")[0].split(","))
    assert name_slug in spellings, f"fresh install id ends with {name_slug!r} but the card looks for {sorted(spellings)}"
