"""Entiteiten van de voorspellaag. Worden alleen aangemaakt als de optie aan staat.

- Op het device 'DynTarNL E': drie compacte sensoren (goedkoopste blok, gemiddelde
  komende 24 uur, gemiddelde morgen). De volledige reeks staat in een NIET-gerecord
  attribuut; de primaire manier om reeksen op te halen is `dyntarnl.get_prices`.
- Op een eigen device 'DynTarNL Forecast': diagnostiek per provider en ensemble.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CURRENCY_EURO, EntityCategory
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from ..const import DOMAIN, ELECTRICITY, ENSEMBLE, FC_CHEAPEST_LOOKAHEAD_H, LEAD_BUCKETS, NAME
from .coordinator import DynTarNLForecastCoordinator
from .model import ForecastConfig
from .views import chart_series, cheapest_block, forecast_chart, next_24h, tomorrow

PRICE_UNIT = f"{CURRENCY_EURO}/kWh"
FIRST_BUCKET = LEAD_BUCKETS[0][0]

type Coord = DynTarNLForecastCoordinator
type ValueFn = Callable[[Coord], Any]
type AttrFn = Callable[[Coord], dict | None]


@dataclass(frozen=True, kw_only=True)
class ForecastSensorDescription(SensorEntityDescription):
    value_fn: ValueFn
    attr_fn: AttrFn | None = None
    on_energy_device: bool = False   # True = op 'DynTarNL E', anders 'DynTarNL Forecast'
    chart: bool = False              # True = draagt de (niet-gerecorde) reeks


def unique_suffix(description: ForecastSensorDescription) -> str:
    if description.on_energy_device:
        return f"{ELECTRICITY}_{description.key}"
    return description.key


def _r(value: float | None, digits: int = 5) -> float | None:
    return None if value is None else round(value, digits)


def _clean(info: dict | None, drop: tuple[str, ...] = ()) -> dict | None:
    if info is None:
        return None
    out = {}
    for k, v in info.items():
        if k in drop:
            continue
        if isinstance(v, datetime):
            v = dt_util.as_local(v).isoformat()
        elif isinstance(v, float):
            v = round(v, 5)
        out[k] = v
    return out


# --- compacte sensoren ------------------------------------------------------


def _merged(c: Coord):
    return c.data.merged if c.data else []


def _cheapest(c: Coord) -> dict | None:
    return cheapest_block(_merged(c), dt_util.utcnow(), c.cfg.cheapest_hours, FC_CHEAPEST_LOOKAHEAD_H)


def _avg_attrs(c: Coord) -> dict | None:
    merged = _merged(c)
    info = _clean(next_24h(merged, dt_util.utcnow()), drop=("avg_price_allin",)) or {}
    prices, band = chart_series(merged)
    forecast, forecast_market = forecast_chart(merged, c.data.tariff if c.data else None)
    forecasts = [r for r in merged if r.source != "published"]
    info.update(
        {
            "prices": prices,
            "error_band": band,
            "forecast": forecast,
            "forecast_market": forecast_market,
            "forecast_until": dt_util.as_local(forecasts[-1].end).isoformat() if forecasts else None,
        }
    )
    return info


def _value(info: dict | None, key: str = "avg_price_allin") -> float | None:
    return None if info is None else _r(info.get(key))


ENERGY_SENSORS: tuple[ForecastSensorDescription, ...] = (
    ForecastSensorDescription(
        key="forecast_cheapest_start",
        name="cheapest block start",
        icon="mdi:clock-star-four-points-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        on_energy_device=True,
        value_fn=lambda c: (b := _cheapest(c)) and b["start"],
        attr_fn=lambda c: _clean(_cheapest(c), drop=("start",)),
    ),
    ForecastSensorDescription(
        key="forecast_total_avg_24h",
        name="all in forecast avg",
        icon="mdi:chart-bell-curve-cumulative",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=5,
        on_energy_device=True,
        chart=True,
        value_fn=lambda c: _value(next_24h(_merged(c), dt_util.utcnow())),
        attr_fn=_avg_attrs,
    ),
    ForecastSensorDescription(
        key="forecast_total_tomorrow_avg",
        name="tomorrow avg forecast",
        icon="mdi:calendar-arrow-right",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=5,
        on_energy_device=True,
        value_fn=lambda c: _value(tomorrow(_merged(c), dt_util.utcnow())),
        attr_fn=lambda c: _clean(tomorrow(_merged(c), dt_util.utcnow()), drop=("avg_price_allin",)),
    ),
)


# --- diagnostiek per provider / ensemble ------------------------------------


def _bucket_attrs(c: Coord, provider: str) -> dict:
    out = {}
    for bucket, _, _ in LEAD_BUCKETS:
        stat = c.tracker.provider_stat(provider, bucket) if c.tracker else None
        out[bucket] = (
            {"mae": _r(stat.mae, 6), "bias": _r(stat.bias, 6), "settled": stat.count} if stat else None
        )
    return {"buckets": out, "window_days": c.cfg.window_days, "unit": PRICE_UNIT}


def _stat(c: Coord, provider: str, field: str) -> float | None:
    stat = c.tracker.provider_stat(provider, FIRST_BUCKET) if c.tracker else None
    return None if stat is None else _r(getattr(stat, field), 6)


def _settled(c: Coord, provider: str) -> int | None:
    stat = c.tracker.provider_window(provider) if c.tracker else None
    return stat.count if stat else 0


def _settled_attrs(c: Coord, provider: str) -> dict:
    total = c.tracker.provider_total(provider) if c.tracker else None
    return {"settled_total": total.count if total else 0, "window_days": c.cfg.window_days}


def _status_attrs(c: Coord, provider: str) -> dict:
    st = c.status.get(provider)
    series = c.series.get(provider)
    iso = lambda d: dt_util.as_local(d).isoformat() if d else None  # noqa: E731
    return {
        "status": st.state if st else None,
        "last_attempt": iso(st.last_attempt) if st else None,
        "last_error": st.last_error if st else None,
        "failures": st.failures if st else None,
        "next_attempt": iso(st.next_attempt) if st else None,
        "points": len(series.points) if series else 0,
        "expanded_points": sum(p.expanded for p in series.points) if series else 0,
        "forecast_until": iso(series.points[-1].start + timedelta(minutes=15)) if series and series.points else None,
    }


def _ensemble_attrs(c: Coord) -> dict:
    out: dict = {"buckets": {}, "by_sources": {}, "window_days": c.cfg.window_days, "unit": PRICE_UNIT}
    if c.tracker is None:
        return out
    for bucket, _, _ in LEAD_BUCKETS:
        stat = c.tracker.ensemble_stat(bucket)
        out["buckets"][bucket] = (
            {"mae": _r(stat.mae, 6), "bias": _r(stat.bias, 6), "settled": stat.count} if stat else None
        )
    for bucket, per_n in c.tracker.ensemble_by_n().items():
        out["by_sources"][bucket] = {
            str(n): {"mae": _r(s.mae, 6), "settled": s.count} for n, s in per_n.items()
        }
    return out


def _diag(key: str, name: str, icon: str, value_fn: ValueFn, attr_fn: AttrFn | None = None, **kw) -> ForecastSensorDescription:
    return ForecastSensorDescription(
        key=key, name=name, icon=icon, entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=value_fn, attr_fn=attr_fn, **kw,
    )


def diagnostic_descriptions(cfg: ForecastConfig) -> list[ForecastSensorDescription]:
    out: list[ForecastSensorDescription] = []
    for p in cfg.providers:
        if cfg.accuracy:
            out += [
                _diag(f"forecast_{p}_mae", f"{p} mae", "mdi:target", lambda c, p=p: _stat(c, p, "mae"),
                      lambda c, p=p: _bucket_attrs(c, p), native_unit_of_measurement=PRICE_UNIT,
                      state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=4),
                _diag(f"forecast_{p}_bias", f"{p} bias", "mdi:scale-unbalanced", lambda c, p=p: _stat(c, p, "bias"),
                      lambda c, p=p: _bucket_attrs(c, p), native_unit_of_measurement=PRICE_UNIT,
                      state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=4),
                _diag(f"forecast_{p}_settled", f"{p} settled", "mdi:counter", lambda c, p=p: _settled(c, p),
                      lambda c, p=p: _settled_attrs(c, p)),
            ]
        if cfg.ensemble:
            out.append(
                _diag(f"forecast_{p}_weight", f"{p} weight", "mdi:weight",
                      lambda c, p=p: _r(100 * c.data.weights.get(p, {}).get(FIRST_BUCKET, 0), 1) if c.data else None,
                      lambda c, p=p: {"buckets": {b: _r(w, 4) for b, w in (c.data.weights.get(p, {}) if c.data else {}).items()}},
                      native_unit_of_measurement="%", suggested_display_precision=1)
            )
        out.append(
            _diag(f"forecast_{p}_last_fetch", f"{p} last fetch", "mdi:cloud-download-outline",
                  lambda c, p=p: c.status[p].last_success if p in c.status else None,
                  lambda c, p=p: _status_attrs(c, p), device_class=SensorDeviceClass.TIMESTAMP)
        )
    if cfg.ensemble and cfg.accuracy:
        out += [
            _diag(f"forecast_{ENSEMBLE}_mae", f"{ENSEMBLE} mae", "mdi:target",
                  lambda c: _r(s.mae, 6) if c.tracker and (s := c.tracker.ensemble_stat(FIRST_BUCKET)) else None,
                  _ensemble_attrs, native_unit_of_measurement=PRICE_UNIT,
                  state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=4),
            _diag(f"forecast_{ENSEMBLE}_settled", f"{ENSEMBLE} settled", "mdi:counter",
                  lambda c: sum(s.count for b, _, _ in LEAD_BUCKETS if c.tracker and (s := c.tracker.ensemble_stat(b))),
                  None),
        ]
    return out


def forecast_descriptions(cfg: ForecastConfig) -> list[ForecastSensorDescription]:
    if not cfg.enabled:
        return []
    return [*ENERGY_SENSORS, *diagnostic_descriptions(cfg)]


def forecast_unique_ids(entry_id: str, cfg: ForecastConfig) -> set[str]:
    return {f"{entry_id}_{unique_suffix(d)}" for d in forecast_descriptions(cfg)}


def is_forecast_unique_id(entry_id: str, unique_id: str) -> bool:
    return unique_id.startswith((f"{entry_id}_forecast_", f"{entry_id}_{ELECTRICITY}_forecast_"))


# --- entiteiten ----------------------------------------------------------------


class DynTarNLForecastSensor(CoordinatorEntity[DynTarNLForecastCoordinator], SensorEntity):
    entity_description: ForecastSensorDescription
    _attr_has_entity_name = True

    def __init__(self, coordinator: Coord, description: ForecastSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{unique_suffix(description)}"
        manufacturer = coordinator.supplier_name or NAME
        if description.on_energy_device:
            # Zelfde device als de bestaande stroomsensoren (zie entity.py).
            self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, f"{entry_id}_{ELECTRICITY}")})
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{entry_id}_forecast")},
                name=f"{NAME} Forecast",
                manufacturer=manufacturer,
                model="Price forecast",
                entry_type=DeviceEntryType.SERVICE,
            )

    @property
    def native_value(self) -> Any:
        try:
            return self.entity_description.value_fn(self.coordinator)
        except Exception:  # noqa: BLE001 - een diagnose-sensor mag nooit crashen
            return None

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.entity_description.attr_fn is None:
            return None
        try:
            return self.entity_description.attr_fn(self.coordinator)
        except Exception:  # noqa: BLE001
            return None


class DynTarNLForecastChartSensor(DynTarNLForecastSensor):
    """Draagt de volledige reeks; die gaat NIET de recorder in."""

    _unrecorded_attributes = frozenset({"prices", "error_band", "forecast", "forecast_market"})


def forecast_entities(coordinator: Coord) -> list[SensorEntity]:
    return [
        (DynTarNLForecastChartSensor if d.chart else DynTarNLForecastSensor)(coordinator, d)
        for d in forecast_descriptions(coordinator.cfg)
    ]
