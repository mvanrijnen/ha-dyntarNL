"""Eén leveranciersformule: `apply_formula` reproduceert de gepubliceerde all-in."""

import asyncio
import re
from datetime import datetime, timedelta, timezone

import pytest

import dyntarnl.sources as src
from dyntarnl.prices import Tariff, apply_formula, tariff_from_slot

AMS = timezone(timedelta(hours=2))


def _patch_sources(monkeypatch, load_fixture, now):
    elec = load_fixture("easyenergy_electricity.json")
    gas = load_fixture("easyenergy_gas.json")
    essent = load_fixture("essent.json")
    frank = load_fixture("frank_today.json")
    today = now.strftime("%Y-%m-%d")

    async def fake_get(session, url, **kw):
        params = kw.get("params") or {}
        if "type" in params:
            return elec if params["type"] == "electricity" else gas
        return essent

    async def fake_post(session, url, body):
        m = re.search(r'startDate:"(\d{4}-\d\d-\d\d)"', body["query"])
        return frank if m and m.group(1) == today else {"data": {}}

    monkeypatch.setattr(src, "_get_json", fake_get)
    monkeypatch.setattr(src, "_post_json", fake_post)


@pytest.mark.parametrize(
    "fetch",
    [
        lambda: src.fetch_easyenergy(None),
        lambda: src.fetch_eon_app(None, "www.essent.nl"),
        lambda: src.fetch_frank(None),
    ],
    ids=["easyenergy", "essent", "frank"],
)
def test_formula_reproduces_published_totals(monkeypatch, load_fixture, now, fetch):
    """Uit één gepubliceerd uur afgeleide formule geeft elk ander uur exact terug."""
    _patch_sources(monkeypatch, load_fixture, now)
    ed = asyncio.run(fetch())["electricity"]
    tariff = tariff_from_slot(ed.today[-1], ed.vat_percentage)
    for s in ed.today:
        rebuilt = apply_formula(s.start, s.end, s.market_ex, tariff)
        assert abs(rebuilt.total - s.total) < 5e-5


def test_formula_on_negative_market_price():
    """Negatieve beurs gaat lineair mee: btw ook over het negatieve deel."""
    start = datetime(2026, 8, 17, 13, tzinfo=AMS)
    s = apply_formula(start, start + timedelta(hours=1), -0.05, Tariff(0.02, 0.09161, 21.0))
    assert abs(s.total - (-0.05 + 0.02 + 0.09161) * 1.21) < 1e-6
    assert s.market < 0
    assert abs(s.market - -0.05 * 1.21) < 1e-6
