"""Afgeleide waarden voor service en sensoren (puur, los te testen)."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from ..const import ELECTRICITY, ENSEMBLE
from ..model import PriceData
from ..prices import Tariff, apply_formula
from .engine import ForecastData, merge, primary_series, published_records
from .model import QUARTER, SOURCE_PUBLISHED, ForecastConfig, QuarterRecord, floor_quarter

PROVIDER_ALL = "all"


def _source(records: list[QuarterRecord]) -> str:
    kinds = {r.source for r in records}
    return kinds.pop() if len(kinds) == 1 else "mixed"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _summary(records: list[QuarterRecord]) -> dict:
    errors = [
        0.0 if r.source == SOURCE_PUBLISHED else r.expected_error_allin
        for r in records
    ]
    providers = sorted({p for r in records for p in r.providers})
    return {
        "avg_price_allin": _mean([r.price_allin for r in records]),
        "avg_price_raw": _mean([r.price_raw for r in records]),
        "source": _source(records),
        "providers": providers,
        # Fout op het gemiddelde: gemiddelde van de kwartierfouten (conservatief;
        # voorspelfouten van naburige kwartieren zijn sterk gecorreleerd).
        "expected_error_allin": None if any(e is None for e in errors) else _mean(errors),
    }


def cheapest_block(
    records: list[QuarterRecord], now: datetime, hours: int, lookahead_hours: int
) -> dict | None:
    """Goedkoopste aaneengesloten blok van `hours` uur (all-in) vanaf het huidige
    kwartier, binnen `lookahead_hours`, inclusief voorspelde kwartieren."""
    first = floor_quarter(now)
    last = first + timedelta(hours=lookahead_hours)
    candidates = [r for r in records if first <= r.start < last and r.price_allin is not None]
    size = max(1, hours * 4)
    best: tuple[float, int] | None = None
    run_start = 0
    for i in range(len(candidates)):
        if i and candidates[i].start - candidates[i - 1].start != QUARTER:
            run_start = i  # gat: blok moet aaneengesloten zijn
        if i - run_start + 1 >= size:
            j = i - size + 1
            total = sum(r.price_allin for r in candidates[j : i + 1])
            if best is None or total < best[0]:
                best = (total, j)
    if best is None:
        return None
    block = candidates[best[1] : best[1] + size]
    return {"start": block[0].start, "end": block[-1].end, "hours": hours, **_summary(block)}


def window_average(records: list[QuarterRecord], start: datetime, end: datetime) -> dict | None:
    chosen = [r for r in records if start <= r.start < end and r.price_allin is not None]
    if not chosen:
        return None
    return {"coverage_hours": len(chosen) / 4, **_summary(chosen)}


def next_24h(records: list[QuarterRecord], now: datetime) -> dict | None:
    first = floor_quarter(now)
    return window_average(records, first, first + timedelta(hours=24))


def tomorrow(records: list[QuarterRecord], now: datetime) -> dict | None:
    """Gemiddelde all-in van morgen (lokale datum): gepubliceerd waar het kan."""
    day = (dt_util.as_local(now) + timedelta(days=1)).date()
    chosen = [r for r in records if dt_util.as_local(r.start).date() == day and r.price_allin is not None]
    if not chosen:
        return None
    return {"date": day.isoformat(), "coverage_hours": len(chosen) / 4, **_summary(chosen)}


def chart_series(records: list[QuarterRecord]) -> list:
    """De volledige reeks per kwartier: [[epoch-ms, all-in, 'p'|'f']]."""
    return [
        [int(r.start.timestamp() * 1000), round(r.price_allin, 5), "p" if r.source == SOURCE_PUBLISHED else "f"]
        for r in records
        if r.price_allin is not None
    ]


def chart_step(records: list[QuarterRecord]) -> timedelta:
    """Resolutie van de gepubliceerde prijzen: een uur als de leverancier per uur
    publiceert (of er nog niets is), anders een kwartier."""
    published = [r for r in records if r.source == SOURCE_PUBLISHED]
    return QUARTER if published and not any(r.expanded for r in published) else timedelta(hours=1)


def forecast_chart(
    records: list[QuarterRecord], tariff: Tariff | None, step: timedelta = timedelta(hours=1)
) -> tuple[list, list, list]:
    """Alleen de voorspelde tijdvakken, als grafiekreeksen naast de bestaande kaart:
    all-in [[ms, prijs]], beurs incl. btw [[ms, prijs]] en foutband [[ms, laag, hoog]].

    Gemiddeld per `step` (standaard per uur, net als de gepubliceerde kolommen):
    ApexCharts baseert de kolombreedte op het kleinste tijdsverschil over álle series,
    dus kwartierpunten zouden de uurkolommen vier keer zo smal maken. Beurs en all-in
    komen uit dezelfde apply_formula, toegepast op de gemiddelde kale prijs.
    """
    if tariff is None:
        return [], [], []
    size = int(step.total_seconds())
    groups: dict[int, list[QuarterRecord]] = {}
    for r in records:
        if r.source == SOURCE_PUBLISHED:
            continue
        ts = int(r.start.timestamp())
        groups.setdefault(ts - ts % size, []).append(r)

    allin, market, band = [], [], []
    for ts in sorted(groups):
        group = groups[ts]
        begin = dt_util.as_utc(datetime.fromtimestamp(ts, tz=dt_util.UTC))
        slot = apply_formula(begin, begin + step, sum(r.price_raw for r in group) / len(group), tariff)
        ms = ts * 1000
        allin.append([ms, round(slot.total, 5)])
        market.append([ms, round(slot.market, 5)])
        errors = [r.expected_error_allin for r in group if r.expected_error_allin is not None]
        if errors:
            err = sum(errors) / len(errors)
            band.append([ms, round(slot.total - err, 5), round(slot.total + err, 5)])
    return allin, market, band


def service_response(
    prices: PriceData | None,
    forecast: ForecastData | None,
    cfg: ForecastConfig | None,
    supplier: str,
    energy: str,
    include_forecast: bool,
    provider: str,
    horizon_hours: int | None,
    now: datetime,
) -> dict:
    """Respons van `dyntarnl.get_prices`. Gas en 'zonder voorspelling' geven alleen
    gepubliceerde prijzen; voor gas op de eigen resolutie (gasdag)."""
    ed = (prices or {}).get(energy)
    published = published_records(ed, supplier, quarters=energy == ELECTRICITY)
    use_forecast = include_forecast and forecast is not None and cfg is not None and energy == ELECTRICITY

    extra: dict = {}
    if not use_forecast:
        records = published
    elif provider == ENSEMBLE:
        records = merge(published, primary_series(forecast.ensemble, forecast.providers, cfg))
    elif provider == PROVIDER_ALL:
        records = merge(published, primary_series(forecast.ensemble, forecast.providers, cfg))
        extra["providers"] = forecast.providers
    elif provider in forecast.providers:
        records = merge(published, forecast.providers[provider])
    else:
        raise ValueError(f"Onbekende of uitgeschakelde provider: {provider}")

    limit = now + timedelta(hours=horizon_hours) if horizon_hours else None

    def clip(recs: list[QuarterRecord]) -> list[dict]:
        return [r.as_dict() for r in recs if limit is None or r.start < limit]

    out = {
        "energy": energy,
        "unit": ed.unit if ed else None,
        "supplier": supplier,
        "vat_percentage": ed.vat_percentage if ed else None,
        "forecast_enabled": forecast is not None,
        "generated_at": dt_util.as_local(now).isoformat(),
        "records": clip(records),
    }
    if "providers" in extra:
        out["providers"] = {p: clip(r) for p, r in extra["providers"].items()}
    return out

