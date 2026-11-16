"""Zomer-/wintertijd met de echte tijdzone Europe/Amsterdam (de stub rekent met +02:00)."""

import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from dyntarnl.model import Slot, localize_naive_series
from dyntarnl.prices import slot_at

AMS = ZoneInfo("Europe/Amsterdam")
UTC = timezone.utc


@pytest.fixture
def real_tz(monkeypatch):
    dt = sys.modules["homeassistant.util.dt"]
    monkeypatch.setattr(dt, "DEFAULT_TIME_ZONE", AMS)
    monkeypatch.setattr(dt, "as_local", lambda d: d.astimezone(AMS))
    return dt


def _hourly(starts):
    # Eind in UTC berekenen: 'start + 1 uur' op een lokale tijd is wandklok-rekenen.
    return [
        Slot(start=s, end=(s.astimezone(UTC) + timedelta(hours=1)).astimezone(AMS), total=float(i), market=0, market_ex=0)
        for i, s in enumerate(starts)
    ]


def test_spring_forward_easyenergy_labels(real_tz):
    """easyEnergy op 29-03-2026: 23 rijen '00:00'…'22:00', inclusief het niet-bestaande
    '02:00' en zonder '23:00'. Label '02:00' = 03:00 CEST, alles daarna een uur later."""
    labels = [f"2026-03-29T{h:02d}:00:00" for h in range(23)]
    starts = localize_naive_series(labels)
    utc_hours = [s.astimezone(UTC).hour for s in starts]
    assert utc_hours == [23] + list(range(0, 22))  # 00:00 CET = 23:00Z, daarna aaneengesloten
    assert starts[-1].astimezone(AMS).hour == 23  # het laatste uur van de dag is er
    assert len({s.timestamp() for s in starts}) == 23


def test_fall_back_duplicate_hour_gets_fold(real_tz):
    """eon-app op 25-10-2026: '02:00' twee keer → eerste CEST, tweede CET."""
    labels = [f"2026-10-25T{h:02d}:00:00" for h in (0, 1, 2, 2, 3, 4)]
    starts = localize_naive_series(labels)
    assert [s.astimezone(UTC).hour for s in starts] == [22, 23, 0, 1, 2, 3]


def test_fall_back_slot_at_picks_the_right_02_hour(real_tz):
    """Slots uit UTC-bronnen (Frank): om 02:30 CEST het eerste, om 02:30 CET het tweede uur."""
    starts = [(datetime(2026, 10, 24, 22, tzinfo=UTC) + timedelta(hours=h)).astimezone(AMS) for h in range(6)]
    slots = _hourly(starts)
    first = datetime(2026, 10, 25, 0, 30, tzinfo=UTC).astimezone(AMS)   # 02:30 CEST
    second = datetime(2026, 10, 25, 1, 30, tzinfo=UTC).astimezone(AMS)  # 02:30 CET
    assert slot_at(slots, first).total == 2
    assert slot_at(slots, second).total == 3


def test_next_hour_over_spring_gap(real_tz):
    """Om 01:30 CET op 29-03 is 'volgend uur' 03:00 CEST (één echt uur later)."""
    starts = [(datetime(2026, 3, 28, 23, tzinfo=UTC) + timedelta(hours=h)).astimezone(AMS) for h in range(5)]
    slots = _hourly(starts)
    now = datetime(2026, 3, 29, 1, 30, tzinfo=AMS)
    nxt = slot_at(slots, now.astimezone(UTC) + timedelta(hours=1))
    assert nxt.start.astimezone(AMS).hour == 3


def test_naive_slots_never_overlap_on_dst_days(real_tz):
    """Eindlabels los omzetten gaf rond de klokwissel overlappende slots."""
    from dyntarnl.model import localize_naive_slots

    # Maart, bron met correcte starts en 'start + 1u' als eind (dus eind '02:00').
    starts = ["2026-03-29T00:00:00", "2026-03-29T01:00:00", "2026-03-29T03:00:00", "2026-03-29T04:00:00"]
    ends = ["2026-03-29T01:00:00", "2026-03-29T02:00:00", "2026-03-29T04:00:00", "2026-03-29T05:00:00"]
    spans = localize_naive_slots(starts, ends)
    for (s1, e1), (s2, _) in zip(spans, spans[1:]):
        assert e1.timestamp() <= s2.timestamp()
    # Oktober, dubbel '02:00' met eindlabels 03:00 twee keer.
    starts = ["2026-10-25T01:00:00", "2026-10-25T02:00:00", "2026-10-25T02:00:00", "2026-10-25T03:00:00"]
    ends = ["2026-10-25T02:00:00", "2026-10-25T03:00:00", "2026-10-25T03:00:00", "2026-10-25T04:00:00"]
    spans = localize_naive_slots(starts, ends)
    assert [round((e - s).total_seconds() / 3600) for s, e in ((x.astimezone(UTC), y.astimezone(UTC)) for x, y in spans)] == [1, 1, 1, 1]
    for (s1, e1), (s2, _) in zip(spans, spans[1:]):
        assert e1.timestamp() <= s2.timestamp()


def test_energyzero_fall_back_hour_has_its_own_price(real_tz, monkeypatch):
    import asyncio

    import dyntarnl.sources as src

    rows = [{"readingDate": f"2026-10-25T{h:02d}:00:00Z", "price": 0.1 + h / 100} for h in range(0, 4)]

    async def fake_get(session, url, **kw):
        return {"Prices": rows}

    monkeypatch.setattr(src, "_get_json", fake_get)
    monkeypatch.setattr(sys.modules["homeassistant.util.dt"], "now", lambda: datetime(2026, 10, 25, 12, tzinfo=AMS))
    monkeypatch.setattr(sys.modules["homeassistant.util.dt"], "start_of_local_day", lambda d: datetime(d.year, d.month, d.day, tzinfo=AMS))
    data = asyncio.run(src.fetch_energyzero(None))
    slots = data["electricity"].today
    # 02:30 CET (01:30Z) hoort bij het 01Z-uur, niet bij het CEST-uur ervoor.
    found = slot_at(slots, datetime(2026, 10, 25, 1, 30, tzinfo=UTC))
    assert found.market_ex == pytest.approx(0.11)
