"""A native Home Assistant to-do list with the shopping list of the current
plan (all its days). Ticked items are remembered per plan."""
from __future__ import annotations

from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity, TodoListEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DietoProDataUpdateCoordinator
from .sensor import build_device_info, shape_day, shopping_list

STORAGE_VERSION = 1


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: DietoProDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ShoppingTodo(hass, coordinator, entry)])


class ShoppingTodo(CoordinatorEntity[DietoProDataUpdateCoordinator], TodoListEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:cart"
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    def __init__(self, hass: HomeAssistant, coordinator: DietoProDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_lista_compra"
        self._attr_name = "Lista de la compra"
        self._attr_device_info = build_device_info(entry)
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.shopping_{entry.entry_id}")
        self._checked: set[str] = set()
        self._stored_plan_id = None
        self._items: list[dict] = []
        self._plan_id = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        stored = await self._store.async_load() or {}
        self._stored_plan_id = stored.get("plan_id")
        self._checked = set(stored.get("checked") or [])
        self._rebuild()

    def _rebuild(self) -> None:
        detail = (self.coordinator.data or {}).get("plan_detail")
        detail = detail if isinstance(detail, dict) else {}
        dietas = detail.get("dietas") if isinstance(detail.get("dietas"), list) else []
        self._plan_id = detail.get("id")
        self._items = shopping_list([shape_day(i, d) for i, d in enumerate(dietas)]) if dietas else []
        if self._plan_id != self._stored_plan_id:
            # A different plan means a different shopping list: start unticked.
            self._checked = set()
            self._stored_plan_id = self._plan_id

    @callback
    def _handle_coordinator_update(self) -> None:
        self._rebuild()
        super()._handle_coordinator_update()

    @property
    def todo_items(self) -> list[TodoItem]:
        return [
            TodoItem(
                uid=item["nombre"].lower(),
                summary=f"{item['nombre']} ({item['cantidad_g']} g)" if item["cantidad_g"] else item["nombre"],
                description=item.get("categoria"),
                status=TodoItemStatus.COMPLETED if item["nombre"].lower() in self._checked else TodoItemStatus.NEEDS_ACTION,
            )
            for item in self._items
        ]

    async def async_update_todo_item(self, item: TodoItem) -> None:
        if item.status == TodoItemStatus.COMPLETED:
            self._checked.add(item.uid)
        else:
            self._checked.discard(item.uid)
        await self._store.async_save({"plan_id": self._plan_id, "checked": sorted(self._checked)})
        self.async_write_ha_state()
