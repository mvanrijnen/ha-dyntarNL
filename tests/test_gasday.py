"""Test dat de gasprijs de gasdag (06:00–06:00) volgt in de eon-app data."""

import asyncio

import dyntarnl.sources as src
from dyntarnl.prices import slot_at


def test_gas_is_constant_within_gasday(monkeypatch, load_fixture):
    essent = load_fixture("essent.json")

    async def fake_get(session, url, **kw):
        return essent

    monkeypatch.setattr(src, "_get_json", fake_get)
    data = asyncio.run(src.fetch_eon_app(None, "www.essent.nl"))
    gas = data["gas"]

    by_hour = {s.start.hour: s.total for s in gas.today}
    assert len(by_hour) == 24
    # binnen dezelfde gasdag is de prijs constant: 00–06 gelijk, 06–24 gelijk
    assert by_hour[0] == by_hour[5]
    assert by_hour[6] == by_hour[23]


def test_slot_at_picks_correct_gas_hour(monkeypatch, load_fixture):
    from datetime import datetime, timedelta, timezone

    ams = timezone(timedelta(hours=2))
    essent = load_fixture("essent.json")

    async def fake_get(session, url, **kw):
        return essent

    monkeypatch.setattr(src, "_get_json", fake_get)
    gas = asyncio.run(src.fetch_eon_app(None, "www.essent.nl"))["gas"]

    before = slot_at(gas.today, datetime(2026, 8, 17, 3, 30, tzinfo=ams))
    after = slot_at(gas.today, datetime(2026, 8, 17, 12, 30, tzinfo=ams))
    assert before is not None and before.start.hour == 3
    assert after is not None and after.start.hour == 12
