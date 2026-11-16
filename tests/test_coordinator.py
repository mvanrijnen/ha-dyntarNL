"""Tests voor de coordinator-logica: EPEX-fallback voor bronnen zonder breakdown."""

from datetime import datetime, timedelta, timezone

from dyntarnl.coordinator import _fill_market_from_epex
from dyntarnl.model import EnergyData, Slot

AMS = timezone(timedelta(hours=2))


def test_epex_fallback_fills_missing_market():
    start = datetime(2026, 8, 17, 12, tzinfo=AMS)
    slot = Slot(
        start=start,
        end=start + timedelta(hours=1),
        total=0.30,
        market=0.0,
        market_ex=0.0,
        has_breakdown=False,
    )
    data = {"electricity": EnergyData(unit="kWh", vat_percentage=21.0, today=[slot])}
    epex = {"electricity": {start.isoformat(): 0.18}, "gas": {}}

    _fill_market_from_epex(data, epex)

    assert slot.market_ex == 0.18
    assert abs(slot.market - 0.18 * 1.21) < 1e-6


def test_epex_fallback_leaves_existing_breakdown():
    start = datetime(2026, 8, 17, 12, tzinfo=AMS)
    slot = Slot(
        start=start,
        end=start + timedelta(hours=1),
        total=0.30,
        market=0.21,
        market_ex=0.17,
        has_breakdown=True,
    )
    data = {"electricity": EnergyData(unit="kWh", vat_percentage=21.0, today=[slot])}
    epex = {"electricity": {start.isoformat(): 0.99}, "gas": {}}

    _fill_market_from_epex(data, epex)

    # bestaande breakdown blijft ongemoeid
    assert slot.market_ex == 0.17
    assert slot.market == 0.21


def test_real_zero_market_price_does_not_trigger_epex_fallback():
    """Een beursprijs van precies 0 is echt; dat mag geen extra (falende) EPEX-call geven."""
    from dyntarnl.coordinator import _needs_epex_fallback

    start = datetime(2026, 8, 17, 12, tzinfo=AMS)
    slot = Slot(start=start, end=start + timedelta(hours=1), total=0.13, market=0.0, market_ex=0.0)
    assert not _needs_epex_fallback({"electricity": EnergyData(unit="kWh", vat_percentage=21.0, today=[slot])})
