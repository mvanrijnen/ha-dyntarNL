"""Inkoop- en verkooptabel: één compact kolomformaat, door dezelfde code gebouwd.

Per energietype één rij per tijdslot, van gisteren 00:00 tot het eind van wat er bekend is
(gepubliceerd, en met voorspellingen aan tot de voorspelhorizon). Gepubliceerde prijzen
winnen altijd van voorspellingen; bij een vast/variabel contract is de prijs ook zonder
beursprijs bekend, dus loopt de tabel niet leeg.

Formaat (attributen van de tabel-entiteit; `get_prices` geeft hetzelfde):

    {"unit": "€/kWh", "resolution_min": 15, "start": <epoch-s eerste rij>,
     "fields": ["price", "market_ex", "energy_ex", "fee_ex", "costs_ex", "tax_ex", "vat",
                "spread", "src"],
     "rows": [[...], ...], "sources": {"p:essent": "...", ...}}

Rij *i* begint op `start + i × resolution_min` (echte seconden, dus DST-veilig). Voor
inkoop én verkoop geldt: price = energy_ex − costs_ex + tax_ex + vat; energy_ex is beurs
+ opslag (dynamisch) of het contracttarief (vast). spread = inkoop − verkoop.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from .const import ELECTRICITY
from .model import EnergyData, Slot
from .tariffs import (
    SRC_CONTRACT,
    SRC_FORECAST,
    SRC_PUBLISHED,
    Period,
    contract_slot,
    period_at,
    sell_slot,
)

FIELDS = ("price", "market_ex", "energy_ex", "fee_ex", "costs_ex", "tax_ex", "vat", "spread", "src")
_QUARTER = timedelta(minutes=15)
_HOUR = timedelta(hours=1)


@dataclass(slots=True)
class Row:
    start: datetime
    end: datetime
    buy: Slot | None
    sell: Slot | None
    src: str


def _all(ed: EnergyData | None) -> list[Slot]:
    if ed is None:
        return []
    return (ed.yesterday or []) + ed.today + (ed.tomorrow or [])


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, 5)


def build_rows(
    ed: EnergyData | None,
    energy: str,
    periods: tuple[Period, ...],
    supplier_key: str,
    forecast_records: list | None,
    now: datetime,
) -> tuple[list[Row], timedelta]:
    """Alle tijdslots met inkoop- en verkoopprijs, op één vaste resolutie."""
    published = _all(ed)
    if not published:
        return [], _HOUR
    forecast = [r for r in (forecast_records or []) if r.source != "published"] if energy == ELECTRICITY else []
    # Altijd in echte tijd (UTC) rekenen: twee lokale tijden met dezelfde tzinfo trekt Python
    # op de wandklok van elkaar af, waardoor het dubbele uur in oktober 0 minuten leek.
    finer = any(s.end.timestamp() - s.start.timestamp() < _HOUR.total_seconds() for s in published)
    step = _QUARTER if forecast or finer else _HOUR

    by_ts: dict[float, Slot] = {}
    for s in published:
        t, end = dt_util.as_utc(s.start), dt_util.as_utc(s.end)
        while t < end:
            by_ts[t.timestamp()] = s
            t += step
    fc_by_ts = {r.start.timestamp(): r for r in forecast}

    first = min((dt_util.as_utc(s.start) for s in published))
    last = max((dt_util.as_utc(s.end) for s in published))
    if forecast:
        last = max(last, max(dt_util.as_utc(r.end) for r in forecast))
    # Vast contract: de prijs van morgen is ook zonder publicatie bekend.
    tomorrow = dt_util.start_of_local_day((dt_util.as_local(now) + timedelta(days=1)).date())
    day_after = dt_util.start_of_local_day((dt_util.as_local(now) + timedelta(days=2)).date())
    if energy == ELECTRICITY:
        period = period_at(periods, tomorrow)
        if period is not None and not period.is_dynamic:
            last = max(last, dt_util.as_utc(day_after))

    rows: list[Row] = []
    t = dt_util.as_utc(first)
    end = dt_util.as_utc(last)
    while t < end:
        period = period_at(periods, t) if periods else None
        slot = by_ts.get(t.timestamp())
        record = fc_by_ts.get(t.timestamp()) if slot is None else None
        if slot is not None:
            buy, market_ex, market = slot, slot.market_ex, slot.market
            src = f"{SRC_PUBLISHED}:{supplier_key}"
        elif record is not None:
            buy, market_ex = record.buy, record.price_raw
            market = buy.market if buy is not None else None
            src = f"{SRC_FORECAST}:{'+'.join(record.providers) or 'forecast'}"
        elif period is not None and not period.is_dynamic:
            buy, market_ex, market = contract_slot(period, energy, t, t + step, None), None, None
            src = SRC_CONTRACT
        else:
            buy, market_ex, market, src = None, None, None, ""
        if period is not None and not period.is_dynamic and src:
            src = SRC_CONTRACT
        sold = (
            sell_slot(period, buy, t, t + step, market_ex, market)
            if energy == ELECTRICITY and period is not None
            else None
        )
        rows.append(Row(t, t + step, buy, sold, src))
        t += step
    return rows, step


def _values(slot: Slot | None, spread: float | None, src: str, market_ex: float | None) -> list:
    if slot is None:
        return [None, _r(market_ex), None, None, None, None, None, _r(spread), src or None]
    return [
        _r(slot.total),
        _r(slot.market_ex),
        _r(slot.energy_part_ex),
        _r(slot.fee_ex),
        _r(slot.costs_ex),
        _r(slot.tax_ex),
        _r(slot.vat),
        _r(spread),
        src or None,
    ]


def describe_source(code: str, supplier_name: str) -> str:
    kind, _, detail = code.partition(":")
    if kind == SRC_PUBLISHED:
        return f"gepubliceerd door {supplier_name}"
    if kind == SRC_FORECAST:
        return f"voorspeld ({detail.replace('+', ', ')})"
    if kind == SRC_CONTRACT:
        return "contracttarief (vast/variabel)"
    return code


def table(rows: list[Row], step: timedelta, side: str, unit: str, supplier_name: str) -> dict:
    """De compacte tabel voor `side` ('buy' of 'sell')."""
    out_rows = []
    sources: dict[str, str] = {}
    for row in rows:
        slot = row.buy if side == "buy" else row.sell
        spread = (
            row.buy.total - row.sell.total if row.buy is not None and row.sell is not None else None
        )
        market_ex = row.buy.market_ex if row.buy is not None else None
        out_rows.append(_values(slot, spread, row.src, market_ex))
        if row.src and row.src not in sources:
            sources[row.src] = describe_source(row.src, supplier_name)
    return {
        "unit": f"€/{unit}",
        "resolution_min": int(step.total_seconds() // 60),
        "start": int(rows[0].start.timestamp()) if rows else None,
        "start_local": dt_util.as_local(rows[0].start).isoformat() if rows else None,
        "fields": list(FIELDS),
        "rows": out_rows,
        "sources": sources,
    }


def row_at(rows: list[Row], moment: datetime) -> Row | None:
    t = moment.timestamp()
    for row in rows:
        if row.start.timestamp() <= t < row.end.timestamp():
            return row
    return None


def next_negative_sell(rows: list[Row], moment: datetime) -> tuple[datetime, datetime] | None:
    """Eerstvolgende aaneengesloten periode (vanaf het huidige slot) met verkoopprijs < 0."""
    t = moment.timestamp()
    start = end = None
    for row in rows:
        if row.end.timestamp() <= t:
            continue
        negative = row.sell is not None and row.sell.total < 0
        if negative and start is None:
            start = row.start
        if start is not None:
            if not negative:
                break
            end = row.end
    return None if start is None else (start, end)
