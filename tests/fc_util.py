"""Hulpjes voor de voorspel-tests (synthetische prijzen rond DEFAULT_NOW)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dyntarnl.const import CONF_FORECAST, ELECTRICITY
from dyntarnl.forecast.model import QUARTER, ForecastConfig, ForecastPoint, ProviderSeries
from dyntarnl.model import EnergyData
from dyntarnl.prices import Tariff, apply_formula

UTC = timezone.utc
AMS = timezone(timedelta(hours=2))
NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)  # = DEFAULT_NOW (14:00 lokaal)
TARIFF = Tariff(fee_ex=0.02, tax_ex=0.09161, vat=21.0)
# Gepubliceerd: vandaag (lokaal) = 16-08 22:00 UTC t/m 17-08 22:00 UTC.
DAY_START = datetime(2026, 8, 16, 22, 0, tzinfo=UTC)


def hour_slots(start: datetime, prices: list[float], tariff: Tariff = TARIFF):
    """Uur-slots (lokale tijden, zoals de bronnen ze leveren) via de formule."""
    out = []
    for i, market_ex in enumerate(prices):
        s = (start + timedelta(hours=i)).astimezone(AMS)
        out.append(apply_formula(s, s + timedelta(hours=1), market_ex, tariff))
    return out


def prices_data(prices: list[float] | None = None, tomorrow: list[float] | None = None) -> dict:
    today = hour_slots(DAY_START, prices if prices is not None else [0.10] * 24)
    tmw = hour_slots(DAY_START + timedelta(days=1), tomorrow) if tomorrow else None
    return {ELECTRICITY: EnergyData(unit="kWh", vat_percentage=21.0, today=today, tomorrow=tmw)}


def series(provider: str, fetched_at: datetime, start: datetime, values: list[float], expanded=False):
    points = [ForecastPoint(start + i * QUARTER, v, expanded) for i, v in enumerate(values)]
    return ProviderSeries(provider, fetched_at, points)


def cfg(**options) -> ForecastConfig:
    return ForecastConfig.from_options({CONF_FORECAST: True, **options})
