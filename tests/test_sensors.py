"""Tests voor de sensor-metrieken: component-sensoren en teruglever-tellingen."""

from datetime import datetime, timedelta, timezone

from dyntarnl.model import EnergyData, Slot
from dyntarnl.sensor import (
    _COMPONENT_SENSORS,
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
