"""Basis-entiteit met device-info voor DynTarNL."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ELECTRICITY, GAS, NAME
from .coordinator import DynTarNLCoordinator
from .model import EnergyData

ENERGY_NAMES = {ELECTRICITY: "Stroom", GAS: "Gas"}


class DynTarNLEntity(CoordinatorEntity[DynTarNLCoordinator]):
    """Gedeelde basis: koppelt de entiteit aan een device per energietype."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DynTarNLCoordinator, energy: str) -> None:
        super().__init__(coordinator)
        self._energy = energy
        entry_id = coordinator.config_entry.entry_id
        supplier = coordinator.supplier
        supplier_name = supplier.name if supplier else NAME
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{energy}")},
            name=f"{NAME} {supplier_name} {ENERGY_NAMES[energy]}",
            manufacturer=supplier_name,
            model="Dynamische tarieven",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _data(self) -> EnergyData | None:
        return self.coordinator.data.get(self._energy)

    @property
    def available(self) -> bool:
        return super().available and self._data is not None
