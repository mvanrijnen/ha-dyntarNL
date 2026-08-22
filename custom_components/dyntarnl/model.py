"""Gedeeld intern prijsmodel; elk platform mapt hier naartoe."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from .const import ELECTRICITY, GAS


@dataclass(slots=True)
class Slot:
    """Eén uur-tarief. Alle bedragen in euro, inclusief btw (…_ex = excl. btw)."""

    start: datetime
    end: datetime
    total: float       # all-in eindprijs, incl. btw
    market: float      # kale beursprijs (EPEX), incl. btw
    market_ex: float   # kale beursprijs, excl. btw
    fee: float = 0.0       # inkoopvergoeding/opslag, incl. btw
    fee_ex: float = 0.0    # opslag, excl. btw
    tax: float = 0.0       # energiebelasting, incl. btw
    tax_ex: float = 0.0    # energiebelasting, excl. btw
    vat: float = 0.0       # btw-deel van de totale prijs
    has_breakdown: bool = True  # False = alleen all-in bekend (bijv. EnergyZero)


@dataclass(slots=True)
class EnergyData:
    unit: str
    vat_percentage: float
    today: list[Slot]
    tomorrow: list[Slot] | None = None
    yesterday: list[Slot] | None = None


# Per energietype ("electricity"/"gas") een EnergyData.
type PriceData = dict[str, EnergyData]


def tomorrow_complete(data: PriceData | None) -> bool:
    """True zodra voor élk energietype de prijzen van morgen binnen zijn.

    Stuurt de ophaal-retries aan: zolang dit False is blijft de integratie het
    's middags/'s avonds opnieuw proberen.
    """
    return bool(data) and all(ed.tomorrow for ed in data.values())


def parse_dt(value: str) -> datetime:
    """Parse een ISO-datumtijd naar lokale (NL) tijd."""
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        raise ValueError(f"Ongeldige datum/tijd: {value}")
    if parsed.tzinfo is None:
        # Naïef = lokale tijd (zoals eon-app/easyEnergy leveren).
        return parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    # Aware (bijv. UTC bij Frank) -> naar lokale tijd.
    return dt_util.as_local(parsed)


def bucket_by_day(slots: list[Slot], unit: str, vat: float) -> EnergyData:
    """Verdeel een platte lijst uur-slots over gisteren/vandaag/morgen (lokale datum)."""
    now = dt_util.now()
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    def day_of(slot: Slot) -> str:
        return slot.start.strftime("%Y-%m-%d")

    buckets: dict[str, list[Slot]] = {}
    for slot in sorted(slots, key=lambda s: s.start):
        buckets.setdefault(day_of(slot), []).append(slot)

    return EnergyData(
        unit=unit,
        vat_percentage=vat,
        today=buckets.get(today, []),
        tomorrow=buckets.get(tomorrow) or None,
        yesterday=buckets.get(yesterday) or None,
    )
