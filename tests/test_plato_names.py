"""A plato without a superPlato (a simple food) must not show as "Sin especificar"."""
from custom_components.nutriplan_bridge import sensor


def _sub(plato):
    return {"id": "1", "plato": plato}


def _ing(name, qty):
    return {"alimento": {"nombre": name}, "cantidad": qty, "medidaCasera": ""}


def test_recipe_plato_keeps_its_superplato_name():
    sub = _sub({"id": 1, "superPlato": {"id": 9, "nombre": "Lasaña"}, "alimentoCantidades": [_ing("pasta", 80)]})
    d = sensor._plato_detail(sub)
    assert d["nombre"] == "Lasaña" and d["nombre_derivado"] is False


def test_simple_food_without_superplato_uses_the_ingredient():
    sub = _sub({"id": 2, "superPlato": None, "energia": 165, "alimentoCantidades": [_ing("pechuga de pollo", 150)]})
    d = sensor._plato_detail(sub)
    assert d["nombre"] == "Pechuga de pollo (150 g)"
    assert d["nombre_derivado"] is True


def test_missing_superplato_name_falls_back_to_plato_name():
    sub = _sub({"id": 3, "nombre": "Plato del día", "superPlato": {"id": 1, "nombre": None}})
    assert sensor._plato_detail(sub)["nombre"] == "Plato del día"


def test_several_ingredients_are_joined():
    sub = _sub({"id": 4, "alimentoCantidades": [_ing("arroz", 60), _ing("pollo", 100), _ing("brócoli", 80), _ing("aceite", 5)]})
    assert sensor._plato_detail(sub)["nombre"] == "Arroz + pollo + brócoli + …"


def test_truly_empty_plato_stays_unnamed():
    assert sensor._plato_detail(_sub({"id": 5}))["nombre"] is None


def test_diagnostics_lists_unnamed_platos():
    from custom_components.nutriplan_bridge.diagnostics import _unnamed_platos

    dieta = {"comida": {"hora": "14:00", "subingestas": [_sub({"id": 5}), _sub({"id": 6, "alimentoCantidades": [_ing("pollo", 1)]})]}}
    out = _unnamed_platos([dieta])
    assert len(out) == 1 and out[0]["franja"] == "comida" and out[0]["plato_keys"] == ["id"]
