"""Tariefperiodes: welke regels gelden voor een tijdslot, en wat kost/levert dat slot op.

Eén bron van waarheid voor alles wat per ingangsdatum kan veranderen: contracttype
(dynamisch/vast/variabel), saldering, energiebelasting, btw, opslag, terugleverkosten.
Een periode geldt vanaf 00:00 (Europe/Amsterdam) op haar ingangsdatum tot de volgende.

De keuze gebeurt altijd op het tijdslot zelf (`period_at(slot.start)`), nooit op "nu":
een reeks of voorspelling die over een ingangsdatum heen loopt, krijgt per slot de
juiste regels. Een ontbrekende waarde levert `None` op (sensor onbekend + repair),
nooit een stille 0.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Any

from homeassistant.util import dt as dt_util

from .const import (
    CONF_MARKUP_ELEC,
    CONF_MARKUP_GAS,
    CONF_PERIODS,
    CONF_TAX_ELEC,
    CONF_TAX_GAS,
    CONF_VAT,
    CONTRACT_DYNAMIC,
    CONTRACTS,
    DEFAULT_TAX_ELEC,
    DEFAULT_TAX_GAS,
    DEFAULT_VAT,
    ELECTRICITY,
    EXPORT_MINIMUM_FIRST_YEAR,
    EXPORT_MINIMUM_LAST_YEAR,
    EXPORT_MINIMUM_SHARE,
    GAS,
    LOGGER,
    PROVISIONAL_2027_START,
    PROVISIONAL_TAX_ELEC_2027,
    PROVISIONAL_TAX_GAS_2027,
    RATE_DOUBLE,
    RATE_INPUT_ALL_IN,
    RATE_INPUT_BARE,
    RATE_SINGLE,
)
from .model import Slot
from .prices import Tariff, apply_formula

# Bron-codes per slot (tabellen en service): gepubliceerd, voorspeld, contract.
SRC_PUBLISHED = "p"
SRC_FORECAST = "f"
SRC_CONTRACT = "c"


@dataclass(frozen=True, slots=True)
class Period:
    """Eén tariefperiode. Bedragen in €/kWh (gas €/m³), excl. btw tenzij anders vermeld."""

    start: date
    contract: str = CONTRACT_DYNAMIC
    netting: bool = True                     # saldering: verkoop = inkoop van hetzelfde slot
    vat: float | None = DEFAULT_VAT          # btw-percentage
    tax_elec_ex: float | None = None         # energiebelasting stroom
    tax_gas_ex: float | None = None          # energiebelasting gas
    # Inkoop, dynamisch: opslag op de beurs (bij een API-leverancier alleen voor
    # voorspelde uren; de gepubliceerde prijs komt van de leverancier).
    markup_elec_ex: float | None = None
    markup_gas_ex: float | None = None
    # Inkoop, vast/variabel.
    rate_mode: str = RATE_SINGLE
    rate_input: str = RATE_INPUT_ALL_IN      # all_in = incl. EB en btw; bare = kaal excl. btw
    rate_elec: float | None = None           # normaal (of enkeltarief)
    rate_elec_low: float | None = None       # dal
    rate_gas: float | None = None
    low_start: time = time(23, 0)
    low_end: time = time(7, 0)
    low_weekend: bool = True
    low_holidays: bool = False
    fixed_costs_month: float | None = None   # info: vaste leveringskosten, niet in de kWh-prijs
    # Verkoop (teruglevering), alleen stroom en alleen als saldering uit staat.
    export_markup_ex: float | None = None    # dynamisch: opslag (+) of korting (−) op de beurs
    export_rate_ex: float | None = None      # vast/variabel: terugleververgoeding (normaal)
    export_rate_low_ex: float | None = None  # vast/variabel: vergoeding dal (leeg = normaal)
    export_costs_ex: float | None = None     # terugleverkosten per kWh
    export_costs_year: float | None = None   # info: vaste terugleverkosten per jaar (staffel)
    provisional: bool = False
    note: str = ""

    @property
    def is_dynamic(self) -> bool:
        return self.contract == CONTRACT_DYNAMIC

    @property
    def factor(self) -> float | None:
        return None if self.vat is None else 1 + self.vat / 100

    # --- (de)serialisatie voor de config-entry-opties -------------------------------

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["start"] = self.start.isoformat()
        out["low_start"] = self.low_start.strftime("%H:%M")
        out["low_end"] = self.low_end.strftime("%H:%M")
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Period:
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in raw.items() if k in known}
        values["start"] = _to_date(raw["start"])
        for key in ("low_start", "low_end"):
            if key in values:
                values[key] = _to_time(values[key])
        if values.get("contract", CONTRACT_DYNAMIC) not in CONTRACTS:
            raise ValueError(f"onbekend contracttype: {values.get('contract')}")
        for f in fields(cls):
            if f.type == "float | None" and values.get(f.name) is not None:
                values[f.name] = float(values[f.name])
        return cls(**values)


def _to_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _to_time(value: Any) -> time:
    if isinstance(value, time):
        return value
    parts = [int(x) for x in str(value).split(":")]
    return time(parts[0], parts[1] if len(parts) > 1 else 0)


def periods_from_options(options: dict[str, Any] | None) -> tuple[Period, ...]:
    """Periodes uit de opties, gesorteerd op ingangsdatum. Een kapotte periode wordt
    overgeslagen (met een waarschuwing), zodat één fout niet alle prijzen blokkeert."""
    out: list[Period] = []
    for raw in (options or {}).get(CONF_PERIODS) or []:
        try:
            out.append(Period.from_dict(raw))
        except (KeyError, TypeError, ValueError) as err:
            LOGGER.warning("Tariefperiode overgeslagen (%s): %s", err, raw)
    return tuple(sorted(out, key=lambda p: p.start))


def period_at(periods: tuple[Period, ...], moment: datetime) -> Period | None:
    """De periode die geldt voor het tijdslot dat op `moment` begint (lokale datum)."""
    day = dt_util.as_local(moment).date()
    for period in reversed(periods):
        if period.start <= day:
            return period
    return None


def next_period(periods: tuple[Period, ...], moment: datetime) -> Period | None:
    day = dt_util.as_local(moment).date()
    return next((p for p in periods if p.start > day), None)


# --- Dal/normaal -----------------------------------------------------------------------


def _easter(year: int) -> date:
    """Eerste Paasdag (Gregoriaans, anonieme algoritme)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


