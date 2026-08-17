"""Tests voor het datamodel: dag-bucketing en tijdzone-parsing."""

from datetime import datetime, timedelta, timezone

from dyntarnl.model import Slot, bucket_by_day, parse_dt

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
