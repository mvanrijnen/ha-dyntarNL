"""Engine: merge-regel, formule op voorspellingen, primaire reeks."""

from datetime import timedelta

import pytest

from dyntarnl.const import CONF_FC_ENSEMBLE, CONF_FC_PROVIDERS, ELECTRICITY, GAS
from dyntarnl.forecast.engine import build, merge, published_records
from dyntarnl.forecast.model import QUARTER, SOURCE_FORECAST, SOURCE_PUBLISHED
from dyntarnl.model import EnergyData
from dyntarnl.prices import apply_formula

from fc_util import AMS, DAY_START, NOW, TARIFF, cfg, prices_data, series

A, B = "epexpredictor", "energypriceforecast"


def test_published_hour_becomes_four_expanded_quarters():
    recs = published_records(prices_data()[ELECTRICITY], "Essent")
    assert len(recs) == 96
    assert recs[1].start - recs[0].start == QUARTER
    assert all(r.expanded and r.source == SOURCE_PUBLISHED and r.supplier == "Essent" for r in recs)


def test_gas_stays_on_its_own_resolution():
    start = DAY_START + timedelta(hours=6)
    slot = apply_formula(start.astimezone(AMS), (start + timedelta(days=1)).astimezone(AMS), 0.35, TARIFF)
    recs = published_records(EnergyData("m³", 21.0, today=[slot]), "x", quarters=False)
    assert len(recs) == 1 and recs[0].end - recs[0].start == timedelta(days=1)


def test_published_always_wins():
    published = published_records(prices_data([0.10] * 24)[ELECTRICITY], "Essent")
    # voorspelling overlapt het laatste gepubliceerde uur én vult daarna aan
    overlap_start = published[-4].start
    data, _ = build(
        prices_data([0.10] * 24),
        {A: series(A, NOW, overlap_start, [0.99] * 12)},
        None, cfg(**{CONF_FC_PROVIDERS: [A]}), "Essent", NOW,
    )
    by_start = {r.start: r for r in data.merged}
    assert by_start[overlap_start].source == SOURCE_PUBLISHED
    assert by_start[overlap_start].price_raw == pytest.approx(0.10)
    after = overlap_start + 4 * QUARTER
    assert by_start[after].source == SOURCE_FORECAST and by_start[after].price_raw == pytest.approx(0.99)


def test_merge_never_lets_forecast_overwrite():
    published = published_records(prices_data()[ELECTRICITY], "x")
    forecast = [r.__class__(**{**{f: getattr(r, f) for f in r.__slots__}, "source": SOURCE_FORECAST, "price_raw": 9.9}) for r in published]
    merged = merge(published, forecast)
    assert all(r.source == SOURCE_PUBLISHED for r in merged)


def test_formula_is_applied_to_forecasts_including_negative():
    tomorrow = DAY_START + timedelta(days=1)
    data, _ = build(
        prices_data(),
        {A: series(A, NOW, tomorrow, [0.08, -0.05])},
        None, cfg(**{CONF_FC_PROVIDERS: [A]}), "Essent", NOW,
    )
    fc = [r for r in data.merged if r.source == SOURCE_FORECAST]
    for rec in fc:
        expected = apply_formula(rec.start, rec.end, rec.price_raw, TARIFF).total
        assert rec.price_allin == pytest.approx(expected)
    assert fc[1].price_allin == pytest.approx((-0.05 + 0.02 + 0.09161) * 1.21, abs=1e-6)


def test_expected_error_allin_scales_with_vat_only():
    tomorrow = DAY_START + timedelta(days=1)
    data, _ = build(
        prices_data(),
        {A: series(A, NOW, tomorrow, [0.10]), B: series(B, NOW, tomorrow, [0.18])},
        None, cfg(), "Essent", NOW,
    )
    rec = data.ensemble[0]
    assert rec.n_sources == 2
    assert rec.expected_error_raw == pytest.approx(0.04)  # alleen halve spreiding (nog geen meting)
    assert rec.expected_error_allin == pytest.approx(0.04 * 1.21)


def test_ensemble_off_uses_first_provider_with_value():
    tomorrow = DAY_START + timedelta(days=1)
    data, _ = build(
        prices_data(),
        {A: series(A, NOW, tomorrow, [0.10] * 4), B: series(B, NOW, tomorrow, [0.20] * 8)},
        None, cfg(**{CONF_FC_ENSEMBLE: False}), "Essent", NOW,
    )
    assert data.ensemble is None
    fc = [r for r in data.merged if r.source == SOURCE_FORECAST]
    assert [r.providers for r in fc] == [(A,)] * 4 + [(B,)] * 4
    assert set(data.providers) == {A, B}  # afzonderlijke reeksen blijven beschikbaar


def test_stale_series_is_ignored():
    tomorrow = DAY_START + timedelta(days=1)
    data, _ = build(
        prices_data(),
        {A: series(A, NOW - timedelta(hours=60), tomorrow, [0.10] * 4)},
        None, cfg(**{CONF_FC_PROVIDERS: [A]}), "Essent", NOW,
    )
    assert not any(r.source == SOURCE_FORECAST for r in data.merged)


def test_no_published_prices_means_no_allin():
    tomorrow = DAY_START + timedelta(days=1)
    data, _ = build(None, {A: series(A, NOW, tomorrow, [0.1])}, None, cfg(**{CONF_FC_PROVIDERS: [A]}), "x", NOW)
    assert data.merged[0].price_allin is None and data.tariff is None


def test_horizon_limits_forecast():
    start = NOW + timedelta(hours=1)
    data, _ = build(
        prices_data(),
        {A: series(A, NOW, start, [0.1] * 4 * 100)},
        None, cfg(**{CONF_FC_PROVIDERS: [A], "forecast_horizon_hours": 24}), "x", NOW,
    )
    assert max(r.start for r in data.merged) < NOW + timedelta(hours=24)
