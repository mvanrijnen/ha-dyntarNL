"""Tests voor de platform-parsers (fixtures, geen live calls)."""

import asyncio
import re

import pytest

import dyntarnl.sources as src
from dyntarnl.prices import slot_at


def _run(coro):
    return asyncio.run(coro)


def test_eon_app_parser(monkeypatch, load_fixture, now):
    essent = load_fixture("essent.json")

    async def fake_get(session, url, **kw):
        assert "dynamic-prices" in url
        return essent

    monkeypatch.setattr(src, "_get_json", fake_get)
    data = _run(src.fetch_eon_app(None, "www.essent.nl"))

    assert set(data) == {"electricity", "gas"}
    ed = data["electricity"]
    assert len(ed.today) == 24
    cur = slot_at(ed.today, now)
    assert cur is not None
    # breakdown telt op tot de all-in prijs
    assert cur.total == round(cur.market + cur.fee + cur.tax, 5) or abs(
        cur.total - (cur.market + cur.fee + cur.tax)
    ) < 1e-4
    assert cur.market_ex > 0 and cur.fee_ex > 0 and cur.tax_ex > 0


def test_easyenergy_parser(monkeypatch, load_fixture, now):
    elec = load_fixture("easyenergy_electricity.json")
    gas = load_fixture("easyenergy_gas.json")

    async def fake_get(session, url, **kw):
        return elec if kw["params"]["type"] == "electricity" else gas

    monkeypatch.setattr(src, "_get_json", fake_get)
    data = _run(src.fetch_easyenergy(None))

    cur = slot_at(data["electricity"].today, now)
    assert cur is not None
    # easyEnergy levert de eigen opslag (purchasePrice) en all-in (invoicePrice) mee.
    assert cur.fee > 0
    assert abs(cur.total - (cur.market + cur.fee + cur.tax)) < 1e-4
    assert abs(cur.market - cur.market_ex * 1.21) < 1e-3

    # all-in moet exact de invoicePrice uit de API zijn
    raw = next(
        r
        for r in elec["prices"]
        if src.parse_dt(src._clean_ts(r["from"])) == cur.start
    )
    assert abs(cur.total - float(raw["invoicePrice"])) < 1e-6
    assert abs(cur.fee - float(raw["purchasePrice"])) < 1e-9


def test_energyzero_parser(monkeypatch, load_fixture, now):
    elec = load_fixture("energyzero_electricity.json")
    gas = load_fixture("energyzero_gas.json")

    async def fake_get(session, url, **kw):
        return elec if kw["params"]["usageType"] == "1" else gas

    monkeypatch.setattr(src, "_get_json", fake_get)
    data = _run(src.fetch_energyzero(None))

    cur = slot_at(data["electricity"].today, now)
    assert cur is not None
    assert cur.market_ex > 0
    assert cur.fee == 0.0  # EnergyZero levert geen opslag
    assert cur.tax > 0  # energiebelasting via NL-standaard


def test_frank_parser(monkeypatch, load_fixture, now):
    payload = load_fixture("frank_today.json")
    today = now.strftime("%Y-%m-%d")

    async def fake_post(session, url, body):
        match = re.fullmatch(r"(\d{4}-\d\d-\d\d)", body["variables"]["date"])
        # alleen 'vandaag' heeft data in de fixture; andere dagen leeg
        return payload if match and match.group(1) == today else {"data": {}}

    monkeypatch.setattr(src, "_post_json", fake_post)
    data = _run(src.fetch_frank(None))

    cur = slot_at(data["electricity"].today, now)
    assert cur is not None
    assert abs(cur.total - (cur.market + cur.fee + cur.tax)) < 1e-4
    assert cur.market_ex > 0 and cur.fee_ex > 0

    # De all-in moet exact de som van Frank's ruwe velden zijn: sourcingMarkupPrice
    # en energyTaxPrice zijn AL incl. btw (geen extra btw eroverheen rekenen).
    raw = next(
        r
        for r in payload["data"]["marketPrices"]["electricityPrices"]
        if src.parse_dt(r["from"]).hour == now.hour
        and src.parse_dt(r["from"]).date() == now.date()
    )
    expected = (
        float(raw["marketPrice"])
        + float(raw["marketPriceTax"])
        + float(raw["sourcingMarkupPrice"])
        + float(raw["energyTaxPrice"])
    )
    assert abs(cur.total - expected) < 1e-5
    assert abs(cur.fee - float(raw["sourcingMarkupPrice"])) < 1e-9


