"""Tests die gaten dichten die een mutation-testing-ronde vond (bughunt R9): per test een
fout die de suite eerder niet zag."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import dyntarnl.sources as src
from dyntarnl.const import ELECTRICITY, ENSEMBLE, FC_ERROR_CLIP
from dyntarnl.model import EnergyData, Slot
from dyntarnl.prices import feed_in_value, feed_in_value_ex
from dyntarnl.forecast.accuracy import AccuracyTracker, ensemble_key
from dyntarnl.forecast.coordinator import DynTarNLForecastCoordinator
from dyntarnl.forecast.model import QUARTER, ForecastPoint, QuarterRecord, SOURCE_FORECAST, SOURCE_PUBLISHED, lead_bucket
from dyntarnl.forecast.views import cheapest_block, tomorrow

from fc_hass import Entry
from fc_util import DAY_START, NOW, UTC, AMS, cfg, prices_data, hour_slots

from test_forecast_coordinator import FakePrices, _stub, _points, A, B


def test_feed_in_exact():
    s = Slot(start=NOW, end=NOW + timedelta(hours=1), total=0, market=0.121, market_ex=0.1, fee=0.0605, fee_ex=0.05)
    assert feed_in_value(s) == pytest.approx(0.0605)
    assert feed_in_value_ex(s) == pytest.approx(0.05)


def test_num_rejects_big_negative():
    with pytest.raises(src.SourceError):
        src._num(-365.0, "x")


def test_negative_error_is_clipped():
    t = AccuracyTracker()
    start = NOW + timedelta(hours=30)
    t.add_snapshot("p", NOW, [(start + i * QUARTER, 0.0, None) for i in range(4)])
    sl = Slot(start=start, end=start + timedelta(hours=1), total=0, market=0, market_ex=1.0)
    assert t.settle([sl], start + timedelta(hours=2)) == 4
    st = t.provider_stat("p", lead_bucket(start, NOW))
    assert st.mae == pytest.approx(FC_ERROR_CLIP) and st.bias == pytest.approx(-FC_ERROR_CLIP)


def test_gap_in_snapshot_hour_not_settled_no_crash():
    t = AccuracyTracker()
    start = NOW + timedelta(hours=30)
    pts = [(start + i * QUARTER, 0.1, None) for i in (0, 1, 3, 4, 5, 6, 7)]
    t.add_snapshot("p", NOW, pts)
    s1 = Slot(start=start, end=start + timedelta(hours=1), total=0, market=0, market_ex=0.1)
    assert t.settle([s1], start) == 0


def test_partial_hour_at_snapshot_end_not_settled():
    t = AccuracyTracker()
    start = NOW + timedelta(hours=30)
    t.add_snapshot("p", NOW, [(start + i * QUARTER, 0.1, None) for i in range(2)])
    s1 = Slot(start=start, end=start + timedelta(hours=1), total=0, market=0, market_ex=0.2)
    assert t.settle([s1], start) == 0


def test_ensemble_n_stays_aligned_after_trim():
    t = AccuracyTracker()
    h0 = NOW + timedelta(hours=30)
    pts = [(h0 + i * QUARTER, 0.1, 2 if i < 4 else 1) for i in range(8)]
    t.add_snapshot(ENSEMBLE, NOW, pts)
    s1 = Slot(start=h0, end=h0 + timedelta(hours=1), total=0, market=0, market_ex=0.1)
    s2 = Slot(start=h0 + timedelta(hours=1), end=h0 + timedelta(hours=2), total=0, market=0, market_ex=0.1)
    t.settle([s1], h0)
    t.settle([s1, s2], h0)
    b = lead_bucket(h0, NOW)
    keys = set(t.total)
    assert ensemble_key(b, 2) in keys and ensemble_key(lead_bucket(h0 + timedelta(hours=1), NOW), 1) in keys
    # n = min over hour
    t2 = AccuracyTracker()
    t2.add_snapshot(ENSEMBLE, NOW, [(h0 + i * QUARTER, 0.1, 1 if i == 2 else 2) for i in range(4)])
    t2.settle([s1], h0)
    assert list(t2.total) == [ensemble_key(b, 1)]


def _recs(prices):
    return [QuarterRecord(start=NOW + i * QUARTER, end=NOW + (i + 1) * QUARTER, price_raw=p, price_allin=p,
                          source=SOURCE_PUBLISHED, supplier="x") for i, p in enumerate(prices)]


def test_cheapest_block_exact_size_and_tie():
    r = cheapest_block(_recs([0.1] * 12), NOW, 3, 48)
    assert r is not None and r["start"] == NOW
    assert r["expected_error_allin"] == 0.0


def test_cheapest_block_lookahead_end_exclusive():
    r = cheapest_block(_recs([0.5] * 4 + [0.0]), NOW, 1, 1)
    assert r["start"] == NOW


def test_tomorrow_uses_local_date():
    now = datetime(2026, 8, 17, 23, 30, tzinfo=UTC)  # 01:30 lokaal 18-08
    # Een hele lokale dag (19-08 00:00–24:00 = 18-08 22:00Z–19-08 22:00Z); 'morgen' vereist een hele dag.
    first = datetime(2026, 8, 18, 22, 0, tzinfo=UTC)
    recs = [QuarterRecord(start=first + i * timedelta(minutes=15), end=first + (i + 1) * timedelta(minutes=15),
                          price_raw=1, price_allin=1, source=SOURCE_PUBLISHED, supplier="x") for i in range(96)]
    assert tomorrow(recs, now)["date"] == "2026-08-19"


def _make_fc(**options):
    entry = Entry(entry_id="e1")
    prices = FakePrices(prices_data())
    return DynTarNLForecastCoordinator(None, entry, prices, cfg(**options)), prices


def test_stored_series_of_removed_provider():
    from homeassistant.helpers.storage import Store
    Store.data["dyntarnl.forecast_e1"] = {"series": {"gone": {"f": NOW.isoformat(), "s": NOW.isoformat(), "v": [0.1], "e": [0]}}, "accuracy": None}
    fc, _ = _make_fc()
    asyncio.run(fc.async_load())
    assert fc._loaded and "gone" not in fc.series


def test_empty_forecast_is_failure_and_success_resets():
    fc, _ = _make_fc()
    asyncio.run(fc.async_load())
    calls = []
    _stub(fc, A, [], calls)
    _stub(fc, B, _points(0.2), calls)
    asyncio.run(fc.async_tick())
    assert fc.status[A].failures == 1 and A not in fc.series
    _stub(fc, A, _points(0.2), calls)
    asyncio.run(fc.async_tick(force=True))
    assert fc.status[A].failures == 0 and fc.status[A].state == "ok"


def test_closed_coordinator_does_nothing():
    from homeassistant.helpers.storage import Store
    fc, _ = _make_fc()
    asyncio.run(fc.async_load())
    asyncio.run(fc.async_flush())
    Store.data.clear()
    calls = []
    _stub(fc, A, _points(0.2), calls)
    _stub(fc, B, _points(0.2), calls)
    asyncio.run(fc.async_tick(force=True))
    fc.rebuild(snapshot=())
    assert calls == [] and Store.data == {}


def test_rebuild_before_load_does_not_save():
    from homeassistant.helpers.storage import Store
    fc, _ = _make_fc()
    from fc_util import series
    fc.series[A] = series(A, NOW, NOW + timedelta(hours=12), [0.1] * 8)
    fc.rebuild(snapshot=[A])
    assert Store.data == {}


def test_snapshot_skips_published_quarters():
    fc, prices = _make_fc()
    asyncio.run(fc.async_load())
    calls = []
    # forecast starting at NOW (today, published) through tomorrow
    pts = [ForecastPoint(NOW + i * QUARTER, 0.2) for i in range(4 * 20)]
    _stub(fc, A, pts, calls)
    _stub(fc, B, pts, calls)
    asyncio.run(fc.async_tick())
    end_today = DAY_START + timedelta(days=1)
    for snap in fc.tracker.snapshots:
        assert datetime.fromisoformat(snap["s"]) >= end_today


def test_custom_keeps_tomorrow(monkeypatch):
    tmw = hour_slots(DAY_START + timedelta(days=1), [0.1] * 24)
    base = {ELECTRICITY: EnergyData(unit="kWh", vat_percentage=21.0, today=hour_slots(DAY_START, [0.1] * 24), tomorrow=tmw)}

    async def fake(session, source):
        return base
    monkeypatch.setattr(src, "_epex_source", fake)
    data = asyncio.run(src.build_custom(None, {"markup_electricity": 0.0, "tax_electricity": 0.0, "vat": 21}))
    assert data[ELECTRICITY].tomorrow and data[ELECTRICITY].tomorrow[0].total == pytest.approx(0.121)


def test_easyenergy_requests_through_day_after_tomorrow(monkeypatch, load_fixture):
    seen = []

    async def fake(session, url, **kw):
        seen.append(kw.get("params"))
        return load_fixture("easyenergy_electricity.json")
    monkeypatch.setattr(src, "_get_json", fake)
    asyncio.run(src.fetch_easyenergy(None))
    assert seen[0]["start"] == "2026-08-16" and seen[0]["end"] == "2026-08-19"


def test_frank_tomorrow_errors_tolerated_today_errors_raise(monkeypatch, load_fixture):
    today = load_fixture("frank_today.json")
    calls = []

    async def post(session, url, payload):
        calls.append(payload["variables"]["date"])
        if payload["variables"]["date"] == "2026-08-18":
            return {"errors": [{"message": "not yet"}], "data": None}
        return today
    monkeypatch.setattr(src, "_post_json", post)
    asyncio.run(src.fetch_frank(None))

    async def post2(session, url, payload):
        if payload["variables"]["date"] == "2026-08-17":
            return {"errors": [{"message": "boom"}], "data": None}
        return today
    monkeypatch.setattr(src, "_post_json", post2)
    with pytest.raises(src.SourceError):
        asyncio.run(src.fetch_frank(None))


def test_easyenergy_total_fallback_without_invoice(monkeypatch, load_fixture):
    elec = load_fixture("easyenergy_electricity.json")
    rows = elec["prices"] if isinstance(elec, dict) else elec
    for r in rows:
        r.pop("invoicePrice", None)

    async def fake(session, url, **kw):
        return elec
    monkeypatch.setattr(src, "_get_json", fake)
    data = asyncio.run(src.fetch_easyenergy(None))
    s = data[ELECTRICITY].today[0]
    assert s.total == pytest.approx(s.market + s.fee + s.tax)


def test_eon_missing_vat_percentage_defaults_21(monkeypatch, load_fixture):
    payload = load_fixture("essent.json")
    for day in payload["prices"]:
        for e in ("electricity", "gas"):
            if day.get(e):
                day[e].pop("vatPercentage", None)

    async def fake(session, url, **kw):
        return payload
    monkeypatch.setattr(src, "_get_json", fake)
    data = asyncio.run(src.fetch_eon_app(None, "x"))
    assert data[ELECTRICITY].vat_percentage == 21.0


from test_resilience import started  # noqa: F401


def test_retry_fetches_while_tomorrow_missing(started):
    entry, _, retry, _ = started
    c = entry.runtime_data
    c.data[ELECTRICITY].tomorrow = None
    entry.tasks.clear()
    retry()
    assert entry.tasks == ["dyntarnl_fetch"]


def test_rebucket_notifies_listeners(started, at_time):
    entry, hourly, _, _ = started
    c = entry.runtime_data
    seen = []
    c.async_add_listener(lambda: seen.append(1))
    c.async_rebucket()
    assert seen


def test_bias_correction_needs_min_samples():
    from dyntarnl.forecast.ensemble import Stat, corrected
    cf = cfg(forecast_bias_correction=True)
    assert corrected(0.1, Stat(count=1, mae=0.01, bias=0.05), cf) == 0.1
    assert corrected(0.1, Stat(count=10_000, mae=0.01, bias=0.05), cf) == pytest.approx(0.05)


def test_tariff_from_latest_slot():
    from dyntarnl.forecast.engine import current_tariff
    from dyntarnl.prices import Tariff
    ed = EnergyData(unit="kWh", vat_percentage=21.0, today=hour_slots(DAY_START, [0.1] * 24),
                    tomorrow=hour_slots(DAY_START + timedelta(days=1), [0.1] * 24, Tariff(0.03, 0.1, 21.0)))
    assert current_tariff(ed).fee_ex == 0.03


def test_due_tolerance():
    from fc_util import series
    fc, _ = _make_fc()
    fc.series[A] = series(A, NOW - timedelta(hours=6) + timedelta(minutes=3), NOW, [0.1])
    assert fc.is_due(A, NOW)


def test_unknown_provider_rejected_also_for_gas():
    from dyntarnl.forecast.views import service_response
    with pytest.raises(ValueError):
        service_response(prices_data(), None, cfg(), "x", "gas", True, "bogus", None, NOW)


def test_ensemble_mae_ignores_thin_stats():
    from dyntarnl.forecast.engine import ensemble_mae
    t = AccuracyTracker()
    b = lead_bucket(NOW + timedelta(hours=30), NOW)
    t._add("2026-08-17", ensemble_key(b, 2), 4, 0.05)
    t._add("2026-08-17", f"{A}|{b}", 4, 0.05)
    assert ensemble_mae(t, b, 2, (A,), cfg()) is None
    t._add("2026-08-16", ensemble_key(b, 2), 400, 0.01)
    t._add("2026-08-16", ensemble_key(b, 3), 4000, 0.10)
    assert ensemble_mae(t, b, 2, (A,), cfg()) == pytest.approx((4 * 0.05 + 400 * 0.01) / 404)


def test_ensemble_snapshot_ignores_stale_series():
    from fc_util import series
    fc, _ = _make_fc()
    asyncio.run(fc.async_load())
    fc.series[A] = series(A, NOW - timedelta(hours=60), NOW + timedelta(hours=12), [0.1] * 400)
    calls = []
    _stub(fc, B, _points(0.2), calls)
    asyncio.run(fc.async_tick())
    ens = [s for s in fc.tracker.snapshots if s["p"] == ENSEMBLE]
    assert ens and all(datetime.fromisoformat(s["f"]) == NOW for s in ens)


def test_rebuild_after_flush_does_not_save():
    from homeassistant.helpers.storage import Store
    from fc_util import series
    fc, _ = _make_fc()
    asyncio.run(fc.async_load())
    asyncio.run(fc.async_flush())
    Store.data.clear()
    fc.series[A] = series(A, NOW, DAY_START + timedelta(days=1), [0.1] * 8)
    fc.rebuild(snapshot=[A])
    assert Store.data == {}


def test_known_quarters_not_settled_as_forecast():
    fc, prices = _make_fc()
    asyncio.run(fc.async_load())
    calls = []
    pts = [ForecastPoint(NOW + i * QUARTER, 0.2) for i in range(4 * 20)]
    _stub(fc, A, pts, calls)
    _stub(fc, B, pts, calls)
    asyncio.run(fc.async_tick())
    assert fc.tracker.total == {}


def test_weight_floor_with_three_sources_never_goes_negative():
    from dyntarnl.forecast.ensemble import apply_floor

    weights = apply_floor({"a": 0.8, "b": 0.1, "c": 0.1}, 0.5)
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) == pytest.approx(1)
