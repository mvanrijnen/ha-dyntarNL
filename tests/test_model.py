"""Tests voor het datamodel: dag-bucketing en tijdzone-parsing."""

from datetime import datetime, timedelta, timezone

from dyntarnl.model import EnergyData, Slot, bucket_by_day, parse_dt, tomorrow_complete

AMS = timezone(timedelta(hours=2))


def _slot(day: str, hour: int) -> Slot:
    start = datetime.fromisoformat(f"{day}T{hour:02d}:00:00").replace(tzinfo=AMS)
    return Slot(start=start, end=start + timedelta(hours=1), total=0.3, market=0.2, market_ex=0.16)


def test_bucket_by_day_splits_yesterday_today_tomorrow():
    slots = (
        [_slot("2026-08-16", h) for h in range(24)]
        + [_slot("2026-08-17", h) for h in range(24)]
        + [_slot("2026-08-18", h) for h in range(24)]
    )
    ed = bucket_by_day(slots, "kWh", 21.0)
    assert len(ed.today) == 24  # DEFAULT_NOW = 2026-08-17
    assert ed.yesterday and len(ed.yesterday) == 24
    assert ed.tomorrow and len(ed.tomorrow) == 24


def test_bucket_without_tomorrow_is_none():
    slots = [_slot("2026-08-17", h) for h in range(24)]
    ed = bucket_by_day(slots, "kWh", 21.0)
    assert len(ed.today) == 24
    assert ed.tomorrow is None
    assert ed.yesterday is None


def test_parse_dt_utc_becomes_local():
    # 22:00Z = 00:00 lokale tijd (CEST, +2)
    parsed = parse_dt("2026-08-16T22:00:00.000Z")
    assert parsed.hour == 0
    assert parsed.strftime("%Y-%m-%d") == "2026-08-17"


def test_parse_dt_naive_is_local():
    parsed = parse_dt("2026-08-17T05:00:00")
    assert parsed.hour == 5


def _hours(day: str, first: int, last: int) -> list[Slot]:
    """Uur-slots [first, last) op een lokale datum (AMS, zoals de test-stub)."""
    base = datetime.fromisoformat(f"{day}T00:00:00+02:00")
    return [
        Slot(start=base + timedelta(hours=h), end=base + timedelta(hours=h + 1), total=0.3, market=0.2, market_ex=0.16)
        for h in range(first, last)
    ]


def _ed(tomorrow, hours=24):
    return EnergyData(unit="kWh", vat_percentage=21.0, today=_hours("2026-08-17", 0, 24),
                      tomorrow=_hours("2026-08-18", 0, hours) if tomorrow else None)


def test_tomorrow_complete_needs_every_energy_type():
    """Gas publiceert later dan stroom; pas als álles binnen is stoppen de retries."""
    assert tomorrow_complete({"electricity": _ed(True), "gas": _ed(True)})
    assert not tomorrow_complete({"electricity": _ed(True), "gas": _ed(False)})
    assert not tomorrow_complete({"electricity": _ed(False), "gas": _ed(False)})


def test_tomorrow_complete_on_empty_data():
    assert not tomorrow_complete(None)
    assert not tomorrow_complete({})


def test_tomorrow_partially_known_is_not_complete():
    """Gas: 00–06 van morgen (lopende gasdag) is er al vroeg; dat is nog niet 'morgen'."""
    assert not tomorrow_complete({"electricity": _ed(True), "gas": _ed(True, hours=6)})
