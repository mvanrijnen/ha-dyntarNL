"""Button om de tarieven handmatig te verversen (ook aanroepbaar via automatiseringen)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import DynTarNLConfigEntry, DynTarNLCoordinator


class DynTarNLRefreshButton(CoordinatorEntity[DynTarNLCoordinator], ButtonEntity):
    """Knop 'Ververs tarieven' — haalt de data direct opnieuw op."""

    _attr_has_entity_name = True
    _attr_name = "Ververs tarieven"
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DynTarNLCoordinator) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        supplier = coordinator.supplier
        supplier_name = supplier.name if supplier else NAME
        self._attr_unique_id = f"{entry_id}_refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_service")},
            name=NAME,
            manufacturer=supplier_name,
            model="Dynamische tarieven",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DynTarNLConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([DynTarNLRefreshButton(entry.runtime_data)])