@lru_cache(maxsize=16)
def nl_holidays(year: int) -> frozenset[date]:
    """Feestdagen waarop netbeheerders het daltarief toepassen: Nieuwjaar, Pasen,
    Koningsdag, Hemelvaart, Pinksteren en Kerst (Goede Vrijdag/Bevrijdingsdag niet)."""
    easter = _easter(year)
    kings = date(year, 4, 27)
    if kings.weekday() == 6:  # zondag → zaterdag ervoor
        kings = date(year, 4, 26)
    return frozenset(
        {
            date(year, 1, 1),
            easter,
            easter + timedelta(days=1),
            kings,
            easter + timedelta(days=39),
            easter + timedelta(days=49),
            easter + timedelta(days=50),
            date(year, 12, 25),
            date(year, 12, 26),
        }
    )


def is_low(period: Period, moment: datetime) -> bool:
    """Daltarief voor het slot dat op `moment` begint (lokale kloktijd, DST-correct)."""
    if period.rate_mode != RATE_DOUBLE:
        return False
    local = dt_util.as_local(moment)
    if period.low_holidays and local.date() in nl_holidays(local.year):
        return True
    if period.low_weekend and local.weekday() >= 5:
        return True
    clock = local.time().replace(tzinfo=None)
    start, end = period.low_start, period.low_end
    if start == end:
        return False
    if start > end:  # over middernacht, bijv. 23:00–07:00
        return clock >= start or clock < end
    return start <= clock < end


# --- Prijs per slot ----------------------------------------------------------------------


def _r(value: float) -> float:
    return round(value, 6)


def custom_tariff(period: Period, energy: str) -> Tariff | None:
    """Dynamisch tarief uit de periode (Custom en voorspelde uren)."""
    markup = period.markup_elec_ex if energy == ELECTRICITY else period.markup_gas_ex
    tax = period.tax_elec_ex if energy == ELECTRICITY else period.tax_gas_ex
    if markup is None or tax is None or period.vat is None:
        return None
    return Tariff(fee_ex=markup, tax_ex=tax, vat=period.vat)


