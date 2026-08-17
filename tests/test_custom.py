"""Tests voor de CUSTOM-berekening (EPEX + eigen opslag/belasting/btw)."""

import asyncio

import dyntarnl.sources as src
from dyntarnl.prices import slot_at


def test_custom_applies_vat_over_markup(monkeypatch, load_fixture, now):
    elec = load_fixture("easyenergy_electricity.json")
    gas = load_fixture("easyenergy_gas.json")

    async def fake_get(session, url, **kw):
        return elec if kw["params"]["type"] == "electricity" else gas

    monkeypatch.setattr(src, "_get_json", fake_get)

    cfg = {
        "vat_percentage": 21.0,
        "markup_electricity": 0.02,
        "markup_gas": 0.08,
        "energy_tax_electricity": 0.09161,
        "energy_tax_gas": 0.60066,
    }
    data = asyncio.run(src.build_custom(None, cfg))
    cur = slot_at(data["electricity"].today, now)
    assert cur is not None

    # btw wordt óók over de opslag gerekend
    assert round(cur.fee, 5) == round(0.02 * 1.21, 5)
    # all-in = (EPEX_excl + opslag + energiebelasting) × 1,21
    expected = (cur.market_ex + 0.02 + 0.09161) * 1.21
    assert abs(cur.total - expected) < 1e-6
    # beurs = EPEX incl. btw
    assert abs(cur.market - cur.market_ex * 1.21) < 1e-6


def test_custom_zero_markup_equals_epex_plus_tax(monkeypatch, load_fixture, now):
    elec = load_fixture("easyenergy_electricity.json")
    gas = load_fixture("easyenergy_gas.json")

    async def fake_get(session, url, **kw):
        return elec if kw["params"]["type"] == "electricity" else gas

    monkeypatch.setattr(src, "_get_json", fake_get)

    cfg = {
        "vat_percentage": 21.0,
        "markup_electricity": 0.0,
        "markup_gas": 0.0,
        "energy_tax_electricity": 0.10,
        "energy_tax_gas": 0.60,
    }
    data = asyncio.run(src.build_custom(None, cfg))
    cur = slot_at(data["electricity"].today, now)
    assert cur.fee == 0.0
    assert abs(cur.total - (cur.market_ex + 0.10) * 1.21) < 1e-6