def test_frank_new_api_shape(monkeypatch, load_fixture, at_time):
    """Echte respons van de nieuwe Frank-API (marketPrices, 16-11-2026, PT60M)."""
    from datetime import datetime, timedelta, timezone

    payload = load_fixture("frank_market_prices_live.json")
    sent = []

    async def fake_post(session, url, body):
        sent.append(body)
        return payload if body["variables"]["date"] == "2026-11-16" else {"data": {"marketPrices": None}}

    at_time(datetime(2026, 11, 16, 12, tzinfo=timezone(timedelta(hours=1))))
    monkeypatch.setattr(src, "_post_json", fake_post)
    data = _run(src.fetch_frank(None))

    assert {b["variables"]["resolution"] for b in sent} == {"PT60M"}
    assert [b["variables"]["date"] for b in sent] == ["2026-11-15", "2026-11-16", "2026-11-17"]
    assert "marketPricesElectricity" not in sent[0]["query"]  # oude query wordt geweigerd
    # De stub rekent met een vaste +02:00, dus tel alle dagen samen.
    for energy in ("electricity", "gas"):
        ed = data[energy]
        assert len((ed.yesterday or []) + ed.today + (ed.tomorrow or [])) == 24
    slots = (data["electricity"].yesterday or []) + data["electricity"].today
    first = min(slots, key=lambda s: s.start)  # 2026-11-15T23:00Z = 00:00 lokaal
    assert first.market_ex == pytest.approx(0.13552)
    assert first.total == pytest.approx(0.13552 + 0.02846 + 0.01815 + 0.11085, abs=1e-5)


def test_easyenergy_gas_is_fetched_per_hour(monkeypatch, load_fixture):
    """'day' gaf kalenderdaggemiddelden (00–00) i.p.v. de gasdag (06–06)."""
    calls = []

    async def fake_get(session, url, **kw):
        calls.append(kw["params"])
        return load_fixture("easyenergy_electricity.json") if kw["params"]["type"] == "electricity" else load_fixture("easyenergy_gas.json")

    monkeypatch.setattr(src, "_get_json", fake_get)
    _run(src.fetch_easyenergy(None))
    assert {(c["type"], c["granularity"]) for c in calls} == {("electricity", "hour"), ("gas", "hour")}


def test_frank_error_for_today_is_not_silently_ignored(monkeypatch):
    async def fake_post(session, url, body):
        return {"data": None, "errors": [{"message": "Graphql validation error"}]}

    monkeypatch.setattr(src, "_post_json", fake_post)
    with pytest.raises(src.SourceError, match="validation"):
        _run(src.fetch_frank(None))


def test_energyzero_window_starts_at_local_midnight(monkeypatch, load_fixture):
    calls = []

    async def fake_get(session, url, **kw):
        calls.append(kw["params"])
        return load_fixture("energyzero_electricity.json")

    monkeypatch.setattr(src, "_get_json", fake_get)
    _run(src.fetch_energyzero(None))
    # 'nu' = 17-08 14:00 +02:00 → gisteren 00:00 lokaal = 15-08 22:00Z; t/m morgen 24:00 lokaal
    assert calls[0]["fromDate"] == "2026-08-15T22:00:00.000Z"
    assert calls[0]["tillDate"] == "2026-08-18T21:59:59.999Z"
