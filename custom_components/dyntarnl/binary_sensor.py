"""Binary sensors: morgen beschikbaar + negatieve-prijs / teruglever-detectie."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import ELECTRICITY, GAS
from .coordinator import DynTarNLConfigEntry
from .entity import DynTarNLEntity
from .model import EnergyData
from .prices import feed_in_value, slot_at

type StateFn = Callable[[EnergyData, datetime], bool | None]


def _now_slot(data, now):
    return slot_at(data.today, now)


def _next_slot(data, now):
    return slot_at(data.today + (data.tomorrow or []), now + timedelta(hours=1))


def _prev_slot(data, now):
    return slot_at((data.yesterday or []) + data.today, now - timedelta(hours=1))


def _neg_now(data, now):
    s = _now_slot(data, now)
    return None if s is None else s.market < 0


def _neg_prev(data, now):
    s = _prev_slot(data, now)
    return None if s is None else s.market < 0


def _neg_next(data, now):
    s = _next_slot(data, now)
    return None if s is None else s.market < 0


def _cost_now(data, now):
    s = _now_slot(data, now)
    return None if s is None else feed_in_value(s) < 0


def _cost_next(data, now):
    s = _next_slot(data, now)
    return None if s is None else feed_in_value(s) < 0


@dataclass(frozen=True, kw_only=True)
class DynTarNLBinaryDescription(BinarySensorEntityDescription):
    state_fn: StateFn


_BINARY_SENSORS: tuple[DynTarNLBinaryDescription, ...] = (
    DynTarNLBinaryDescription(key="price_negative_now", name="prijs negatief nu", icon="mdi:cash-minus", state_fn=_neg_now),
    DynTarNLBinaryDescription(key="price_negative_previous_hour", name="prijs negatief vorig uur", icon="mdi:cash-minus", state_fn=_neg_prev),
    DynTarNLBinaryDescription(key="price_negative_next_hour", name="prijs negatief volgend uur", icon="mdi:cash-minus", state_fn=_neg_next),
    DynTarNLBinaryDescription(key="feedin_costs_money_now", name="terugleveren kost geld nu", icon="mdi:transmission-tower-export", state_fn=_cost_now),
    DynTarNLBinaryDescription(key="feedin_costs_money_next_hour", name="terugleveren kost geld volgend uur", icon="mdi:transmission-tower-export", state_fn=_cost_next),
)


class DynTarNLTomorrowAvailable(DynTarNLEntity, BinarySensorEntity):
    _attr_name = "morgen beschikbaar"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, energy: str) -> None:
        super().__init__(coordinator, energy)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{energy}_tomorrow_available"

    @property
    def is_on(self) -> bool:
        return bool(self._data and self._data.tomorrow)


class DynTarNLFeedInBinary(DynTarNLEntity, BinarySensorEntity):
    entity_description: DynTarNLBinaryDescription

    def __init__(self, coordinator, description) -> None:
        super().__init__(coordinator, ELECTRICITY)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_electricity_{description.key}"

    @property
    def is_on(self) -> bool | None:
        data = self._data
        if data is None:
            return None
        return self.entity_description.state_fn(data, dt_util.now())


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DynTarNLConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        DynTarNLTomorrowAvailable(coordinator, energy) for energy in (ELECTRICITY, GAS)
    ]
    entities.extend(DynTarNLFeedInBinary(coordinator, d) for d in _BINARY_SENSORS)
    async_add_entities(entities)
