"""Gedeelde prijs- en teruglever-helpers."""

from __future__ import annotations

from datetime import datetime

from .model import Slot


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