def contract_slot(
    period: Period,
    energy: str,
    start: datetime,
    end: datetime,
    market_ex: float | None,
    market: float | None = None,
) -> Slot | None:
    """Inkoopprijs van een vast/variabel contract. De beurs blijft informatief in het slot."""
    factor = period.factor
    if energy == ELECTRICITY:
        low = is_low(period, start)
        rate = period.rate_elec_low if low else period.rate_elec
        tax = period.tax_elec_ex
    else:
        rate, tax = period.rate_gas, period.tax_gas_ex
    if rate is None or tax is None or factor is None:
        return None
    if period.rate_input == RATE_INPUT_BARE:
        energy_ex = rate
        total = (rate + tax) * factor
    else:
        energy_ex = rate / factor - tax
        total = rate
    subtotal_ex = energy_ex + tax
    if market is None and market_ex is not None:
        market = _r(market_ex * factor)
    return Slot(
        start=start,
        end=end,
        total=_r(total),
        market=market,
        market_ex=market_ex,
        fee=0.0,
        fee_ex=0.0,
        tax=_r(tax * factor),
        tax_ex=tax,
        vat=_r(total - subtotal_ex),
        energy_ex=_r(energy_ex),
    )


def buy_slot(period: Period | None, slot: Slot, energy: str, custom: bool) -> Slot | None:
    """Inkoopprijs van een gepubliceerd slot volgens de periode van dat slot.

    Dynamisch bij een API-leverancier: het slot van de leverancier, ongewijzigd (die levert
    belasting en opslag zelf per slot). Dynamisch Custom: de formule met de periodewaarden.
    Vast/variabel: het contracttarief.
    """
    if period is None:
        return None if custom else slot
    if period.is_dynamic:
        if not custom:
            return slot
        tariff = custom_tariff(period, energy)
        return None if tariff is None else apply_formula(slot.start, slot.end, slot.market_ex, tariff)
    return contract_slot(period, energy, slot.start, slot.end, slot.market_ex, slot.market)


def sell_slot(period: Period | None, buy: Slot | None, start: datetime, end: datetime,
              market_ex: float | None, market: float | None) -> Slot | None:
    """Verkoopprijs (teruglevering) van een slot.

    Saldering aan: gelijk aan de inkoopprijs van datzelfde slot. Uit: geen energiebelasting
    en geen btw; dynamisch = beurs ± teruglever-opslag − terugleverkosten, vast/variabel =
    vergoeding (normaal/dal) − terugleverkosten.
    """
    if period is None:
        return None
    if period.netting:
        return None if buy is None else replace(buy, costs_ex=0.0)
    costs = period.export_costs_ex
    if costs is None:
        return None
    if period.is_dynamic:
        if market_ex is None or period.export_markup_ex is None:
            return None
        energy_ex = market_ex + period.export_markup_ex
        fee = period.export_markup_ex
    else:
        rate = period.export_rate_ex
        if is_low(period, start) and period.export_rate_low_ex is not None:
            rate = period.export_rate_low_ex
        if rate is None:
            return None
        energy_ex, fee = rate, 0.0
    return Slot(
        start=start,
        end=end,
        total=_r(energy_ex - costs),
        market=market,
        market_ex=market_ex,
        fee=fee,
        fee_ex=fee,
        tax=0.0,
        tax_ex=0.0,
        vat=0.0,
        energy_ex=_r(energy_ex),
        costs_ex=costs,
    )


def apply_periods(slots: list[Slot], periods: tuple[Period, ...], energy: str, custom: bool) -> list[Slot]:
    """Inkoopslots volgens de periode van elk slot; slots zonder prijs vallen weg."""
    out = []
    for slot in slots:
        priced = buy_slot(period_at(periods, slot.start), slot, energy, custom)
        if priced is not None:
            out.append(priced)
    return out


def sell_slots(slots: list[Slot], periods: tuple[Period, ...]) -> list[Slot]:
    """Verkoopslots bij een lijst inkoopslots (zelfde tijden); zonder prijs vallen weg."""
    out = []
    for s in slots:
        sold = sell_slot(period_at(periods, s.start), s, s.start, s.end, s.market_ex, s.market)
        if sold is not None:
            out.append(sold)
    return out


