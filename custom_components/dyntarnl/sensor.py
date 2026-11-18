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
from homeassistant.const import CURRENCY_EURO, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homeassistant.util import dt as dt_util

from .const import DOMAIN, ELECTRICITY, GAS, NAME
from .coordinator import DynTarNLConfigEntry, DynTarNLCoordinator
from .entity import DynTarNLEntity
from .model import EnergyData, Slot
from .prices import feed_in_value, feed_in_value_ex, slot_at as _slot_at
from .tables import row_at, table
from .tariffs import period_at, period_summary

type PriceFn = Callable[[Slot], float]
type ValueFn = Callable[[EnergyData, datetime, PriceFn], float | None]
type AttrFn = Callable[[EnergyData, datetime, PriceFn], dict]

PRICE_PRECISION = 5


# --- Prijs-metrieken --------------------------------------------------------


def _current(data: EnergyData, now: datetime, price: PriceFn) -> float | None:
    slot = _slot_at(data.today, now)
    return price(slot) if slot else None


def _next_hour(data: EnergyData, now: datetime, price: PriceFn) -> float | None:
    slot = _slot_at(data.today + (data.tomorrow or []), dt_util.as_utc(now) + timedelta(hours=1))
    return price(slot) if slot else None


def _previous_hour(data: EnergyData, now: datetime, price: PriceFn) -> float | None:
    slot = _slot_at((data.yesterday or []) + data.today, dt_util.as_utc(now) - timedelta(hours=1))
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

    # Alle uren die de coordinator in cache heeft, op volgorde (gisteren t/m morgen).
    all_slots = (data.yesterday or []) + data.today + (data.tomorrow or [])

    attrs: dict = {
        "unit": data.unit,
        "vat_percentage": data.vat_percentage,
        "yesterday": series(data.yesterday) if data.yesterday else None,
        "today": series(data.today),
        "tomorrow": series(data.tomorrow) if data.tomorrow else None,
        # Kant-en-klare grafiekreeks over álle dagen: [epoch-ms, prijs]. Precies het
        # formaat dat ApexCharts verwacht, dus een kaart kan dit 1-op-1 plotten.
        "prices": [
            [int(s.start.timestamp() * 1000), round(price(s), PRICE_PRECISION)]
            for s in all_slots
        ],
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
    ("previous_hour", "prev", "mdi:cash-clock", _previous_hour, None),
    ("current", "now", "mdi:cash", _current, _attributes),
    ("next_hour", "next", "mdi:cash-clock", _next_hour, None),
    ("today_min", "today min", "mdi:arrow-down-bold", _today_min, None),
    ("today_avg", "today avg", "mdi:approximately-equal", _today_avg, None),
    ("today_max", "today max", "mdi:arrow-up-bold", _today_max, None),
    ("tomorrow_min", "tomorrow min", "mdi:arrow-down-bold-outline", _tomorrow_min, None),
    ("tomorrow_max", "tomorrow max", "mdi:arrow-up-bold-outline", _tomorrow_max, None),
)

_BASES: tuple[tuple[str, str, PriceFn], ...] = (
    ("total", "all in", lambda s: s.total),
    ("market", "market", lambda s: s.market),
)


class DynTarNLPriceSensor(DynTarNLEntity, SensorEntity):
    entity_description: DynTarNLSensorDescription
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 5
    # De dag-reeksen veranderen elk uur mee en zijn groot: niet in de recorder.
    _unrecorded_attributes = frozenset({"yesterday", "today", "tomorrow", "prices"})

    def __init__(self, coordinator, energy, basis_key, price_fn, description) -> None:
        super().__init__(coordinator, energy)
        self.entity_description = description
        self._price = price_fn
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{energy}_{basis_key}_{description.key}"
        )

    @property
    def native_unit_of_measurement(self) -> str:
        unit = self._data.unit if self._data else ("m³" if self._energy == GAS else "kWh")
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
    ("energy_tax", "tax", "mdi:bank", lambda s: s.tax, lambda s: s.tax_ex),
    ("purchase_fee", "markup", "mdi:cash-plus", lambda s: s.fee, lambda s: s.fee_ex),
)


