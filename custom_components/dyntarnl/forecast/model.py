"""Datamodel van de voorspellaag: alles per kwartier, intern in UTC."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from ..const import (
    CONF_FC_ACCURACY,
    CONF_FC_BIAS,
    CONF_FC_CHEAPEST_HOURS,
    CONF_FC_ENSEMBLE,
    CONF_FC_HORIZON,
    CONF_FC_INTERVAL,
    CONF_FC_MIN_SAMPLES,
    CONF_FC_PROVIDERS,
    CONF_FC_WEIGHT_EXPONENT,
    CONF_FC_WEIGHT_FLOOR,
    CONF_FC_WEIGHTING,
    CONF_FC_WINDOW,
    CONF_FORECAST,
    FC_MAX_HORIZON,
    FORECAST_DEFAULTS,
    LEAD_BUCKETS,
    WEIGHTING_ACCURACY,
)

QUARTER = timedelta(minutes=15)
SOURCE_PUBLISHED = "published"
SOURCE_FORECAST = "forecast"


def floor_quarter(moment: datetime) -> datetime:
    """Rond af naar het begin van het kwartier, in UTC."""
    moment = dt_util.as_utc(moment)
    return moment.replace(minute=moment.minute - moment.minute % 15, second=0, microsecond=0)


def lead_bucket(target: datetime, fetched_at: datetime) -> str | None:
    """Looptijd-bucket van een voorspelling (doelkwartier − ophaalmoment)."""
    hours = (target - fetched_at).total_seconds() / 3600
    for key, low, high in LEAD_BUCKETS:
        if low <= hours < high:
            return key
    return None


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    """Eén voorspeld kwartier van één provider: kale EPEX in €/kWh excl. btw."""

    start: datetime          # UTC, begin van het kwartier
    price_raw: float
    expanded: bool = False   # True = uit een uurwaarde over 4 kwartieren verdeeld


@dataclass(slots=True)
class ProviderSeries:
    provider: str
    fetched_at: datetime     # UTC
    points: list[ForecastPoint]

    def by_start(self) -> dict[datetime, ForecastPoint]:
        return {p.start: p for p in self.points}


@dataclass(slots=True)
class QuarterRecord:
    """Eén kwartier in de samengevoegde reeks (gepubliceerd óf voorspeld)."""

    start: datetime          # UTC
    end: datetime            # UTC
    price_raw: float         # kale EPEX, excl. btw
    price_allin: float | None
    source: str              # SOURCE_PUBLISHED / SOURCE_FORECAST
    supplier: str | None = None
    providers: tuple[str, ...] = ()
    fetched_at: datetime | None = None
    expanded: bool = False
    n_sources: int | None = None
    spread_min: float | None = None
    spread_max: float | None = None
    expected_error_raw: float | None = None
    expected_error_allin: float | None = None

    def as_dict(self) -> dict:
        """Voor de service-respons: tijden in lokale ISO-notatie."""

        def r(value: float | None) -> float | None:
            return None if value is None else round(value, 6)

        out: dict = {
            "start": dt_util.as_local(self.start).isoformat(),
            "end": dt_util.as_local(self.end).isoformat(),
            "price_raw": r(self.price_raw),
            "price_allin": r(self.price_allin),
            "source": self.source,
            "expanded": self.expanded,
        }
        if self.source == SOURCE_PUBLISHED:
            out["supplier"] = self.supplier
        else:
            out["providers"] = list(self.providers)
            out["fetched_at"] = (
                dt_util.as_local(self.fetched_at).isoformat() if self.fetched_at else None
            )
            out["n_sources"] = self.n_sources
            out["spread_min"] = r(self.spread_min)
            out["spread_max"] = r(self.spread_max)
            out["expected_error_raw"] = r(self.expected_error_raw)
            out["expected_error_allin"] = r(self.expected_error_allin)
        return out


@dataclass(frozen=True, slots=True)
class ForecastConfig:
    """Alle voorspel-opties uit entry.options, met standaardwaarden aangevuld."""

    enabled: bool = False
    providers: tuple[str, ...] = ()
    ensemble: bool = True
    weighting: str = WEIGHTING_ACCURACY
    weight_exponent: int = 1
    accuracy: bool = True
    horizon_hours: int = 72
    interval_hours: float = 6
    min_samples: int = 96
    weight_floor: float = 0.10
    bias_correction: bool = False
    window_days: int = 28
    cheapest_hours: int = 3
    options: dict = field(default_factory=dict)  # ruwe opties (provider-specifiek)

    @classmethod
    def from_options(cls, options: dict | None) -> ForecastConfig:
        o = {**FORECAST_DEFAULTS, **(options or {})}
        return cls(
            enabled=bool(o[CONF_FORECAST]),
            providers=tuple(o[CONF_FC_PROVIDERS] or ()),
            ensemble=bool(o[CONF_FC_ENSEMBLE]),
            weighting=str(o[CONF_FC_WEIGHTING]),
            weight_exponent=int(o[CONF_FC_WEIGHT_EXPONENT]),
            accuracy=bool(o[CONF_FC_ACCURACY]),
            horizon_hours=min(int(o[CONF_FC_HORIZON]), FC_MAX_HORIZON),
            interval_hours=float(o[CONF_FC_INTERVAL]),
            min_samples=int(o[CONF_FC_MIN_SAMPLES]),
            weight_floor=float(o[CONF_FC_WEIGHT_FLOOR]),
            bias_correction=bool(o[CONF_FC_BIAS]),
            window_days=int(o[CONF_FC_WINDOW]),
            cheapest_hours=int(o[CONF_FC_CHEAPEST_HOURS]),
            options=o,
        )