class PriceResolver:
    """Prijs per (voorspeld) kwartier volgens de periode van dat kwartier.

    Zonder periodes, of binnen de periode van het laatst gepubliceerde uur, rekent hij
    exact als voorheen: met het tarief van dat laatste uur (`ref_tariff`). In een latere
    dynamische periode komen belasting en btw uit die periode; de opslag ook, of (bij een
    API-leverancier, opslag niet ingevuld) de huidige opslag van de leverancier.
    """

    def __init__(
        self,
        periods: tuple[Period, ...] = (),
        ref_tariff: Tariff | None = None,
        ref_start: datetime | None = None,
        custom: bool = False,
    ) -> None:
        self.periods = periods
        self.ref_tariff = ref_tariff
        self.ref_period = period_at(periods, ref_start) if ref_start is not None and periods else None
        self.custom = custom

    def period(self, start: datetime) -> Period | None:
        return period_at(self.periods, start) if self.periods else None

    def tariff_for(self, start: datetime) -> Tariff | None:
        period = self.period(start)
        if period is None or period is self.ref_period:
            return self.ref_tariff
        if not period.is_dynamic:
            return None
        fee = period.markup_elec_ex
        if fee is None and not self.custom and self.ref_tariff is not None:
            fee = self.ref_tariff.fee_ex
        if fee is None or period.tax_elec_ex is None or period.vat is None:
            return None
        return Tariff(fee_ex=fee, tax_ex=period.tax_elec_ex, vat=period.vat)

    def buy(self, start: datetime, end: datetime, market_ex: float) -> Slot | None:
        period = self.period(start)
        if period is not None and not period.is_dynamic:
            return contract_slot(period, ELECTRICITY, start, end, market_ex)
        tariff = self.tariff_for(start)
        return None if tariff is None else apply_formula(start, end, market_ex, tariff)

    def sell(self, buy: Slot | None, start: datetime, end: datetime, market_ex: float) -> Slot | None:
        factor = self.ref_tariff.factor if self.ref_tariff else None
        period = self.period(start)
        if period is not None and period.factor is not None:
            factor = period.factor
        market = None if factor is None else _r(market_ex * factor)
        return sell_slot(period, buy, start, end, market_ex, market)

    def error_factor(self, start: datetime) -> float | None:
        """Hoe een fout in de kale prijs doorwerkt in de inkoopprijs: × btw bij
        dynamisch, 0 bij een vast contract (de prijs ligt vast)."""
        period = self.period(start)
        if period is not None and not period.is_dynamic:
            return 0.0
        tariff = self.tariff_for(start)
        return None if tariff is None else tariff.factor


# --- Controle -----------------------------------------------------------------------------


def missing_fields(period: Period, custom: bool) -> list[str]:
    """Verplichte velden die leeg zijn, gegeven contracttype, saldering en leverancier."""
    required = ["vat", "tax_elec_ex"]
    if period.is_dynamic:
        if custom:
            required += ["markup_elec_ex", "markup_gas_ex", "tax_gas_ex"]
        if not period.netting:
            required += ["export_markup_ex", "export_costs_ex"]
    else:
        required.append("rate_elec")
        if period.rate_mode == RATE_DOUBLE:
            required.append("rate_elec_low")
        if period.rate_gas is not None:
            required.append("tax_gas_ex")
        if not period.netting:
            required += ["export_rate_ex", "export_costs_ex"]
    return [key for key in required if getattr(period, key) is None]


def bare_rate(period: Period, low: bool = False) -> float | None:
    """Kaal leveringstarief stroom (excl. EB en btw) van een vast/variabel contract."""
    rate = period.rate_elec_low if low else period.rate_elec
    if rate is None:
        return None
    if period.rate_input == RATE_INPUT_BARE:
        return rate
    if period.factor is None or period.tax_elec_ex is None:
        return None
    return rate / period.factor - period.tax_elec_ex