def _component_descriptions() -> list[DynTarNLComponentDescription]:
    out: list[DynTarNLComponentDescription] = []
    for base_key, label, icon, incl_fn, excl_fn in _COMPONENTS:
        out.append(DynTarNLComponentDescription(key=f"{base_key}_incl_vat", name=f"{label} incl VAT", icon=icon, suggested_display_precision=5, value_fn=incl_fn))
        out.append(DynTarNLComponentDescription(key=f"{base_key}_excl_vat", name=f"{label} excl VAT", icon=icon, suggested_display_precision=5, value_fn=excl_fn))
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
        unit = self._data.unit if self._data else ("m³" if self._energy == GAS else "kWh")
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
    slot = _slot_at(data.today + (data.tomorrow or []), dt_util.as_utc(now) + timedelta(hours=1))
    return max(0.0, -feed_in_value(slot)) if slot else None


def _negative_hours_today(data: EnergyData, now: datetime) -> float:
    return float(sum(1 for s in data.today if s.market < 0))


def _feedin_cost_hours_today(data: EnergyData, now: datetime) -> float:
    return float(sum(1 for s in data.today if feed_in_value(s) < 0))


@dataclass(frozen=True, kw_only=True)
class DynTarNLFeedInDescription(SensorEntityDescription):
    compute: FeedInFn
    is_hours: bool = False
    # Optionele attributen, zelfde vorm als bij de prijssensoren, met attr_price als prijs.
    attr_fn: AttrFn | None = None
    attr_price: PriceFn = feed_in_value


_FEEDIN_SENSORS: tuple[DynTarNLFeedInDescription, ...] = (
    DynTarNLFeedInDescription(key="feedin_compensation_now", name="export price now", icon="mdi:transmission-tower-export", suggested_display_precision=5, compute=_feedin_compensation, attr_fn=_attributes),
    DynTarNLFeedInDescription(key="feedin_cost_now", name="export cost now", icon="mdi:cash-minus", suggested_display_precision=5, compute=_feedin_cost_now),
    DynTarNLFeedInDescription(key="feedin_cost_next_hour", name="export cost next", icon="mdi:cash-minus", suggested_display_precision=5, compute=_feedin_cost_next),
    DynTarNLFeedInDescription(key="negative_hours_today", name="neg hours today", icon="mdi:counter", suggested_display_precision=0, compute=_negative_hours_today, is_hours=True),
    DynTarNLFeedInDescription(key="feedin_cost_hours_today", name="export loss today", icon="mdi:counter", suggested_display_precision=0, compute=_feedin_cost_hours_today, is_hours=True),
)


class DynTarNLFeedInSensor(DynTarNLEntity, SensorEntity):
    entity_description: DynTarNLFeedInDescription
    _attr_state_class = SensorStateClass.MEASUREMENT
    # Alleen export price now heeft deze (grote, elk uur wisselende) reeksen.
    _unrecorded_attributes = frozenset({"yesterday", "today", "tomorrow", "prices"})

    def __init__(self, coordinator, description) -> None:
        super().__init__(coordinator, ELECTRICITY)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_electricity_{description.key}"

    @property
    def native_unit_of_measurement(self) -> str:
        if self.entity_description.is_hours:
            return UnitOfTime.HOURS
        unit = self._data.unit if self._data else ("m³" if self._energy == GAS else "kWh")
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

    @property
    def extra_state_attributes(self) -> dict | None:
        """Terugleverreeks (beurs − opslag) per uur, gisteren t/m morgen."""
        data = self._data
        description = self.entity_description
        if data is None or description.attr_fn is None:
            return None
        return description.attr_fn(data, dt_util.now(), description.attr_price)


class DynTarNLSellSensor(DynTarNLPriceSensor):
    """Verkoop (teruglevering): dezelfde metrieken als inkoop, op de verkoopprijs."""

    @property
    def _data(self) -> EnergyData | None:
        return self.coordinator.sell_data()


