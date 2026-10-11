"""Icons: every service has one, and entity icons follow the state."""
import json
from pathlib import Path

import yaml

from custom_components.nutriplan_bridge import binary_sensor, sensor

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "nutriplan_bridge"


def test_every_service_has_an_icon_and_vice_versa():
    services = set(yaml.safe_load((PKG / "services.yaml").read_text(encoding="utf-8")))
    icons = json.loads((PKG / "icons.json").read_text(encoding="utf-8"))["services"]
    assert services == set(icons)
    assert all(v["service"].startswith("mdi:") for v in icons.values())


def test_brand_assets_exist():
    for name in ("icon.png", "icon@2x.png", "logo.png", "logo@2x.png", "dark_icon.png", "dark_logo.png"):
        assert (PKG / "brand" / name).stat().st_size > 1000


def test_next_meal_icon_follows_the_meal(coord, entry):
    nxt = sensor.DietoProNextMealSensor(coord, entry)
    assert nxt.icon == "mdi:food"  # 09:00 -> next is lunch
    from tests.conftest import FakeCoordinator

    assert sensor.DietoProNextMealSensor(FakeCoordinator({}), entry).icon == "mdi:check-circle-outline"


def test_weight_change_icon_follows_sign(coord, entry):
    spec = next(s for s in sensor.DERIVED_SENSORS if s[0] == "variacion_peso_30d")
    assert sensor.DietoProDerivedSensor(coord, entry, *spec).icon == "mdi:trending-down"


def test_binary_icons_follow_state(coord, entry):
    unread = binary_sensor.UnreadMessagesBinary(coord, entry)
    assert unread.is_on and unread.icon == "mdi:message-badge"
    coord.data["chat_unread"] = 0
    assert unread.icon == "mdi:message-outline"


def test_chat_sensor_icon(coord, entry):
    chat = sensor.DietoProChatSensor(coord, entry)
    assert chat.icon == "mdi:chat-processing"
    coord.data["chat_unread"] = 0
    assert chat.icon == "mdi:chat-outline"
