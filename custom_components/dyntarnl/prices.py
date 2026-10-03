"""Gedeelde prijs- en teruglever-helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .model import Slot


@dataclass(frozen=True, slots=True)
class Tariff:
    """Leveranciersformule: vaste componenten excl. btw + btw-percentage."""

    fee_ex: float   # opslag, excl. btw
    tax_ex: float   # energiebelasting, excl. btw
    vat: float      # btw-percentage

    @property
    def factor(self) -> float:
        return 1 + self.vat / 100


def apply_formula(start: datetime, end: datetime, market_ex: float, tariff: Tariff) -> Slot:
    """DÉ leveranciersformule: all-in = (beurs + opslag + belasting) × (1 + btw%).

    Eén implementatie voor CUSTOM én voor voorspelde prijzen. Een negatieve
    beursprijs gaat lineair mee (btw óók over het negatieve deel), precies zoals
    de leveranciers-API's het zelf afrekenen.
    """
    factor = tariff.factor
    subtotal_ex = market_ex + tariff.fee_ex + tariff.tax_ex
    total = round(subtotal_ex * factor, 6)
    return Slot(
        start=start,
        end=end,
        total=total,
        market=round(market_ex * factor, 6),
        market_ex=market_ex,
        fee=round(tariff.fee_ex * factor, 6),
        fee_ex=tariff.fee_ex,
        tax=round(tariff.tax_ex * factor, 6),
        tax_ex=tariff.tax_ex,
        vat=round(total - subtotal_ex, 6),
    )


def tariff_from_slot(slot: Slot, vat: float) -> Tariff:
    """Leid de formule af uit een gepubliceerd uur (voor de API-leveranciers)."""
    return Tariff(fee_ex=slot.fee_ex, tax_ex=slot.tax_ex, vat=vat)


def slot_at(slots: list[Slot], moment: datetime) -> Slot | None:
    """Vind het uur-slot dat `moment` bevat (gas verspringt om 06:00, gasdag)."""
    for slot in slots:
        if slot.start <= moment < slot.end:
            return slot
    return None


def feed_in_value(slot: Slot) -> float:
    """Netto terugleververgoeding (incl. btw) = beursprijs − opslag.

    Negatief = terugleveren kost geld (drempel: beursprijs ≤ opslag). Bij bronnen
    zonder opslag (fee=0) valt de drempel samen met beursprijs < 0.
    """
    return slot.market - slot.fee


def feed_in_value_ex(slot: Slot) -> float:
    return slot.market_ex - slot.fee_ex
