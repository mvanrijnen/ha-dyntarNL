"""Afgeleide waarden voor service en sensoren (puur, los te testen)."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from ..const import ELECTRICITY, ENSEMBLE
from ..model import PriceData
from ..prices import Tariff, feed_in_value
from ..tariffs import PriceResolver
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
    records: list[QuarterRecord],
    now: datetime,
    hours: int,
    lookahead_hours: int,
    sell: bool = False,
) -> dict | None:
    """Goedkoopste aaneengesloten blok van `hours` uur (all-in), binnen `lookahead_hours`,
    inclusief voorspelde kwartieren. Met `sell=True`: het blok met de hóógste
    verkoopprijs (het beste moment om terug te leveren).

    Het blok begint op de resolutie van de gepubliceerde prijzen (bij uurprijzen: een heel
    uur) en mag het lopende uur zijn. Anders startte het om bijv. 13:15 en schoof het elk
    uur een stukje op terwijl het blok al bezig was.
    """
    step = int(chart_step(records).total_seconds())
    first = floor_quarter(now)
    first -= timedelta(seconds=int(first.timestamp()) % step)
    last = first + timedelta(hours=lookahead_hours)
    def price(r: QuarterRecord) -> float | None:
        return r.price_sell if sell else r.price_allin

    candidates = [r for r in records if first <= r.start < last and price(r) is not None]
    size = max(1, hours * 4)
    best: tuple[float, int] | None = None
    run_start = 0
    for i in range(len(candidates)):
        if i and candidates[i].start - candidates[i - 1].start != QUARTER:
            run_start = i  # gat: blok moet aaneengesloten zijn
        if i - run_start + 1 >= size:
            j = i - size + 1
            if int(candidates[j].start.timestamp()) % step:
                continue  # blok begint niet op een heel uur (of kwartier bij kwartierprijzen)
            total = sum(price(r) for r in candidates[j : i + 1])
            score = -total if sell else total
            if best is None or score < best[0]:
                best = (score, j)
    if best is None:
        return None
    block = candidates[best[1] : best[1] + size]
    out = {"start": block[0].start, "end": block[-1].end, "hours": hours, **_summary(block)}
    if sell:
        out["avg_price_sell"] = _mean([r.price_sell for r in block])
    return out


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
    # Alleen een hele dag (23/24/25 uur rond de klokwissel): een gemiddelde over alleen de
    # ochtend (korte horizon, verouderde reeks) zag eruit als het daggemiddelde.
    start = dt_util.as_utc(dt_util.start_of_local_day(day))
    end = dt_util.as_utc(dt_util.start_of_local_day(day + timedelta(days=1)))
    if not chosen or len(chosen) < (end - start) / QUARTER:
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
    records: list[QuarterRecord],
    tariff: Tariff | PriceResolver | None,
    step: timedelta = timedelta(hours=1),
) -> tuple[list, list, list, list]:
    """Alleen de voorspelde tijdvakken, als grafiekreeksen naast de bestaande kaart:
    all-in [[ms, prijs]], beurs incl. btw [[ms, prijs]], foutband [[ms, laag, hoog]] en
    teruglevering (beurs − opslag, feed_in_value) [[ms, prijs]].

    Gemiddeld per `step` (standaard per uur, net als de gepubliceerde kolommen):
    ApexCharts baseert de kolombreedte op het kleinste tijdsverschil over álle series,
    dus kwartierpunten zouden de uurkolommen vier keer zo smal maken. Beurs en all-in
    komen uit dezelfde apply_formula, toegepast op de gemiddelde kale prijs, met het tarief
    van de periode van dat uur (vast contract: het contracttarief).
    """
    if tariff is None:
        return [], [], [], []
    resolver = tariff if isinstance(tariff, PriceResolver) else PriceResolver(ref_tariff=tariff)
    size = int(step.total_seconds())
    groups: dict[int, list[QuarterRecord]] = {}
    for r in records:
        if r.source == SOURCE_PUBLISHED:
            continue
        ts = int(r.start.timestamp())
        groups.setdefault(ts - ts % size, []).append(r)

    allin, market, band, export = [], [], [], []
    for ts in sorted(groups):
        group = groups[ts]
        if len(group) < size // int(QUARTER.total_seconds()):
            continue  # onvolledig uur aan de rand van de horizon: geen halve kolom tonen
        begin = dt_util.as_utc(datetime.fromtimestamp(ts, tz=dt_util.UTC))
        slot = resolver.buy(begin, begin + step, sum(r.price_raw for r in group) / len(group))
        if slot is None:
            continue  # geen tarief voor deze periode (bijv. nog niet ingevuld)
        ms = ts * 1000
        allin.append([ms, round(slot.total, 5)])
        market.append([ms, round(slot.market, 5)])
        export.append([ms, round(feed_in_value(slot), 5)])
        errors = [r.expected_error_allin for r in group if r.expected_error_allin is not None]
        if errors:
            err = sum(errors) / len(errors)
            band.append([ms, round(slot.total - err, 5), round(slot.total + err, 5)])
    return allin, market, band, export


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
    periods: tuple = (),
) -> dict:
    """Respons van `dyntarnl.get_prices`. Gas en 'zonder voorspelling' geven alleen
    gepubliceerde prijzen; voor gas op de eigen resolutie (gasdag)."""
    ed = (prices or {}).get(energy)
    published = published_records(
        ed, supplier, quarters=energy == ELECTRICITY, periods=periods if energy == ELECTRICITY else ()
    )
    enabled = cfg is not None and cfg.enabled
    if enabled and provider not in (ENSEMBLE, PROVIDER_ALL) and provider not in cfg.providers:
        raise ValueError(f"Onbekende of uitgeschakelde provider: {provider}")
    use_forecast = include_forecast and enabled and energy == ELECTRICITY
    # Aan, maar nog geen berekende reeks (opstarten): gewoon alleen gepubliceerd.
    ensemble = forecast.ensemble if forecast else None
    series = forecast.providers if forecast else {}

    extra: dict = {}
    if not use_forecast:
        records = published
    elif provider == ENSEMBLE:
        records = merge(published, primary_series(ensemble, series, cfg))
    elif provider == PROVIDER_ALL:
        records = merge(published, primary_series(ensemble, series, cfg))
        extra["providers"] = series
    elif provider in cfg.providers:
        # Ingestelde bron zonder actuele reeks (nog niet opgehaald, backoff, verouderd):
        # geen fout, gewoon alleen de gepubliceerde prijzen.
        records = merge(published, series.get(provider, []))
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
        "forecast_enabled": enabled,
        # Welke voorspelling 'ensemble' is: het gewogen ensemble, of (ensemble uit) per
        # kwartier de eerste bron met een waarde.
        "forecast_series": (
            None if not use_forecast else ("ensemble" if cfg.ensemble else "first_available_provider")
        ),
        "generated_at": dt_util.as_local(now).isoformat(),
        "records": clip(records),
    }
    if "providers" in extra:
        out["providers"] = {p: clip(r) for p, r in extra["providers"].items()}
    return out

