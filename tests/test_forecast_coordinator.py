"""Forecast-coordinator: isolatie, time-outs, backoff, schema en opslag."""

import asyncio
from datetime import datetime, timedelta
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
    asyncio.run(fc.async_load())  # zoals async_start: eerst de opslag inlezen
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
    assert restarted.status[A].last_success == NOW  # last_fetch-sensor niet leeg na herstart
    assert restarted.status[A].state == "ok"
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


# --- bughunt ronde 3 ------------------------------------------------------------


def test_snapshots_skip_quarters_that_are_already_published():
    """Een al gepubliceerd kwartier is geen voorspelling meer en mag de MAE niet vervuilen."""
    fc, prices = _make()
    prices.data = prices_data(tomorrow=[0.10] * 24)  # morgen staat al gepubliceerd
    _stub(fc, A, _points(0.12, hours=48), [])  # voorspelt morgen + overmorgen
    _stub(fc, B, _points(0.08, hours=48), [])
    asyncio.run(fc.async_tick())
    published_end = TOMORROW + timedelta(days=1)
    for snap in fc.tracker.snapshots:
        assert datetime.fromisoformat(snap["s"]) >= published_end, snap["p"]


def test_accuracy_history_survives_turning_accuracy_off(monkeypatch):
    fc, prices = _make(entry_id="acc")
    _stub(fc, A, _points(0.12), [])
    _stub(fc, B, _points(0.08), [])
    asyncio.run(fc.async_tick())
    prices.data = prices_data(tomorrow=[0.10] * 24)
    fc.handle_prices_update()
    before = fc.tracker.to_dict()["total"]
    assert before

    off, _ = _make(entry_id="acc", forecast_accuracy=False, forecast_weighting="equal")
    asyncio.run(off.async_load())
    asyncio.run(off.async_flush())
    on, _ = _make(entry_id="acc")
    asyncio.run(on.async_load())
    assert on.tracker.to_dict()["total"] == before


def test_unreadable_stored_series_is_skipped():
    import sys

    store = sys.modules["homeassistant.helpers.storage"].Store
    store.data["dyntarnl.forecast_broken"] = {"series": {A: {"f": None, "s": "nonsense", "v": [0.1], "e": [0]}}}
    fc, _ = _make(entry_id="broken")
    asyncio.run(fc.async_load())  # mag niet crashen
    assert A not in fc.series


def test_concurrent_ticks_fetch_once():
    fc, _ = _make()
    calls = []

    async def slow():
        await asyncio.sleep(0.05)
        return _points(0.1)

    _stub(fc, A, slow, calls)
    _stub(fc, B, slow, calls)

    async def both():
        await asyncio.gather(fc.async_tick(), fc.async_tick())

    asyncio.run(both())
    assert sorted(calls) == sorted([A, B])  # niet twee keer


def test_flush_writes_pending_state_now():
    import sys

    store = sys.modules["homeassistant.helpers.storage"].Store
    fc, _ = _make(entry_id="flush")
    _stub(fc, A, _points(0.1), [])
    _stub(fc, B, _points(0.2), [])
    asyncio.run(fc.async_tick())
    store.data.pop("dyntarnl.forecast_flush", None)
    asyncio.run(fc.async_flush())
    assert set(store.data["dyntarnl.forecast_flush"]["series"]) == {A, B}


def test_provider_info_survives_restart():
    fc, _ = _make(entry_id="info")
    _stub(fc, A, _points(0.1), [])
    _stub(fc, B, _points(0.2), [])
    fc.providers[B].info = {"plan": "private_pro", "allowed_horizon_hours": 120}
    asyncio.run(fc.async_tick())
    asyncio.run(fc.async_flush())
    restarted, _ = _make(entry_id="info")
    asyncio.run(restarted.async_load())
    assert restarted.providers[B].info == {"plan": "private_pro", "allowed_horizon_hours": 120}


def test_forced_refresh_during_running_tick_is_not_lost():
    """Ververs-knop tijdens een lopende ronde: daarna alsnog geforceerd ophalen."""
    fc, _ = _make()
    calls = []

    async def slow():
        await asyncio.sleep(0.05)
        return _points(0.1)

    _stub(fc, A, slow, calls)
    _stub(fc, B, slow, calls)

    async def scenario():
        first = asyncio.ensure_future(fc.async_tick())
        await asyncio.sleep(0.01)
        await fc.async_tick(force=True)  # ronde loopt al
        await first

    asyncio.run(scenario())
    assert sorted(calls) == sorted([A, B, A, B])  # tweede (geforceerde) ronde na de eerste


def test_nothing_is_saved_after_unload():
    import sys

    store = sys.modules["homeassistant.helpers.storage"].Store
    fc, _ = _make(entry_id="closed")
    asyncio.run(fc.async_flush())
    store.data.pop("dyntarnl.forecast_closed", None)
    fc.rebuild()
    asyncio.run(fc.async_tick(force=True))
    assert "dyntarnl.forecast_closed" not in store.data


def test_hourly_rebuild_without_changes_does_not_rewrite_storage():
    import sys

    store = sys.modules["homeassistant.helpers.storage"].Store
    fc, _ = _make(entry_id="quiet")
    _stub(fc, A, _points(0.1), [])
    _stub(fc, B, _points(0.2), [])
    asyncio.run(fc.async_tick())
    store.data.pop("dyntarnl.forecast_quiet", None)
    fc.handle_prices_update()  # geen nieuwe reeks, niets af te rekenen
    assert "dyntarnl.forecast_quiet" not in store.data


def test_ensemble_snapshot_uses_oldest_contributing_fetch(at_time):
    fc, _ = _make()
    _stub(fc, A, _points(0.1), [])
    _stub(fc, B, _points(0.2), [])
    asyncio.run(fc.async_tick())
    at_time(NOW + timedelta(hours=6))
    _stub(fc, B, _points(0.2), [])
    _stub(fc, A, RuntimeError("down"), [])  # A blijft op zijn oude reeks
    asyncio.run(fc.async_tick())
    ens = [s for s in fc.tracker.snapshots if s["p"] == "ensemble"][-1]
    assert datetime.fromisoformat(ens["f"]) == NOW  # A's fetch van 6 uur eerder


def test_unload_during_load_does_not_wipe_storage():
    """Herladen terwijl de opslag nog werd ingelezen mocht geen lege stand wegschrijven."""
    import sys

    from dyntarnl.forecast.coordinator import DynTarNLForecastCoordinator as FC

    store = sys.modules["homeassistant.helpers.storage"].Store
    saved = {"series": {A: {"f": NOW.isoformat(), "s": TOMORROW.isoformat(), "v": [0.1], "e": [0]}}, "accuracy": {"total": {"x": [4, 0.1, 0.1]}}}
    store.data["dyntarnl.forecast_race"] = saved
    fc = FC(None, Entry(entry_id="race"), FakePrices(prices_data()), cfg())  # nog niet ingelezen
    asyncio.run(fc.async_flush())
    assert store.data["dyntarnl.forecast_race"] == saved
