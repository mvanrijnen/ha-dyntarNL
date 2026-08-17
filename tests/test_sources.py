"""Tests voor de platform-parsers (fixtures, geen live calls)."""

import asyncio
import re

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
        match = re.search(r'startDate:"(\d{4}-\d\d-\d\d)"', body["query"])
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
        for r in payload["data"]["marketPricesElectricity"]
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
