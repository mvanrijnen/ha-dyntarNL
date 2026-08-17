"""Sensor-entiteiten voor DynTarNL."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CURRENCY_EURO, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.util import dt as dt_util

from .const import ELECTRICITY, GAS
from .coordinator import DynTarNLConfigEntry
from .entity import DynTarNLEntity
from .model import EnergyData, Slot
from .prices import feed_in_value, feed_in_value_ex, slot_at as _slot_at

type PriceFn = Callable[[Slot], float]
type ValueFn = Callable[[EnergyData, datetime, PriceFn], float | None]
type AttrFn = Callable[[EnergyData, datetime, PriceFn], dict]

PRICE_PRECISION = 5


# --- Prijs-metrieken --------------------------------------------------------


def _current(data: EnergyData, now: datetime, price: PriceFn) -> float | None:
    slot = _slot_at(data.today, now)
    return price(slot) if slot else None


def _next_hour(data: EnergyData, now: datetime, price: PriceFn) -> float | None:
    slot = _slot_at(data.today + (data.tomorrow or []), now + timedelta(hours=1))
    return price(slot) if slot else None


def _previous_hour(data: EnergyData, now: datetime, price: PriceFn) -> float | None:
    slot = _slot_at((data.yesterday or []) + data.today, now - timedelta(hours=1))
    return price(slot) if slot else None


def _today_min(data: EnergyData, now: datetime, price: PriceFn) -> float | None:
    return min((price(s) for s in data.today), default=None)


def _today_max(data: EnergyData, now: datetime, price: PriceFn) -> float | None:
    return max((price(s) for s in data.today), default=None)


def _today_avg(data: EnergyData, now: datetime, price: PriceFn) -> float | None:
    if not data.today:
        return None
    return sum(price(s) for s in data.today) / len(data.today)


def _tomorrow_min(data: EnergyData, now: datetime, price: PriceFn) -> float | None:
    return min((price(s) for s in data.tomorrow), default=None) if data.tomorrow else None


def _tomorrow_max(data: EnergyData, now: datetime, price: PriceFn) -> float | None:
    return max((price(s) for s in data.tomorrow), default=None) if data.tomorrow else None


def _attributes(data: EnergyData, now: datetime, price: PriceFn) -> dict:
    def series(slots: list[Slot]) -> list[dict]:
        return [
            {"start": s.start.isoformat(), "end": s.end.isoformat(), "price": round(price(s), PRICE_PRECISION)}
            for s in slots
        ]

    attrs: dict = {
        "unit": data.unit,
        "vat_percentage": data.vat_percentage,
        "today": series(data.today),
        "tomorrow": series(data.tomorrow) if data.tomorrow else None,
    }
    current = _slot_at(data.today, now)
    if current is not None:
        attrs["market_price"] = round(current.market, PRICE_PRECISION)
        attrs["purchase_fee"] = round(current.fee, PRICE_PRECISION)
        attrs["energy_tax"] = round(current.tax, PRICE_PRECISION)
    return attrs


@dataclass(frozen=True, kw_only=True)
class DynTarNLSensorDescription(SensorEntityDescription):
    value_fn: ValueFn
    attr_fn: AttrFn | None = None


_METRICS: tuple[tuple[str, str, str, ValueFn, AttrFn | None], ...] = (
    ("previous_hour", "vorig uur", "mdi:cash-clock", _previous_hour, None),
    ("current", "huidige prijs", "mdi:cash", _current, _attributes),
    ("next_hour", "volgend uur", "mdi:cash-clock", _next_hour, None),
    ("today_min", "vandaag laagste", "mdi:arrow-down-bold", _today_min, None),
    ("today_avg", "vandaag gemiddeld", "mdi:approximately-equal", _today_avg, None),
    ("today_max", "vandaag hoogste", "mdi:arrow-up-bold", _today_max, None),
    ("tomorrow_min", "morgen laagste", "mdi:arrow-down-bold-outline", _tomorrow_min, None),
    ("tomorrow_max", "morgen hoogste", "mdi:arrow-up-bold-outline", _tomorrow_max, None),
)

_BASES: tuple[tuple[str, str, PriceFn], ...] = (
    ("total", "all-in", lambda s: s.total),
    ("market", "beurs", lambda s: s.market),
)


class DynTarNLPriceSensor(DynTarNLEntity, SensorEntity):
    entity_description: DynTarNLSensorDescription
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 5

    def __init__(self, coordinator, energy, basis_key, price_fn, description) -> None:
        super().__init__(coordinator, energy)
        self.entity_description = description
        self._price = price_fn
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{energy}_{basis_key}_{description.key}"
        )

    @property
    def native_unit_of_measurement(self) -> str:
        unit = self._data.unit if self._data else "kWh"
        return f"{CURRENCY_EURO}/{unit}"

    @property
    def native_value(self) -> float | None:
        data = self._data
        if data is None:
            return None
        value = self.entity_description.value_fn(data, dt_util.now(), self._price)
        return None if value is None else round(value, PRICE_PRECISION)

    @property
    def extra_state_attributes(self) -> dict | None:
        data = self._data
        if data is None or self.entity_description.attr_fn is None:
            return None
        return self.entity_description.attr_fn(data, dt_util.now(), self._price)


# --- Component-sensoren (belasting/opslag, incl/excl btw) -------------------


@dataclass(frozen=True, kw_only=True)
class DynTarNLComponentDescription(SensorEntityDescription):
    value_fn: PriceFn


_COMPONENTS: tuple[tuple[str, str, str, PriceFn, PriceFn], ...] = (
    ("energy_tax", "energiebelasting", "mdi:bank", lambda s: s.tax, lambda s: s.tax_ex),
    ("purchase_fee", "inkoopvergoeding", "mdi:cash-plus", lambda s: s.fee, lambda s: s.fee_ex),
)


def _component_descriptions() -> list[DynTarNLComponentDescription]:
    out: list[DynTarNLComponentDescription] = []
    for base_key, label, icon, incl_fn, excl_fn in _COMPONENTS:
        out.append(DynTarNLComponentDescription(key=f"{base_key}_incl_vat", name=f"{label} incl btw", icon=icon, suggested_display_precision=5, value_fn=incl_fn))
        out.append(DynTarNLComponentDescription(key=f"{base_key}_excl_vat", name=f"{label} excl btw", icon=icon, suggested_display_precision=5, value_fn=excl_fn))
    return out


_COMPONENT_SENSORS = _component_descriptions()


class DynTarNLComponentSensor(DynTarNLEntity, SensorEntity):
    entity_description: DynTarNLComponentDescription
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, energy, description) -> None:
        super().__init__(coordinator, energy)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{energy}_{description.key}"

    @property
    def native_unit_of_measurement(self) -> str:
        unit = self._data.unit if self._data else "kWh"
        return f"{CURRENCY_EURO}/{unit}"

    @property
    def native_value(self) -> float | None:
        data = self._data
        if data is None:
            return None
        slot = _slot_at(data.today, dt_util.now())
        return round(self.entity_description.value_fn(slot), PRICE_PRECISION) if slot else None


# --- Teruglever-sensoren (elektra) -----------------------------------------

type FeedInFn = Callable[[EnergyData, datetime], float | None]


def _feedin_compensation(data: EnergyData, now: datetime) -> float | None:
    slot = _slot_at(data.today, now)
    return feed_in_value(slot) if slot else None


def _feedin_cost_now(data: EnergyData, now: datetime) -> float | None:
    slot = _slot_at(data.today, now)
    return max(0.0, -feed_in_value(slot)) if slot else None


def _feedin_cost_next(data: EnergyData, now: datetime) -> float | None:
    slot = _slot_at(data.today + (data.tomorrow or []), now + timedelta(hours=1))
    return max(0.0, -feed_in_value(slot)) if slot else None


def _negative_hours_today(data: EnergyData, now: datetime) -> float:
    return float(sum(1 for s in data.today if s.market < 0))


def _feedin_cost_hours_today(data: EnergyData, now: datetime) -> float:
    return float(sum(1 for s in data.today if feed_in_value(s) < 0))


@dataclass(frozen=True, kw_only=True)
class DynTarNLFeedInDescription(SensorEntityDescription):
    compute: FeedInFn
    is_hours: bool = False


_FEEDIN_SENSORS: tuple[DynTarNLFeedInDescription, ...] = (
    DynTarNLFeedInDescription(key="feedin_compensation_now", name="terugleververgoeding nu", icon="mdi:transmission-tower-export", suggested_display_precision=5, compute=_feedin_compensation),
    DynTarNLFeedInDescription(key="feedin_cost_now", name="terugleverkosten nu", icon="mdi:cash-minus", suggested_display_precision=5, compute=_feedin_cost_now),
    DynTarNLFeedInDescription(key="feedin_cost_next_hour", name="terugleverkosten volgend uur", icon="mdi:cash-minus", suggested_display_precision=5, compute=_feedin_cost_next),
    DynTarNLFeedInDescription(key="negative_hours_today", name="negatieve uren vandaag", icon="mdi:counter", suggested_display_precision=0, compute=_negative_hours_today, is_hours=True),
    DynTarNLFeedInDescription(key="feedin_cost_hours_today", name="uren terugleveren kost geld vandaag", icon="mdi:counter", suggested_display_precision=0, compute=_feedin_cost_hours_today, is_hours=True),
)


class DynTarNLFeedInSensor(DynTarNLEntity, SensorEntity):
    entity_description: DynTarNLFeedInDescription
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, description) -> None:
        super().__init__(coordinator, ELECTRICITY)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_electricity_{description.key}"

    @property
    def native_unit_of_measurement(self) -> str:
        if self.entity_description.is_hours:
            return UnitOfTime.HOURS
        unit = self._data.unit if self._data else "kWh"
        return f"{CURRENCY_EURO}/{unit}"

    @property
    def native_value(self) -> float | None:
        data = self._data
        if data is None:
            return None
        value = self.entity_description.compute(data, dt_util.now())
        if value is None:
            return None
        return round(value, 0 if self.entity_description.is_hours else PRICE_PRECISION)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DynTarNLConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for energy in (ELECTRICITY, GAS):
        for basis_key, basis_label, price_fn in _BASES:
            for key, label, icon, value_fn, attr_fn in _METRICS:
                description = DynTarNLSensorDescription(
                    key=f"{basis_key}_{key}", name=f"{basis_label} {label}", icon=icon,
                    value_fn=value_fn, attr_fn=attr_fn,
                )
                entities.append(DynTarNLPriceSensor(coordinator, energy, basis_key, price_fn, description))
        entities.extend(
            DynTarNLComponentSensor(coordinator, energy, d) for d in _COMPONENT_SENSORS
        )
    entities.extend(DynTarNLFeedInSensor(coordinator, d) for d in _FEEDIN_SENSORS)
    async_add_entities(entities)
