"""Tests voor de teruglever-drempel en slot-selectie."""

from datetime import datetime, timedelta, timezone

from dyntarnl.model import Slot
from dyntarnl.prices import feed_in_value, slot_at

AMS = timezone(timedelta(hours=2))


def _slot(hour: int, market: float, fee: float = 0.0) -> Slot:
    start = datetime(2026, 8, 17, hour, tzinfo=AMS)
    return Slot(
        start=start,
        end=start + timedelta(hours=1),
        total=market + fee,
        market=market,
        market_ex=market / 1.21,
        fee=fee,
        fee_ex=fee / 1.21,
    )


def test_feed_in_is_market_minus_fee():
    # markt 0,015 maar opslag 0,025 -> teruglevering kost geld, hoewel markt > 0
    assert feed_in_value(_slot(12, 0.015, 0.025)) < 0
    assert feed_in_value(_slot(12, 0.05, 0.025)) > 0


def test_feed_in_without_fee_matches_negative_price():
    # zonder opslag valt de drempel samen met beursprijs < 0
    assert feed_in_value(_slot(12, -0.01, 0.0)) < 0
    assert feed_in_value(_slot(12, 0.01, 0.0)) > 0


def test_slot_at_selects_covering_hour():
    slots = [_slot(h, 0.10) for h in range(24)]
    picked = slot_at(slots, datetime(2026, 8, 17, 13, 30, tzinfo=AMS))
    assert picked is not None
    assert picked.start.hour == 13


def test_slot_at_returns_none_outside_range():
    slots = [_slot(h, 0.10) for h in range(6, 10)]
    assert slot_at(slots, datetime(2026, 8, 17, 3, 0, tzinfo=AMS)) is None
