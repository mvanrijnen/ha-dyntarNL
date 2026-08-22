"""Tests voor de ophaalplanning: spreiding over installaties."""

from dyntarnl import _TOMORROW_RETRY_HOURS, _spread


def test_spread_is_stable_per_entry():
    """Zelfde entry_id -> zelfde offset, ook na een herstart."""
    assert _spread("abc123", 30) == _spread("abc123", 30)


def test_spread_stays_within_the_half_hour():
    """offset en offset+30 moeten allebei geldige minuten blijven (0-59)."""
    for entry_id in (f"entry{i:04d}" for i in range(500)):
        offset = _spread(entry_id, 30)
        assert 0 <= offset <= 29
        assert offset + 30 <= 59


def test_spread_actually_spreads():
    """Verschillende installaties komen niet allemaal op dezelfde minuut uit."""
    offsets = {_spread(f"entry{i:04d}", 30) for i in range(500)}
    assert len(offsets) == 30, f"maar {len(offsets)} van de 30 minuten benut"


def test_retry_window_covers_afternoon_and_evening():
    assert _TOMORROW_RETRY_HOURS[0] == 13
    assert _TOMORROW_RETRY_HOURS[-1] == 23
