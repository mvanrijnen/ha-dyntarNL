"""Engine: bouwt uit gepubliceerde prijzen + providerreeksen de kwartierreeksen.

Puur (geen netwerk): wordt aangeroepen na elke fetch én na elke update van de
gewone prijzen. Regels:
- Gepubliceerde prijzen winnen altijd; een voorspelling vult alleen kwartieren
  zonder gepubliceerde prijs.
- price_allin komt uit `apply_formula`: exact dezelfde functie als CUSTOM, met de
  formule afgeleid uit het laatst gepubliceerde stroom-uur.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from ..const import ELECTRICITY, FC_SERIES_MAX_AGE_H, LEAD_BUCKETS
from ..model import EnergyData, PriceData
from ..prices import Tariff, apply_formula, tariff_from_slot
from .accuracy import AccuracyTracker
from .ensemble import Combined, Stat, combine, compute_weights, expected_error
from .model import (
    QUARTER,
    SOURCE_FORECAST,
    SOURCE_PUBLISHED,
    ForecastConfig,
    ProviderSeries,
    QuarterRecord,
    floor_quarter,
    lead_bucket,
)


@dataclass(slots=True)
class ForecastData:
    generated_at: datetime
    tariff: Tariff | None
    published: list[QuarterRecord]
    providers: dict[str, list[QuarterRecord]]
    ensemble: list[QuarterRecord] | None
    merged: list[QuarterRecord]
    #: Huidig gewicht per provider per looptijd-bucket (voor diagnostiek).
    weights: dict[str, dict[str, float]] = field(default_factory=dict)


def all_slots(ed: EnergyData | None) -> list:
    if ed is None:
        return []
    return (ed.yesterday or []) + ed.today + (ed.tomorrow or [])


def published_records(ed: EnergyData | None, supplier: str, quarters: bool = True) -> list[QuarterRecord]:
    """Gepubliceerde slots als records. Stroom per kwartier (uur → 4× `expanded`),
    gas op de eigen resolutie (een gasdag als één record)."""
    out: list[QuarterRecord] = []
    for s in all_slots(ed):
        start, end = dt_util.as_utc(s.start), dt_util.as_utc(s.end)
        steps = max(1, int((end - start) / QUARTER)) if quarters else 1
        step = (end - start) / steps
        for i in range(steps):
            out.append(
                QuarterRecord(
                    start=start + i * step,
                    end=start + (i + 1) * step,
                    price_raw=s.market_ex,
                    price_allin=s.total,
                    source=SOURCE_PUBLISHED,
                    supplier=supplier,
                    expanded=steps > 1 and quarters,
                )
            )
    return out


def merge(published: list[QuarterRecord], forecast: list[QuarterRecord]) -> list[QuarterRecord]:
    """Gepubliceerd wint altijd: een voorspelling vult alleen lege kwartieren."""
    by_start = {r.start: r for r in forecast}
    by_start.update({r.start: r for r in published})
    return [by_start[k] for k in sorted(by_start)]


def current_tariff(ed: EnergyData | None) -> Tariff | None:
    """Formule uit het laatst gepubliceerde uur (opslag/EB/btw van de leverancier)."""
    slots = all_slots(ed)
    if not slots:
        return None
    return tariff_from_slot(max(slots, key=lambda s: s.start), ed.vat_percentage)


def _allin(start: datetime, raw: float, tariff: Tariff | None) -> float | None:
    if tariff is None:
        return None
    return apply_formula(start, start + QUARTER, raw, tariff).total


def _scale(error: float | None, tariff: Tariff | None) -> float | None:
    """All-in marge = kale marge × btw-factor (vaste opslag verandert de marge niet)."""
    if error is None or tariff is None:
        return None
    return error * tariff.factor


def _usable(stat: Stat | None, cfg: ForecastConfig) -> Stat | None:
    return stat if stat is not None and stat.count >= cfg.min_samples else None


def ensemble_mae(
    tracker: AccuracyTracker | None, bucket: str | None, n: int, providers: tuple[str, ...], cfg: ForecastConfig
) -> float | None:
    """Gemeten ensemble-MAE voor (bucket, n); terugvallen op (bucket, alle n) en
    daarna op de gemiddelde provider-MAE in die bucket."""
    if tracker is None or bucket is None:
        return None
    for stat in (tracker.ensemble_stat(bucket, n), tracker.ensemble_stat(bucket)):
        if _usable(stat, cfg):
            return stat.mae
    maes = [s.mae for p in providers if (s := _usable(tracker.provider_stat(p, bucket), cfg))]
    return sum(maes) / len(maes) if maes else None


def build(
    prices: PriceData | None,
    series: dict[str, ProviderSeries],
    tracker: AccuracyTracker | None,
    cfg: ForecastConfig,
    supplier: str,
    now: datetime,
) -> tuple[ForecastData, dict[datetime, Combined]]:
    now = dt_util.as_utc(now)
    ed = (prices or {}).get(ELECTRICITY)
    tariff = current_tariff(ed)
    published = published_records(ed, supplier)

    first = floor_quarter(now)
    last = first + timedelta(hours=cfg.horizon_hours)
    max_age = now - timedelta(hours=FC_SERIES_MAX_AGE_H)
    live = [
        ProviderSeries(
            s.provider,
            s.fetched_at,
            [p for p in s.points if first <= p.start < last],
        )
        for key in cfg.providers
        if (s := series.get(key)) is not None and s.fetched_at >= max_age
    ]

    def stat_fn(provider: str, bucket: str | None) -> Stat | None:
        return tracker.provider_stat(provider, bucket) if tracker else None

    # Afzonderlijke providerreeksen.
    provider_records: dict[str, list[QuarterRecord]] = {}
    for s in live:
        recs = []
        for p in s.points:
            stat = _usable(stat_fn(s.provider, lead_bucket(p.start, s.fetched_at)), cfg)
            err = stat.mae if stat else None
            recs.append(
                QuarterRecord(
                    start=p.start,
                    end=p.start + QUARTER,
                    price_raw=p.price_raw,
                    price_allin=_allin(p.start, p.price_raw, tariff),
                    source=SOURCE_FORECAST,
                    providers=(s.provider,),
                    fetched_at=s.fetched_at,
                    expanded=p.expanded,
                    n_sources=1,
                    spread_min=p.price_raw,
                    spread_max=p.price_raw,
                    expected_error_raw=err,
                    expected_error_allin=_scale(err, tariff),
                )
            )
        provider_records[s.provider] = recs

    # Ensemble.
    combined: dict[datetime, Combined] = {}
    ensemble_records: list[QuarterRecord] | None = None
    if cfg.ensemble:
        combined = combine(live, stat_fn, cfg)
        ensemble_records = []
        for c in combined.values():
            mae = ensemble_mae(tracker, lead_bucket(c.start, now), c.n_sources, c.providers, cfg)
            err = expected_error(mae, c.spread)
            ensemble_records.append(
                QuarterRecord(
                    start=c.start,
                    end=c.start + QUARTER,
                    price_raw=c.value,
                    price_allin=_allin(c.start, c.value, tariff),
                    source=SOURCE_FORECAST,
                    providers=c.providers,
                    fetched_at=c.fetched_at,
                    expanded=c.expanded,
                    n_sources=c.n_sources,
                    spread_min=c.spread_min,
                    spread_max=c.spread_max,
                    expected_error_raw=err,
                    expected_error_allin=_scale(err, tariff),
                )
            )

    data = ForecastData(
        generated_at=now,
        tariff=tariff,
        published=published,
        providers=provider_records,
        ensemble=ensemble_records,
        merged=merge(published, primary_series(ensemble_records, provider_records, cfg)),
        weights=current_weights(tracker, cfg),
    )
    return data, combined


def primary_series(
    ensemble: list[QuarterRecord] | None,
    providers: dict[str, list[QuarterRecord]],
    cfg: ForecastConfig,
) -> list[QuarterRecord]:
    """De voorspelling waar de sensoren mee rekenen: het ensemble, of — als dat uit
    staat — per kwartier de eerste provider (in de ingestelde volgorde) met een waarde."""
    if ensemble is not None:
        return ensemble
    by_start: dict[datetime, QuarterRecord] = {}
    for key in reversed(cfg.providers):
        for r in providers.get(key, []):
            by_start[r.start] = r
    return [by_start[k] for k in sorted(by_start)]


def current_weights(tracker: AccuracyTracker | None, cfg: ForecastConfig) -> dict[str, dict[str, float]]:
    """Gewicht per provider per bucket als alle providers een waarde hebben."""
    out: dict[str, dict[str, float]] = {p: {} for p in cfg.providers}
    for bucket, _, _ in LEAD_BUCKETS:
        stats = {p: tracker.provider_stat(p, bucket) if tracker else None for p in cfg.providers}
        for p, w in compute_weights(stats, cfg).items():
            out[p][bucket] = w
    return out


def ensemble_snapshot(combined: dict[datetime, Combined]) -> list[tuple[datetime, float, int]]:
    return [(c.start, c.value, c.n_sources) for c in combined.values()]

