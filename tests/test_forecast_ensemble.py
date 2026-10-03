"""Gewichten en combinatie van het ensemble."""

from datetime import timedelta

import pytest

from dyntarnl.const import (
    CONF_FC_BIAS,
    CONF_FC_MIN_SAMPLES,
    CONF_FC_WEIGHT_EXPONENT,
    CONF_FC_WEIGHT_FLOOR,
    CONF_FC_WEIGHTING,
)
from dyntarnl.forecast.ensemble import Stat, apply_floor, combine, compute_weights, expected_error

from fc_util import NOW, cfg, series

A, B = "epexpredictor", "energypriceforecast"


def test_equal_weights_below_min_samples():
    stats = {A: Stat(count=50, mae=0.01, bias=0), B: Stat(count=500, mae=0.04, bias=0)}
    assert compute_weights(stats, cfg(**{CONF_FC_MIN_SAMPLES: 96})) == {A: 0.5, B: 0.5}


def test_equal_weights_without_any_measurement():
    assert compute_weights({A: None, B: None}, cfg()) == {A: 0.5, B: 0.5}


def test_equal_weighting_option_ignores_accuracy():
    stats = {A: Stat(500, 0.01, 0), B: Stat(500, 0.04, 0)}
    assert compute_weights(stats, cfg(**{CONF_FC_WEIGHTING: "equal"})) == {A: 0.5, B: 0.5}


def test_inverse_mae_weights():
    stats = {A: Stat(500, 0.01, 0), B: Stat(500, 0.03, 0)}
    w = compute_weights(stats, cfg(**{CONF_FC_WEIGHT_FLOOR: 0}))
    assert w[A] == pytest.approx(0.75) and w[B] == pytest.approx(0.25)


def test_inverse_mae_squared_weights():
    stats = {A: Stat(500, 0.01, 0), B: Stat(500, 0.03, 0)}
    w = compute_weights(stats, cfg(**{CONF_FC_WEIGHT_FLOOR: 0, CONF_FC_WEIGHT_EXPONENT: 2}))
    assert w[A] == pytest.approx(0.9) and w[B] == pytest.approx(0.1)


def test_weight_floor_keeps_bad_provider_in():
    stats = {A: Stat(500, 0.001, 0), B: Stat(500, 0.5, 0)}
    w = compute_weights(stats, cfg(**{CONF_FC_WEIGHT_FLOOR: 0.2}))
    assert w[B] == pytest.approx(0.2) and w[A] == pytest.approx(0.8)
    assert sum(w.values()) == pytest.approx(1)


def test_floor_with_three_providers_renormalises_rest():
    w = apply_floor({"a": 0.9, "b": 0.08, "c": 0.02}, 0.1)
    assert w["b"] == pytest.approx(0.1) and w["c"] == pytest.approx(0.1)
    assert w["a"] == pytest.approx(0.8)


def test_combine_with_different_horizons():
    """A loopt 2 uur, B 4 uur: eerste 2 uur n=2 (gemiddeld), daarna alleen B (n=1)."""
    start = NOW + timedelta(hours=1)
    a = series(A, NOW, start, [0.10] * 8)
    b = series(B, NOW, start, [0.20] * 16)
    out = combine([a, b], lambda p, bucket: None, cfg())

    first, last = out[start], out[start + timedelta(hours=3)]
    assert first.n_sources == 2 and first.value == pytest.approx(0.15)
    assert (first.spread_min, first.spread_max) == (0.10, 0.20)
    assert last.n_sources == 1 and last.value == pytest.approx(0.20) and last.providers == (B,)


def test_combine_uses_measured_weights_per_provider_bucket():
    start = NOW + timedelta(hours=1)
    stats = {A: Stat(500, 0.01, 0), B: Stat(500, 0.03, 0)}
    out = combine(
        [series(A, NOW, start, [0.10]), series(B, NOW, start, [0.20])],
        lambda p, bucket: stats[p] if bucket == "d0_1" else None,
        cfg(**{CONF_FC_WEIGHT_FLOOR: 0}),
    )
    assert out[start].value == pytest.approx(0.75 * 0.10 + 0.25 * 0.20)


def test_bias_correction_subtracts_measured_bias():
    start = NOW + timedelta(hours=1)
    stats = {A: Stat(500, 0.02, 0.02), B: Stat(500, 0.02, -0.01)}
    out = combine(
        [series(A, NOW, start, [0.12]), series(B, NOW, start, [0.09])],
        lambda p, bucket: stats[p],
        cfg(**{CONF_FC_BIAS: True}),
    )
    assert out[start].value == pytest.approx(0.10)  # (0.12−0.02 + 0.09+0.01) / 2


def test_expected_error_combines_mae_and_spread():
    assert expected_error(0.03, 0.08) == pytest.approx((0.03**2 + 0.04**2) ** 0.5)
    assert expected_error(None, 0.08) == pytest.approx(0.04)
    assert expected_error(None, 0.0) is None
    assert expected_error(0.02, 0.0) == pytest.approx(0.02)