def below_export_minimum(period: Period, until: date | None) -> bool:
    """True als een vaste/variabele vergoeding onder het wettelijk minimum zit (50% van het
    kale leveringstarief) in een periode die 2027–2029 raakt. `until` = start volgende periode."""
    if period.is_dynamic or period.netting:
        return False
    first = date(EXPORT_MINIMUM_FIRST_YEAR, 1, 1)
    last = date(EXPORT_MINIMUM_LAST_YEAR, 12, 31)
    if period.start > last or (until is not None and until <= first):
        return False
    pairs = [(period.export_rate_ex, bare_rate(period))]
    if period.rate_mode == RATE_DOUBLE:
        low_export = period.export_rate_low_ex if period.export_rate_low_ex is not None else period.export_rate_ex
        pairs.append((low_export, bare_rate(period, low=True)))
    return any(
        export is not None and bare is not None and export < EXPORT_MINIMUM_SHARE * bare
        for export, bare in pairs
    )


# --- Startwaarden (nieuwe installatie en migratie) ----------------------------------------


def initial_periods(data: dict[str, Any], custom: bool, today: date) -> list[dict[str, Any]]:
    """De huidige periode uit de bestaande instellingen, plus een voorlopige 2027-periode.

    Niets verzonnen: opslag en terugleverkosten voor 2027 blijven leeg (repair), alleen de
    bekende (voorlopige) belasting wordt ingevuld.
    """
    switch = _to_date(PROVISIONAL_2027_START)
    vat = float(data.get(CONF_VAT, DEFAULT_VAT))
    provisional = Period(
        start=switch,
        netting=False,
        vat=vat,
        tax_elec_ex=PROVISIONAL_TAX_ELEC_2027,
        tax_gas_ex=PROVISIONAL_TAX_GAS_2027,
        provisional=True,
        note="Belastingplan 2027 nog niet definitief; vul opslag en terugleverkosten in.",
    )
    if today >= switch:
        return [replace(provisional, start=date(today.year, 1, 1)).to_dict()]
    current = Period(
        start=date(today.year, 1, 1),
        netting=True,
        vat=vat,
        tax_elec_ex=float(data.get(CONF_TAX_ELEC, DEFAULT_TAX_ELEC)),
        tax_gas_ex=float(data.get(CONF_TAX_GAS, DEFAULT_TAX_GAS)),
        markup_elec_ex=float(data[CONF_MARKUP_ELEC]) if custom and CONF_MARKUP_ELEC in data else None,
        markup_gas_ex=float(data[CONF_MARKUP_GAS]) if custom and CONF_MARKUP_GAS in data else None,
    )
    return [current.to_dict(), provisional.to_dict()]


def legacy_periods(cfg: dict[str, Any]) -> tuple[Period, ...]:
    """Eén altijd geldende Custom-periode uit de oude platte instellingen (tests/terugval)."""
    return (
        Period(
            start=date(1970, 1, 1),
            vat=float(cfg.get(CONF_VAT, DEFAULT_VAT)),
            markup_elec_ex=float(cfg.get(CONF_MARKUP_ELEC, 0.0)),
            markup_gas_ex=float(cfg.get(CONF_MARKUP_GAS, 0.0)),
            tax_elec_ex=float(cfg.get(CONF_TAX_ELEC, 0.0)),
            tax_gas_ex=float(cfg.get(CONF_TAX_GAS, 0.0)),
        ),
    )


ENERGIES = (ELECTRICITY, GAS)


def period_summary(periods: tuple[Period, ...], now: datetime, custom: bool) -> dict[str, Any]:
    """Attributen van de diagnostische sensor: huidige en volgende periode, en wat er
    voorlopig of onvolledig is."""
    current = period_at(periods, now)
    upcoming = next_period(periods, now)
    today = dt_util.as_local(now).date()
    return {
        "current": None if current is None else current.to_dict(),
        "next": None if upcoming is None else upcoming.to_dict(),
        "next_start": None if upcoming is None else upcoming.start.isoformat(),
        "provisional_active": bool(current and current.provisional),
        "provisional_planned": any(p.provisional for p in periods if p.start > today),
        "missing_current": [] if current is None else missing_fields(current, custom),
        "missing_next": [] if upcoming is None else missing_fields(upcoming, custom),
        "period_starts": [p.start.isoformat() for p in periods],
    }
