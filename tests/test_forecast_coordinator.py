"""Forecast-coordinator: isolatie, time-outs, backoff, schema en opslag."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

import dyntarnl.forecast.coordinator as fcoord
from dyntarnl.const import ENSEMBLE
from dyntarnl.forecast.coordinator import DynTarNLForecastCoordinator, backoff
from dyntarnl.forecast.model import QUARTER, ForecastPoint, SOURCE_FORECAST

from fc_hass import Entry
from fc_util import DAY_START, NOW, cfg, prices_data

A, B = "epexpredictor", "energypriceforecast"
TOMORROW = DAY_START + timedelta(days=1)


class FakePrices:
    def __init__(self, data):
        self.data = data
        self.supplier = SimpleNamespace(name="Essent")


def _points(value, hours=24):
    return [ForecastPoint(TOMORROW + i * QUARTER, value) for i in range(hours * 4)]


def _make(entry_id="e1", **options):
    entry = Entry(entry_id=entry_id)
    prices = FakePrices(prices_data())
    fc = DynTarNLForecastCoordinator(None, entry, prices, cfg(**options))
    return fc, prices


def _stub(fc, key, result, calls):
    async def fetch(session, horizon):
        calls.append(key)
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            return await result()
        return result

    fc.providers[key].async_fetch = fetch


def test_backoff_grows_and_caps():
    assert backoff(1) == timedelta(minutes=30)
    assert backoff(2) == timedelta(hours=1)
    assert backoff(3) == timedelta(hours=2)
    assert backoff(20) == timedelta(hours=12)


def test_failing_provider_is_isolated():
    fc, _ = _make()
    calls = []
    _stub(fc, A, RuntimeError("boom"), calls)
    _stub(fc, B, _points(0.2), calls)

    asyncio.run(fc.async_tick())

    assert fc.status[A].failures == 1 and "boom" in fc.status[A].last_error
    assert fc.status[A].next_attempt == NOW + timedelta(minutes=30)
    assert fc.status[B].state == "ok"
    assert set(fc.series) == {B}
    forecasts = [r for r in fc.data.merged if r.source == SOURCE_FORECAST]
    assert forecasts and all(r.providers == (B,) for r in forecasts)


def test_slow_provider_times_out(monkeypatch):
    monkeypatch.setattr(fcoord, "FC_TIMEOUT", 0.01)
    fc, _ = _make()

    async def slow():
        await asyncio.sleep(5)

    _stub(fc, A, slow, [])
    _stub(fc, B, _points(0.2), [])
    asyncio.run(fc.async_tick())
    assert "TimeoutError" in fc.status[A].last_error
    assert B in fc.series


def test_backoff_is_respected_then_retried(at_time):
    fc, _ = _make()
    calls = []
    _stub(fc, A, RuntimeError("down"), calls)
    _stub(fc, B, _points(0.2), calls)
    asyncio.run(fc.async_tick())
    calls.clear()

    at_time(NOW + timedelta(minutes=15))
    asyncio.run(fc.async_tick())
    assert calls == []  # A in backoff, B nog vers

    at_time(NOW + timedelta(minutes=31))
    asyncio.run(fc.async_tick())
    assert calls == [A]


def test_fetch_interval_is_respected(at_time):
    fc, _ = _make(forecast_interval_hours=6)
    calls = []
    _stub(fc, A, _points(0.1), calls)
    _stub(fc, B, _points(0.2), calls)
    asyncio.run(fc.async_tick())
    assert sorted(calls) == sorted([A, B])
    calls.clear()

    at_time(NOW + timedelta(hours=3))
    asyncio.run(fc.async_tick())
    assert calls == []

    at_time(NOW + timedelta(hours=6))
    asyncio.run(fc.async_tick())
    assert sorted(calls) == sorted([A, B])


def test_rebuild_error_never_breaks_price_listeners(monkeypatch):
    fc, _ = _make()

    def explode(*a, **k):
        raise RuntimeError("bug in engine")

    monkeypatch.setattr(fcoord, "build", explode)
    fc.handle_prices_update()  # mag niet gooien


def test_snapshots_are_settled_when_prices_arrive():
    fc, prices = _make()
    _stub(fc, A, _points(0.12), [])
    _stub(fc, B, _points(0.08), [])
    asyncio.run(fc.async_tick())
    assert {s["p"] for s in fc.tracker.snapshots} == {A, B, ENSEMBLE}

    prices.data = prices_data(tomorrow=[0.10] * 24)
    fc.handle_prices_update()

    stat_a = fc.tracker.provider_stat(A, "d0_1")
    stat_b = fc.tracker.provider_stat(B, "d0_1")
    assert stat_a.bias == pytest.approx(0.02) and stat_b.bias == pytest.approx(-0.02)
    # 10:00–22:00 UTC morgen valt in d0_1, de rest in d2_3: samen 96 kwartieren
    total = sum(s.count for b in ("d0_1", "d2_3") if (s := fc.tracker.provider_stat(A, b)))
    assert total == 96
    assert fc.tracker.ensemble_stat("d0_1", 2).mae == pytest.approx(0.0)
    # gepubliceerd wint: geen voorspelde kwartieren meer voor morgen
    assert not any(r.source == SOURCE_FORECAST and r.start < TOMORROW + timedelta(days=1) for r in fc.data.merged)


def test_restart_restores_series_without_refetch(at_time):
    fc, _ = _make(entry_id="persist")
    calls = []
    _stub(fc, A, _points(0.1), calls)
    _stub(fc, B, _points(0.2), calls)
    asyncio.run(fc.async_tick())

    at_time(NOW + timedelta(hours=1))
    restarted, _ = _make(entry_id="persist")
    calls2 = []
    _stub(restarted, A, _points(0.1), calls2)
    _stub(restarted, B, _points(0.2), calls2)
    asyncio.run(restarted.async_start())

    assert calls2 == []  # nog vers: geen extra calls na herstart
    assert set(restarted.series) == {A, B}
    assert len(restarted.series[A].points) == 96
    assert len(restarted.tracker.snapshots) == len(fc.tracker.snapshots)


def test_accuracy_off_has_no_tracker():
    fc, _ = _make(forecast_accuracy=False, forecast_weighting="equal")
    _stub(fc, A, _points(0.1), [])
    _stub(fc, B, _points(0.2), [])
    asyncio.run(fc.async_tick())
    assert fc.tracker is None and fc.data.ensemble


def test_all_forecast_sensor_functions_compute(monkeypatch):
    """Roep elke value_fn/attr_fn direct aan (de entiteit zelf vangt fouten af)."""
    from dyntarnl.forecast.sensor import forecast_descriptions, forecast_entities

    fc, prices = _make()
    _stub(fc, A, _points(0.12, hours=48), [])
    _stub(fc, B, _points(0.08, hours=48), [])
    asyncio.run(fc.async_tick())
    prices.data = prices_data(tomorrow=[0.10] * 24)
    fc.handle_prices_update()

    values = {}
    for d in forecast_descriptions(fc.cfg):
        values[d.key] = d.value_fn(fc)
        if d.attr_fn:
            d.attr_fn(fc)
    assert values["forecast_cheapest_start"] is not None
    assert values["forecast_total_avg_24h"] == pytest.approx(prices.data["electricity"].today[0].total, abs=1e-5)
    assert values["forecast_epexpredictor_bias"] == pytest.approx(0.02)
    assert values["forecast_epexpredictor_settled"] == 96
    assert values["forecast_epexpredictor_weight"] == pytest.approx(50.0)  # nog < min_samples per bucket
    assert values["forecast_ensemble_settled"] == 96

    entities = forecast_entities(fc)
    chart = next(e for e in entities if e.entity_description.chart)
    assert "prices" in chart._unrecorded_attributes
    assert chart.extra_state_attributes["prices"][0][2] in ("p", "f")
