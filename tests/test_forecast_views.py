"""Compacte sensoren en de get_prices-respons."""

from datetime import timedelta

import pytest

from dyntarnl.const import CONF_FC_PROVIDERS, ELECTRICITY, GAS
from dyntarnl.forecast.engine import build
from dyntarnl.forecast.views import cheapest_block, next_24h, service_response, tomorrow
from dyntarnl.model import EnergyData

from fc_util import DAY_START, NOW, cfg, hour_slots, prices_data, series

A, B = "epexpredictor", "energypriceforecast"
TOMORROW = DAY_START + timedelta(days=1)


def _forecast(values_a, values_b=None, prices=None):
    s = {A: series(A, NOW, TOMORROW, values_a)}
    if values_b is not None:
        s[B] = series(B, NOW, TOMORROW, values_b)
    c = cfg() if values_b is not None else cfg(**{CONF_FC_PROVIDERS: [A]})
    data, _ = build(prices or prices_data(), s, None, c, "Essent", NOW)
    return data, c


def test_cheapest_block_can_span_published_and_forecast():
    # vandaag duur, laatste gepubliceerde uur (21:00 UTC) goedkoop, morgen begint goedkoop
    today = [0.30] * 23 + [0.01]
    data, _ = _forecast([0.02] * 8 + [0.40] * 88, prices=prices_data(today))
    block = cheapest_block(data.merged, NOW, 3, 48)
    assert block["start"] == TOMORROW - timedelta(hours=1)
    assert block["source"] == "mixed"
    assert block["hours"] == 3


def test_cheapest_block_needs_contiguous_quarters():
    """Goedkope uren aan weerszijden van een gat mogen geen blok vormen."""
    c = cfg()
    s = {
        A: series(A, NOW, TOMORROW, [0.01] * 4),                       # morgen 00:00–01:00
        B: series(B, NOW, TOMORROW + timedelta(hours=2), [0.01] * 8),  # 02:00–04:00
    }
    data, _ = build(prices_data([0.30] * 24), s, None, c, "Essent", NOW)
    block = cheapest_block(data.merged, NOW, 3, 48)
    assert block["end"] == TOMORROW + timedelta(hours=1)  # eindigt vóór het gat
    assert block["source"] == "mixed"


def test_tomorrow_average_prefers_published():
    data, _ = _forecast([0.50] * 96, prices=prices_data(tomorrow=[0.10] * 24))
    info = tomorrow(data.merged, NOW)
    assert info["source"] == "published" and info["coverage_hours"] == 24


def test_tomorrow_average_from_forecast_has_error():
    data, _ = _forecast([0.10] * 96, [0.14] * 96)
    info = tomorrow(data.merged, NOW)
    assert info["source"] == "forecast"
    assert info["expected_error_allin"] == pytest.approx(0.02 * 1.21)


def test_next_24h_mixes_sources():
    data, _ = _forecast([0.10] * 96)
    info = next_24h(data.merged, NOW)
    assert info["source"] == "mixed" and info["coverage_hours"] == 24


def test_service_without_forecast_returns_published_only():
    out = service_response(prices_data(), None, None, "Essent", ELECTRICITY, True, "ensemble", None, NOW)
    assert out["forecast_enabled"] is False
    assert len(out["records"]) == 96
    assert {r["source"] for r in out["records"]} == {"published"}
    assert out["records"][0]["start"].endswith("+02:00")  # lokale tijd in de output


def test_service_with_forecast_and_horizon():
    data, c = _forecast([0.10] * 96, [0.14] * 96)
    out = service_response(prices_data(), data, c, "Essent", ELECTRICITY, True, "ensemble", 24, NOW)
    recs = out["records"]
    assert recs[-1]["source"] == "forecast" and recs[-1]["n_sources"] == 2
    assert len([r for r in recs if r["source"] == "forecast"]) == 4 * 14  # 22:00 UTC tot NOW+24h


def test_service_single_provider_and_all():
    data, c = _forecast([0.10] * 96, [0.14] * 96)
    one = service_response(prices_data(), data, c, "Essent", ELECTRICITY, True, B, None, NOW)
    assert {tuple(r["providers"]) for r in one["records"] if r["source"] == "forecast"} == {(B,)}
    every = service_response(prices_data(), data, c, "Essent", ELECTRICITY, True, "all", None, NOW)
    assert set(every["providers"]) == {A, B}


def test_service_unknown_provider_raises():
    data, c = _forecast([0.10] * 4)
    with pytest.raises(ValueError):
        service_response(prices_data(), data, c, "Essent", ELECTRICITY, True, "nope", None, NOW)


def test_service_gas_is_published_only_on_native_resolution():
    gas = EnergyData("m³", 21.0, today=hour_slots(DAY_START + timedelta(hours=6), [0.35]))
    prices = {**prices_data(), GAS: gas}
    data, c = _forecast([0.10] * 4)
    out = service_response(prices, data, c, "Essent", GAS, True, "ensemble", None, NOW)
    assert len(out["records"]) == 1 and out["unit"] == "m³"


def test_forecast_chart_is_hourly_like_the_columns():
    """Kwartierpunten zouden de uurkolommen in ApexCharts 4x zo smal maken."""
    from dyntarnl.forecast.views import chart_step, forecast_chart

    data, _ = _forecast([0.08, 0.10, 0.12, 0.14, 0.20, 0.20, 0.20, 0.20], [0.10] * 8)
    step = chart_step(data.merged)
    assert step == timedelta(hours=1)  # leverancier publiceert per uur
    allin, market, band = forecast_chart(data.merged, data.tariff, step)
    assert len(allin) == len(market) == len(band) == 2
    assert allin[1][0] - allin[0][0] == 3_600_000
    # eerste uur: ensemble per kwartier (0.09, 0.10, 0.11, 0.12) → gemiddeld 0.105 kaal
    assert allin[0][0] == int(TOMORROW.timestamp() * 1000)
    assert allin[0][1] == pytest.approx((0.105 + 0.02 + 0.09161) * 1.21, abs=1e-5)
    assert market[0][1] == pytest.approx(0.105 * 1.21, abs=1e-5)  # beurs incl. btw
    low, high = band[0][1], band[0][2]
    assert low < allin[0][1] < high


def test_forecast_chart_only_forecast_and_needs_tariff():
    from dyntarnl.forecast.views import forecast_chart

    data, _ = _forecast([0.10] * 8)
    allin, _, _ = forecast_chart(data.merged, data.tariff)
    published_end = max(r.end for r in data.merged if r.source == "published")
    assert all(ms >= published_end.timestamp() * 1000 for ms, _ in allin)
    assert forecast_chart(data.merged, None) == ([], [], [])
