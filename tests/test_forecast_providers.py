"""Providers: parsen van de echte (vastgelegde) responses, eenheden, tijdzones."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from dyntarnl.const import CONF_FC_EPEXPREDICTOR_URL, CONF_FC_EPF_API_KEY
from dyntarnl.forecast.model import QUARTER
from dyntarnl.forecast.providers import PROVIDERS, ProviderAuthError, ProviderError
from dyntarnl.forecast.providers.base import to_quarters
from dyntarnl.forecast.providers.energypriceforecast import EnergyPriceForecastProvider
from dyntarnl.forecast.providers.epexpredictor import EpexPredictorProvider

UTC = timezone.utc


def _fake(payload, calls):
    async def get_json(session, url, params=None, headers=None):
        calls.append({"url": url, "params": params, "headers": headers})
        return payload

    return get_json


def test_registry_contains_both_providers():
    assert set(PROVIDERS) == {"epexpredictor", "energypriceforecast"}


def test_hour_to_quarters_marks_expanded():
    start = datetime(2026, 10, 4, 10, tzinfo=UTC)
    points = to_quarters([(start, 0.1), (start + timedelta(hours=1), 0.2)])
    assert [p.start for p in points] == [start + i * QUARTER for i in range(8)]
    assert [p.price_raw for p in points] == [0.1] * 4 + [0.2] * 4
    assert all(p.expanded for p in points)


def test_quarters_stay_quarters():
    start = datetime(2026, 10, 4, 10, tzinfo=UTC)
    points = to_quarters([(start + i * QUARTER, 0.1 * i) for i in range(4)])
    assert len(points) == 4 and not any(p.expanded for p in points)


def test_epexpredictor_parses_only_forecasts(load_fixture):
    payload = load_fixture("epexpredictor_nl.json")
    calls = []
    provider = EpexPredictorProvider({CONF_FC_EPEXPREDICTOR_URL: "http://192.168.1.5:8000/"})
    provider._get_json = _fake(payload, calls)

    points = asyncio.run(provider.async_fetch(None, 72))

    known = datetime.fromisoformat(payload["knownUntil"].replace("Z", "+00:00"))
    assert points and all(p.start > known for p in points)
    assert all(p.start.tzinfo == UTC for p in points)
    assert not any(p.expanded for p in points)  # publieke instantie levert kwartieren
    first = next(r for r in payload["prices"] if datetime.fromisoformat(r["startsAt"].replace("Z", "+00:00")) > known)
    assert points[0].price_raw == pytest.approx(first["total"] / 1000)  # €/MWh → €/kWh
    # Instelbare URL, en nooit de opslag/btw-opties van de bron.
    assert calls[0]["url"] == "http://192.168.1.5:8000/prices"
    assert calls[0]["params"]["region"] == "NL" and calls[0]["params"]["unit"] == "EUR_PER_MWH"
    assert "surcharge" not in calls[0]["params"] and "taxPercent" not in calls[0]["params"]


def test_epexpredictor_hourly_selfhosted_is_expanded():
    payload = {
        "knownUntil": "2026-10-04T21:45:00Z",
        "prices": [
            {"startsAt": "2026-10-04T22:00:00Z", "total": 100.0},
            {"startsAt": "2026-10-04T23:00:00Z", "total": 120.0},
        ],
    }
    provider = EpexPredictorProvider({})
    provider._get_json = _fake(payload, [])
    points = asyncio.run(provider.async_fetch(None, 48))
    assert len(points) == 8 and all(p.expanded for p in points)
    assert points[4].price_raw == pytest.approx(0.12)


def test_epf_parses_only_forecast_entries(load_fixture):
    payload = load_fixture("energypriceforecast_nl.json")
    calls = []
    provider = EnergyPriceForecastProvider({CONF_FC_EPF_API_KEY: "secret"})
    provider._get_json = _fake(payload, calls)

    points = asyncio.run(provider.async_fetch(None, 200))

    forecast = [e for e in payload["entries"] if e["source"] == "forecast"]
    assert len(points) == len(forecast)
    assert {p.start for p in points} == {
        datetime.fromisoformat(e["start"].replace("Z", "+00:00")) for e in forecast
    }
    by_start = {p.start: p for p in points}
    for e in forecast:
        p = by_start[datetime.fromisoformat(e["start"].replace("Z", "+00:00"))]
        assert p.price_raw == pytest.approx(e["value"])  # al EUR/kWh
        assert p.expanded == (e["expansion_method"] == "repeat_hourly")
    assert calls[0]["headers"]["Authorization"] == "Bearer secret"
    assert calls[0]["params"]["price_mode"] == "base"
    assert calls[0]["params"]["hours"] == "120"  # begrensd op de max van de bron


def test_epf_without_key_sends_no_auth(load_fixture):
    calls = []
    provider = EnergyPriceForecastProvider({})
    provider._get_json = _fake(load_fixture("energypriceforecast_nl.json"), calls)
    asyncio.run(provider.async_fetch(None, 48))
    assert "Authorization" not in calls[0]["headers"]


def test_epf_rejected_key(load_fixture):
    payload = load_fixture("energypriceforecast_nl.json")
    payload["meta"]["api_key_state"] = "invalid"
    provider = EnergyPriceForecastProvider({CONF_FC_EPF_API_KEY: "bad"})
    provider._get_json = _fake(payload, [])
    with pytest.raises(ProviderAuthError):
        asyncio.run(provider.async_fetch(None, 48))
    assert asyncio.run(provider.async_validate(None)) == "invalid_api_key"


def test_epf_unexpected_format_is_an_error():
    provider = EnergyPriceForecastProvider({})
    provider._get_json = _fake({"format": "something-else"}, [])
    with pytest.raises(ProviderError):
        asyncio.run(provider.async_fetch(None, 48))
    assert asyncio.run(provider.async_validate(None)) == "cannot_connect"