class DynTarNLSpreadSensor(DynTarNLEntity, SensorEntity):
    """Inkoop − verkoop voor het huidige slot (0 zolang saldering geldt)."""

    _attr_name = "spread now"
    _attr_icon = "mdi:arrow-expand-vertical"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 5
    _attr_native_unit_of_measurement = f"{CURRENCY_EURO}/kWh"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, ELECTRICITY)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_electricity_spread_current"

    @property
    def native_value(self) -> float | None:
        row = row_at(self.coordinator.rows(ELECTRICITY)[0], dt_util.now())
        if row is None or row.buy is None or row.sell is None:
            return None
        return round(row.buy.total - row.sell.total, PRICE_PRECISION)


class DynTarNLTableSensor(DynTarNLEntity, SensorEntity):
    """De volledige inkoop- of verkooptabel (compact kolomformaat); state = prijs van nu.

    Gepubliceerd én (met voorspellingen aan) voorspeld, per slot met bron en opbouw. De
    tabel gaat niet de recorder in.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 5
    _unrecorded_attributes = frozenset({"rows", "sources", "fields", "start", "start_local"})

    def __init__(self, coordinator, energy: str, side: str) -> None:
        super().__init__(coordinator, energy)
        self._side = side
        self._attr_name = f"{side} table"
        self._attr_icon = "mdi:table-arrow-down" if side == "buy" else "mdi:table-arrow-up"
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{energy}_{side}_table"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        fc = self.coordinator.forecast
        if fc is not None and self._energy == ELECTRICITY:
            # Nieuwe voorspelling = nieuwe tabel, ook zonder nieuwe gepubliceerde prijzen.
            self.async_on_remove(fc.async_add_listener(self.async_write_ha_state))

    @property
    def native_unit_of_measurement(self) -> str:
        unit = self._data.unit if self._data else ("m³" if self._energy == GAS else "kWh")
        return f"{CURRENCY_EURO}/{unit}"

    @property
    def native_value(self) -> float | None:
        row = row_at(self.coordinator.rows(self._energy)[0], dt_util.now())
        slot = None if row is None else (row.buy if self._side == "buy" else row.sell)
        return None if slot is None else round(slot.total, PRICE_PRECISION)

    @property
    def extra_state_attributes(self) -> dict | None:
        rows, step = self.coordinator.rows(self._energy)
        if not rows:
            return None
        unit = self._data.unit if self._data else "kWh"
        supplier = self.coordinator.supplier.name if self.coordinator.supplier else ""
        return table(rows, step, self._side, unit, supplier)


class DynTarNLTariffPeriodSensor(CoordinatorEntity[DynTarNLCoordinator], SensorEntity):
    """Diagnostiek: welke tariefperiode nu geldt en welke eraan komt."""

    _attr_has_entity_name = True
    _attr_name = "tariff period"
    _attr_icon = "mdi:calendar-range"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_tariff_period"
        supplier = coordinator.supplier
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_service")},
            name=NAME,
            manufacturer=supplier.name if supplier else NAME,
            model="Dynamic tariffs",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str | None:
        current = period_at(self.coordinator.periods, dt_util.now())
        return None if current is None else current.start.isoformat()

    @property
    def extra_state_attributes(self) -> dict:
        return period_summary(self.coordinator.periods, dt_util.now(), self.coordinator.custom)


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
    # Verkoop: dezelfde metrieken als inkoop (zonder de grote reeks-attributen; die staan in
    # de tabel), plus spread, de tabellen en de diagnostiek van de tariefperiode.
    for key, label, icon, value_fn, _attr_fn in _METRICS:
        description = DynTarNLSensorDescription(
            key=f"sell_{key}", name=f"sell {label}", icon=icon, value_fn=value_fn
        )
        entities.append(DynTarNLSellSensor(coordinator, ELECTRICITY, "sell", lambda s: s.total, description))
    entities.append(DynTarNLSpreadSensor(coordinator))
    entities.extend(
        DynTarNLTableSensor(coordinator, energy, side)
        for energy, side in ((ELECTRICITY, "buy"), (ELECTRICITY, "sell"), (GAS, "buy"))
    )
    entities.append(DynTarNLTariffPeriodSensor(coordinator))
    if coordinator.forecast is not None:
        # Alleen als de voorspel-optie aan staat; anders exact de oude set.
        from .forecast.sensor import forecast_entities

        entities.extend(forecast_entities(coordinator.forecast))
    async_add_entities(entities)
