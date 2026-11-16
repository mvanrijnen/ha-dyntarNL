"""Kapotte of onvolledige antwoorden van bronnen: netjes falen, cache behouden."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

import dyntarnl.sources as src
from dyntarnl.coordinator import DynTarNLCoordinator
from dyntarnl.forecast.model import QUARTER
from dyntarnl.forecast.providers.base import to_quarters
from dyntarnl.forecast.providers.energypriceforecast import EnergyPriceForecastProvider
from dyntarnl.model import Slot, bucket_by_day, tomorrow_complete

from fc_hass import Entry
from fc_util import prices_data

UTC = timezone.utc
UpdateFailed = __import__("sys").modules["homeassistant.helpers.update_coordinator"].UpdateFailed


def _run(coro):
    return asyncio.run(coro)


def _coordinator(monkeypatch, result):
    async def fetch(session):
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr("dyntarnl.coordinator.fetch_easyenergy", fetch)
    c = DynTarNLCoordinator(None, Entry(data={"supplier": "easyenergy"}))
    return c


def test_partial_answer_keeps_cache_and_fails(monkeypatch):
    """Wel gas, geen stroom: niet als geslaagd tellen (anders de hele dag geen stroom)."""
    c = _coordinator(monkeypatch, {"gas": prices_data()["electricity"]})
    c.data = prices_data()
    with pytest.raises(UpdateFailed, match="Onvolledig"):
        _run(c._async_update_data())


def test_answer_without_today_fails(monkeypatch):
    ed = prices_data()["electricity"]
    ed.today = []
    c = _coordinator(monkeypatch, {"electricity": ed})
    with pytest.raises(UpdateFailed, match="vandaag"):
        _run(c._async_update_data())


@pytest.mark.parametrize("error", [KeyError("price"), TypeError("x"), ValueError("json"), AttributeError("get")])
def test_unexpected_shapes_become_update_failed(monkeypatch, error):
    c = _coordinator(monkeypatch, error)
    with pytest.raises(UpdateFailed, match="Onverwacht"):
        _run(c._async_update_data())


def test_tomorrow_complete_needs_electricity():
    assert not tomorrow_complete({"gas": prices_data(tomorrow=[0.1] * 24)["electricity"]})


@pytest.mark.parametrize("bad", [None, "n/a", "NaN", "Infinity", 365.1])
def test_frank_bad_energy_tax_is_an_error_not_a_lower_price(monkeypatch, load_fixture, now, bad):
    payload = load_fixture("frank_today.json")
    payload = json.loads(json.dumps(payload))
    payload["data"]["marketPrices"]["electricityPrices"][0]["energyTaxPrice"] = bad
    today = now.strftime("%Y-%m-%d")

    async def fake_post(session, url, body):
        return payload if body["variables"]["date"] == today else {"data": {}}

    monkeypatch.setattr(src, "_post_json", fake_post)
    with pytest.raises(src.SourceError):
        _run(src.fetch_frank(None))


def test_frank_errors_in_odd_shapes(monkeypatch):
    for errors in ({"message": "x"}, ["kapot"]):
        async def fake_post(session, url, body, errors=errors):
            return {"data": None, "errors": errors}

        monkeypatch.setattr(src, "_post_json", fake_post)
        with pytest.raises(src.SourceError):
            _run(src.fetch_frank(None))


def test_duplicate_rows_count_once():
    base = datetime(2026, 8, 17, 0, tzinfo=timezone(timedelta(hours=2)))
    slot = lambda h, v: Slot(start=base + timedelta(hours=h), end=base + timedelta(hours=h + 1), total=v, market=v, market_ex=v)  # noqa: E731
    ed = bucket_by_day([slot(0, -0.1), slot(0, -0.1), slot(1, 0.2)], "kWh", 21.0)
    assert len(ed.today) == 2


def test_forecast_quarters_skip_bad_values_and_handle_duplicates():
    start = datetime(2026, 10, 4, 10, tzinfo=UTC)
    rows = [(start, 0.1), (start, 0.1), (start + timedelta(hours=1), float("nan")), (start + timedelta(hours=2), 0.2)]
    points = to_quarters(rows)
    assert len(points) == 8 and all(p.expanded for p in points)  # NaN-uur overgeslagen, geen kwartier-misdetectie


def test_epf_start_not_on_quarter_is_not_dropped():
    provider = EnergyPriceForecastProvider({})

    async def fake(session, url, params=None, headers=None):
        return {
            "format": "home-assistant-prices", "country": "NL", "unit": "EUR/kWh", "meta": {},
            "entries": [
                {"start": "2026-10-04T10:07:00.500Z", "value": 0.1, "source": "forecast",
                 "native_resolution_minutes": 15, "expansion_method": "native"},
                {"start": "2026-10-04T10:15:00Z", "value": float("nan"), "source": "forecast",
                 "native_resolution_minutes": 15, "expansion_method": "native"},
            ],
        }

    provider._get_json = fake
    points = _run(provider.async_fetch(None, 48))
    assert [p.start for p in points] == [datetime(2026, 10, 4, 10, tzinfo=UTC)]
