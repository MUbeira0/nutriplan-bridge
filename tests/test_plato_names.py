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


def test_loose_food_alimento_cantidad_is_named_and_shaped():
    # exactly the structure seen in a real account: no "plato", only alimentoCantidad
    sub = {
        "id": "99",
        "changedByPaciente": False,
        "alimentoCantidad": {
            "cantidad": 150,
            "medidaCasera": "1 filete",
            "alimento": {
                "id": 7,
                "nombre": "pechuga de pollo",
                "grupoAlimento": {"nombre": "Carnes", "superGrupoAlimento": {"nombre": "Proteicos"}},
            },
        },
    }
    d = sensor._plato_detail(sub)
    assert d["nombre"] == "Pechuga de pollo (150 g)"
    assert d["tipo"] == "alimento" and d["plato_id"] is None and d["subingesta_id"] == "99"
    assert d["ingredientes"][0]["categoria"] == "Proteicos"
    assert d["modificado_por_paciente"] is False


def test_loose_food_feeds_the_shopping_list():
    sub = {"id": "1", "alimentoCantidad": {"cantidad": 150, "alimento": {"nombre": "Pechuga de pollo"}}}
    day = {"comidas": [{"platos": [sensor._plato_detail(sub)]}]}
    assert sensor.shopping_list([day, day])[0]["cantidad_g"] == 300


def test_supplement_is_named():
    sub = {"id": "2", "suplementoCantidad": {"cantidad": 5, "suplemento": {"nombre": "Creatina"}}}
    d = sensor._plato_detail(sub)
    assert d["tipo"] == "suplemento" and d["nombre"].startswith("Creatina")


def test_loose_food_energy_is_used_when_the_api_sends_it():
    sub = {"id": "3", "alimentoCantidad": {"cantidad": 100, "energia": 165.4, "alimento": {"nombre": "Pollo"}}}
    assert sensor._plato_detail(sub)["calorias"] == 165


def test_diagnostics_reports_loose_items_without_values():
    from custom_components.nutriplan_bridge.diagnostics import _loose_items, _unnamed_platos

    dieta = {"tentempie1": {"subingestas": [{"id": "1", "alimentoCantidad": {"cantidad": 1, "alimento": {"nombre": "x", "id": 1}}}]}}
    out = _loose_items([dieta])
    assert out[0]["item_keys"] == ["alimento", "cantidad"] and out[0]["inner_keys"] == ["id", "nombre"]
    assert _unnamed_platos([dieta]) == []  # now named, so no longer "unnamed"
