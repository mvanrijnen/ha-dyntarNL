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
    """True zodra voor élk energietype de prijzen van morgen tot 24:00 binnen zijn.

    Stuurt de ophaal-retries aan: zolang dit False is blijft de integratie het
    's middags/'s avonds opnieuw proberen. Eén uur van morgen is niet genoeg: voor gas
    staat 00:00–06:00 van morgen (de lopende gasdag) er al ruim vóór de nieuwe gasdag.
    """
    if not data or ELECTRICITY not in data:
        return False
    day_after = dt_util.start_of_local_day((dt_util.now() + timedelta(days=2)).date())
    return all(
        ed.tomorrow and max(s.end for s in ed.tomorrow).timestamp() >= day_after.timestamp()
        for ed in data.values()
    )


def localize_naive_slots(starts: list[str], ends: list[str]) -> list[tuple[datetime, datetime]]:
    """Begin/eind-paren uit naïeve lokale tijden → echte lokale tijden.

    Het eind wordt afgeleid van het begin plus de (naïeve) duur, in echte tijd. Eindlabels
    los omzetten gaf rond de klokwissel overlappende slots (bijv. een eind '02:00' dat in
    maart niet bestaat, of een dubbel '03:00' in oktober).
    """
    localized = localize_naive_series(starts)
    out = []
    for i, (start, s_raw, e_raw) in enumerate(zip(localized, starts, ends)):
        # Eind == begin van het volgende slot (zelfde label): dan is dát het eind, in echte
        # tijd. Werkt ongeacht hoe de bron rond de klokwissel zijn eindlabels schrijft.
        if i + 1 < len(starts) and e_raw == starts[i + 1]:
            out.append((start, localized[i + 1]))
            continue
        naive_start = dt_util.parse_datetime(s_raw)
        naive_end = dt_util.parse_datetime(e_raw)
        if naive_start is None or naive_end is None:
            raise ValueError(f"Ongeldige datum/tijd: {s_raw} / {e_raw}")
        if naive_end.tzinfo is not None:
            end = dt_util.as_local(naive_end)
        else:
            end = dt_util.as_local(dt_util.as_utc(start) + (naive_end - naive_start.replace(tzinfo=None)))
        out.append((start, end))
    return out


def localize_naive_series(values: list[str]) -> list[datetime]:
    """Opeenvolgende naïeve lokale tijden (eon-app, easyEnergy) → echte lokale tijden.

    Rond de klokwissel is een naïeve tijd niet eenduidig:
    - Zomertijd (maart): een bron die het niet-bestaande '02:00' noemt (easyEnergy) bedoelt
      03:00, en schuift alles daarna een uur op; vanaf dat label tellen we +1 uur.
    - Wintertijd (oktober): een tweede '02:00' direct na het eerste is het CET-uur (fold=1).
    """
    tz = dt_util.DEFAULT_TIME_ZONE
    out: list[datetime] = []
    shifted_date = None
    previous = None
    for value in values:
        naive = dt_util.parse_datetime(value)
        if naive is None:
            raise ValueError(f"Ongeldige datum/tijd: {value}")
        if naive.tzinfo is not None:
            out.append(dt_util.as_local(naive))
            previous = None
            continue
        aware = naive.replace(tzinfo=tz, fold=1 if naive == previous else 0)
        instant = dt_util.as_utc(aware)
        if dt_util.as_local(instant).replace(tzinfo=None, fold=0) != naive:
            shifted_date = naive.date()  # bestaat niet: de klok sprong hier vooruit
        elif shifted_date == naive.date():
            instant += timedelta(hours=1)
        out.append(dt_util.as_local(instant))
        previous = naive
    return out


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

    # Dubbele rijen van de bron (zelfde tijdstip) één keer tellen; de laatste wint. De twee
    # 02:00-uren in oktober hebben verschillende tijdstippen en blijven dus allebei.
    unique = {slot.start.timestamp(): slot for slot in slots}
    buckets: dict[str, list[Slot]] = {}
    for _, slot in sorted(unique.items()):
        buckets.setdefault(day_of(slot), []).append(slot)

    return EnergyData(
        unit=unit,
        vat_percentage=vat,
        today=buckets.get(today, []),
        tomorrow=buckets.get(tomorrow) or None,
        yesterday=buckets.get(yesterday) or None,
    )
