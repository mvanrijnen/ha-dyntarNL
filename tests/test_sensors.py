"""Tests voor de sensor-metrieken: component-sensoren en teruglever-tellingen."""

from datetime import datetime, timedelta, timezone

from dyntarnl.model import EnergyData, Slot
from dyntarnl.sensor import (
    _COMPONENT_SENSORS,
    _attributes,
    _feedin_cost_hours_today,
    _feedin_cost_now,
    _negative_hours_today,
    _today_avg,
)

AMS = timezone(timedelta(hours=2))
NOW = datetime(2026, 8, 17, 12, tzinfo=AMS)


def _slot(hour: int, market: float, fee: float = 0.025) -> Slot:
    start = datetime(2026, 8, 17, hour, tzinfo=AMS)
    return Slot(
        start=start,
        end=start + timedelta(hours=1),
        total=market + fee + 0.11,
        market=market,
        market_ex=market / 1.21,
        fee=fee,
        fee_ex=fee / 1.21,
        tax=0.11,
        tax_ex=0.09,
    )


def _energy(slots):
    return EnergyData(unit="kWh", vat_percentage=21.0, today=slots)


def test_component_descriptions_map_to_fields():
    by_key = {d.key: d for d in _COMPONENT_SENSORS}
    assert set(by_key) == {
        "energy_tax_incl_vat",
        "energy_tax_excl_vat",
        "purchase_fee_incl_vat",
        "purchase_fee_excl_vat",
    }
    s = _slot(12, 0.2)
    assert by_key["energy_tax_incl_vat"].value_fn(s) == s.tax
    assert by_key["energy_tax_excl_vat"].value_fn(s) == s.tax_ex
    assert by_key["purchase_fee_incl_vat"].value_fn(s) == s.fee
    assert by_key["purchase_fee_excl_vat"].value_fn(s) == s.fee_ex


def test_negative_hours_vs_feedin_cost_hours():
    # 2 uur echt negatief, 2 uur positief maar onder de opslag (0,025)
    slots = (
        [_slot(h, -0.05) for h in (0, 1)]
        + [_slot(h, 0.01) for h in (2, 3)]
        + [_slot(h, 0.2) for h in range(4, 24)]
    )
    ed = _energy(slots)
    # 'prijs negatief' telt alleen markt < 0
    assert _negative_hours_today(ed, NOW) == 2.0
    # 'terugleveren kost geld' telt markt < opslag -> ook de 2 uren van 0,01
    assert _feedin_cost_hours_today(ed, NOW) == 4.0


def test_feedin_cost_now_positive_when_below_markup():
    ed = _energy([_slot(12, 0.01)])  # markt 0,01 < opslag 0,025 -> kost geld
    assert _feedin_cost_now(ed, NOW) > 0


def test_today_avg():
    ed = _energy([_slot(h, 0.10) for h in range(24)])
    assert abs(_today_avg(ed, NOW, lambda s: s.market) - 0.10) < 1e-9


def _slot_on(day: int, hour: int, market: float) -> Slot:
    start = datetime(2026, 8, day, hour, tzinfo=AMS)
    return Slot(
        start=start,
        end=start + timedelta(hours=1),
        total=market + 0.135,
        market=market,
        market_ex=market / 1.21,
    )


def test_attributes_expose_all_cached_days():
    """De now-sensor draagt gisteren t/m morgen mee, zodat een grafiek alle
    uren kan tekenen die de coordinator in cache heeft."""
    ed = EnergyData(
        unit="kWh",
        vat_percentage=21.0,
        yesterday=[_slot_on(16, h, 0.20) for h in range(24)],
        today=[_slot_on(17, h, 0.30) for h in range(24)],
        tomorrow=[_slot_on(18, h, 0.40) for h in range(24)],
    )
    attrs = _attributes(ed, NOW, lambda s: s.total)

    assert [len(attrs[k]) for k in ("yesterday", "today", "tomorrow")] == [24, 24, 24]
    combined = attrs["yesterday"] + attrs["today"] + attrs["tomorrow"]
    starts = [e["start"] for e in combined]
    assert starts == sorted(starts)  # aaneengesloten, chronologisch
    assert attrs["yesterday"][0]["price"] == round(0.20 + 0.135, 5)


def test_attributes_omit_missing_days():
    ed = EnergyData(unit="kWh", vat_percentage=21.0, today=[_slot_on(17, 12, 0.30)])
    attrs = _attributes(ed, NOW, lambda s: s.total)
    assert attrs["yesterday"] is None and attrs["tomorrow"] is None


def test_prices_array_is_chart_ready():
    """`prices` is één platte [epoch-ms, prijs]-reeks over alle gecachete dagen."""
    ed = EnergyData(
        unit="kWh",
        vat_percentage=21.0,
        yesterday=[_slot_on(16, h, 0.20) for h in range(24)],
        today=[_slot_on(17, h, 0.30) for h in range(24)],
        tomorrow=[_slot_on(18, h, 0.40) for h in range(24)],
    )
    prices = _attributes(ed, NOW, lambda s: s.total)["prices"]

    assert len(prices) == 72
    assert all(isinstance(ts, int) and isinstance(v, float) for ts, v in prices)
    stamps = [ts for ts, _ in prices]
    assert stamps == sorted(stamps)
    # aaneengesloten uren, geen gaten
    assert {b - a for a, b in zip(stamps, stamps[1:])} == {3_600_000}
    # eerste punt is gisteren 00:00 lokaal en hoort bij de eerste yesterday-slot
    assert stamps[0] == int(datetime(2026, 8, 16, 0, tzinfo=AMS).timestamp() * 1000)


def test_prices_array_matches_day_arrays():
    ed = EnergyData(
        unit="kWh",
        vat_percentage=21.0,
        today=[_slot_on(17, h, 0.30) for h in range(24)],
        tomorrow=[_slot_on(18, h, 0.40) for h in range(24)],
    )
    attrs = _attributes(ed, NOW, lambda s: s.total)
    from_days = [e["price"] for e in attrs["today"] + attrs["tomorrow"]]
    assert [v for _, v in attrs["prices"]] == from_days
