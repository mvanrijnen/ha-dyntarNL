"""Tariefperiodes, inkoop- en verkoopprijs per slot, contracttypes en de tabellen.

Rond de jaarwisseling en de klokwissels met de echte Europe/Amsterdam-tijdzone.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from dyntarnl.const import CONTRACT_DYNAMIC, CONTRACT_FIXED, ELECTRICITY, GAS, RATE_DOUBLE, RATE_INPUT_BARE
from dyntarnl.forecast.model import QuarterRecord
from dyntarnl.issues import period_issues
from dyntarnl.model import EnergyData, Slot
from dyntarnl.prices import Tariff, apply_formula
from dyntarnl.tables import FIELDS, build_rows, next_negative_sell, row_at, table
from dyntarnl.tariffs import (
    Period,
    PriceResolver,
    apply_periods,
    contract_slot,
    initial_periods,
    is_low,
    missing_fields,
    nl_holidays,
    period_at,
    periods_from_options,
    sell_slot,
    sell_slots,
)

AMS = ZoneInfo("Europe/Amsterdam")
UTC = timezone.utc

P2026 = Period(start=date(2026, 1, 1), netting=True, vat=21.0, tax_elec_ex=0.09161, tax_gas_ex=0.60066,
               markup_elec_ex=0.02, markup_gas_ex=0.05)
P2027 = Period(start=date(2027, 1, 1), netting=False, vat=21.0, tax_elec_ex=0.08802, tax_gas_ex=0.61628,
               markup_elec_ex=0.02, markup_gas_ex=0.05, export_markup_ex=0.0, export_costs_ex=0.01)
PERIODS = (P2026, P2027)


@pytest.fixture
def real_tz(monkeypatch):
    dt = sys.modules["homeassistant.util.dt"]
    monkeypatch.setattr(dt, "DEFAULT_TIME_ZONE", AMS)
    monkeypatch.setattr(dt, "as_local", lambda d: d.astimezone(AMS))
    monkeypatch.setattr(
        dt, "start_of_local_day",
        lambda d=None: datetime.combine(d.date() if isinstance(d, datetime) else d, time(0), AMS),
    )
    return dt


def local(*args) -> datetime:
    return datetime(*args, tzinfo=AMS)


def slot(start: datetime, market_ex: float, minutes: int = 60, tariff: Tariff | None = None) -> Slot:
    end = (start.astimezone(UTC) + timedelta(minutes=minutes)).astimezone(AMS)
    return apply_formula(start, end, market_ex, tariff or Tariff(fee_ex=0.02, tax_ex=0.09161, vat=21.0))


def hours(first: datetime, prices: list[float]) -> list[Slot]:
    """Opeenvolgende uurslots in echte tijd (DST-veilig)."""
    out = []
    t = first.astimezone(UTC)
    for p in prices:
        out.append(slot(t.astimezone(AMS), p))
        t += timedelta(hours=1)
    return out


# --- periode per tijdslot ---------------------------------------------------------------


def test_year_boundary_quarter_gets_its_own_period(real_tz):
    assert period_at(PERIODS, local(2026, 12, 31, 23, 45)) is P2026
    assert period_at(PERIODS, local(2027, 1, 1, 0, 0)) is P2027
    # 1-1-2027 00:00 lokaal = 31-12 23:00 UTC: de keuze gaat op lokale tijd, niet UTC.
    assert period_at(PERIODS, datetime(2026, 12, 31, 23, 0, tzinfo=UTC)) is P2027
    assert period_at(PERIODS, datetime(2026, 12, 31, 22, 45, tzinfo=UTC)) is P2026


def test_before_first_period_is_none_not_a_guess(real_tz):
    assert period_at(PERIODS, local(2025, 12, 31, 12)) is None


def test_future_period_does_not_touch_prices_before_its_start(real_tz):
    """API-leverancier, dynamisch: 2026-slots blijven exact (zelfde object) gelijk."""
    slots = hours(local(2026, 12, 31, 0), [0.10] * 24)
    assert all(a is b for a, b in zip(apply_periods(slots, PERIODS, ELECTRICITY, custom=False), slots))


# --- inkoop -------------------------------------------------------------------------------


def test_purchase_2027_worked_example(real_tz):
    """kaal 0,10 + opslag 0,02 → (0,10 + 0,02) × 1,21 + 0,1065 = 0,2517."""
    s = slot(local(2027, 1, 1, 12), 0.10)
    buy = apply_periods([s], PERIODS, ELECTRICITY, custom=True)[0]
    assert buy.total == pytest.approx(0.2517, abs=1e-4)
    assert buy.tax == pytest.approx(0.1065, abs=1e-4)


def test_custom_2026_is_bit_for_bit_the_old_formula(real_tz):
    s = slot(local(2026, 8, 17, 14), 0.1234)
    buy = apply_periods([s], PERIODS, ELECTRICITY, custom=True)[0]
    assert buy == apply_formula(s.start, s.end, 0.1234, Tariff(fee_ex=0.02, tax_ex=0.09161, vat=21.0))


def test_missing_value_gives_no_price_never_zero(real_tz):
    incomplete = (P2026, Period(start=date(2027, 1, 1), netting=False, vat=21.0, tax_elec_ex=0.08802,
                                provisional=True))
    s = slot(local(2027, 1, 2, 12), 0.10)
    assert apply_periods([s], incomplete, ELECTRICITY, custom=True) == []  # opslag ontbreekt
    assert sell_slot(incomplete[1], s, s.start, s.end, 0.10, 0.121) is None  # terugleverkosten ontbreken
    assert set(missing_fields(incomplete[1], custom=True)) >= {"markup_elec_ex", "export_costs_ex"}


# --- verkoop ------------------------------------------------------------------------------


def test_sell_equals_buy_per_slot_while_netting(real_tz):
    slots = hours(local(2026, 12, 31, 0), [0.10, -0.05, 0.30])
    for buy, sold in zip(slots, sell_slots(slots, PERIODS)):
        assert sold.total == buy.total and sold.start == buy.start and sold.end == buy.end


def test_sell_2027_is_market_plus_markup_minus_costs_no_tax_no_vat(real_tz):
    s = slot(local(2027, 1, 1, 12), 0.10)
    sold = sell_slot(P2027, s, s.start, s.end, 0.10, s.market)
    assert sold.total == pytest.approx(0.09)
    assert sold.tax == 0 and sold.vat == 0 and sold.costs_ex == 0.01


def test_negative_market_2027_gives_negative_sell_and_binary_on(real_tz, at_time):
    first = local(2027, 1, 1, 0)
    at_time(local(2027, 1, 1, 10, 30))
    slots = hours(first, [0.10] * 10 + [-0.05, -0.02] + [0.10] * 12)
    ed = EnergyData("kWh", 21.0, today=slots)
    rows, _ = build_rows(ed, ELECTRICITY, PERIODS, "essent", None, local(2027, 1, 1, 10, 30))
    now = row_at(rows, local(2027, 1, 1, 10, 30))
    assert now.sell.total == pytest.approx(-0.06)
    assert now.buy.total > 0  # inkoop blijft positief door belasting
    start, end = next_negative_sell(rows, local(2027, 1, 1, 9))
    assert (start, end) == (local(2027, 1, 1, 10).astimezone(UTC), local(2027, 1, 1, 12).astimezone(UTC))


def test_negative_market_2026_sell_follows_buy(real_tz):
    s = slot(local(2026, 6, 1, 13), -0.05)
    sold = sell_slot(P2026, s, s.start, s.end, -0.05, s.market)
    assert sold.total == s.total  # saldering: zelfde als inkoop


# --- forecast over de jaargrens ------------------------------------------------------------


def test_forecast_across_new_year_uses_old_rules_before_and_new_after(real_tz):
    ref = Tariff(fee_ex=0.02, tax_ex=0.09161, vat=21.0)
    resolver = PriceResolver(PERIODS, ref, local(2026, 12, 31, 12), custom=True)
    q = timedelta(minutes=15)
    before = resolver.buy(local(2026, 12, 31, 23, 45), local(2026, 12, 31, 23, 45) + q, 0.10)
    after = resolver.buy(local(2027, 1, 1, 0, 0), local(2027, 1, 1, 0, 0) + q, 0.10)
    assert before.total == apply_formula(before.start, before.end, 0.10, ref).total
    assert after.tax_ex == 0.08802
    assert resolver.sell(before, before.start, before.end, 0.10).total == before.total  # saldering
    assert resolver.sell(after, after.start, after.end, 0.10).total == pytest.approx(0.09)  # 2027


def test_api_supplier_forecast_in_new_period_keeps_supplier_markup(real_tz):
    ref = Tariff(fee_ex=0.0123, tax_ex=0.09161, vat=21.0)
    no_markup = (P2026, Period(start=date(2027, 1, 1), netting=False, vat=21.0, tax_elec_ex=0.08802))
    resolver = PriceResolver(no_markup, ref, local(2026, 12, 31, 12), custom=False)
    t = resolver.tariff_for(local(2027, 1, 2, 12))
    assert t.fee_ex == 0.0123 and t.tax_ex == 0.08802


# --- tabellen -------------------------------------------------------------------------------


def test_buy_and_sell_tables_share_slots_also_on_dst_day(real_tz, at_time):
    """25-uursdag (oktober): beide tabellen hebben exact dezelfde rijen."""
    at_time(local(2026, 10, 25, 12))
    slots = hours(local(2026, 10, 25, 0), [0.10] * 25)
    assert slots[-1].end == local(2026, 10, 26, 0)
    rows, step = build_rows(EnergyData("kWh", 21.0, today=slots), ELECTRICITY, PERIODS, "essent", None,
                            local(2026, 10, 25, 12))
    buy = table(rows, step, "buy", "kWh", "Essent")
    sell = table(rows, step, "sell", "kWh", "Essent")
    assert len(buy["rows"]) == len(sell["rows"]) == 25
    assert buy["start"] == sell["start"] and buy["resolution_min"] == sell["resolution_min"] == 60
    assert buy["fields"] == sell["fields"] == list(FIELDS)
    assert [r[0] for r in buy["rows"]] == [r[0] for r in sell["rows"]]  # 2026: verkoop == inkoop
    assert all(r[FIELDS.index("spread")] == 0 for r in buy["rows"])


def test_table_row_sums_up(real_tz, at_time):
    """price = energy_ex − costs_ex + tax_ex + vat, voor inkoop én verkoop."""
    at_time(local(2027, 1, 1, 12))
    slots = apply_periods(hours(local(2027, 1, 1, 0), [0.10] * 24), PERIODS, ELECTRICITY, custom=True)
    rows, step = build_rows(EnergyData("kWh", 21.0, today=slots), ELECTRICITY, PERIODS, "custom", None,
                            local(2027, 1, 1, 12))
    i = {f: n for n, f in enumerate(FIELDS)}
    for side in ("buy", "sell"):
        for r in table(rows, step, side, "kWh", "x")["rows"]:
            assert r[i["price"]] == pytest.approx(r[i["energy_ex"]] - r[i["costs_ex"]] + r[i["tax_ex"]] + r[i["vat"]], abs=2e-5)
        # spread = inkoop − verkoop
    first = table(rows, step, "buy", "kWh", "x")["rows"][0]
    assert first[i["spread"]] == pytest.approx(0.2517 - 0.09, abs=1e-4)


def test_published_price_replaces_forecast_in_both_tables(real_tz, at_time):
    at_time(local(2026, 8, 17, 12))
    published = hours(local(2026, 8, 17, 0), [0.10] * 24)
    resolver = PriceResolver(PERIODS, Tariff(0.02, 0.09161, 21.0), local(2026, 8, 17, 23), custom=False)
    records = []
    t = local(2026, 8, 17, 22).astimezone(UTC)  # overlapt het laatste gepubliceerde uur + verder
    for _ in range(16):
        buy = resolver.buy(t, t + timedelta(minutes=15), 0.50)
        records.append(QuarterRecord(start=t, end=t + timedelta(minutes=15), price_raw=0.50,
                                     price_allin=buy.total, source="forecast", providers=("ensemble",),
                                     buy=buy, sell=resolver.sell(buy, t, t + timedelta(minutes=15), 0.50)))
        t += timedelta(minutes=15)
    rows, step = build_rows(EnergyData("kWh", 21.0, today=published), ELECTRICITY, PERIODS, "essent",
                            records, local(2026, 8, 17, 12))
    assert step == timedelta(minutes=15)
    at_22 = row_at(rows, local(2026, 8, 17, 22, 15))
    assert at_22.src == "p:essent" and at_22.buy.market_ex == 0.10 and at_22.sell.total == at_22.buy.total
    at_00 = row_at(rows, local(2026, 8, 18, 0, 15))
    assert at_00.src == "f:ensemble" and at_00.buy.market_ex == 0.50 and at_00.sell.total == at_00.buy.total


# --- vast / variabel --------------------------------------------------------------------------


FIXED = Period(start=date(2027, 1, 1), contract=CONTRACT_FIXED, netting=False, vat=21.0, tax_elec_ex=0.08802,
               rate_mode=RATE_DOUBLE, rate_elec=0.30, rate_elec_low=0.25,
               export_rate_ex=0.07, export_costs_ex=0.015)


def test_switch_dynamic_to_fixed_on_start_date_incl_forecast(real_tz, at_time):
    periods = (P2026, FIXED)
    at_time(local(2026, 12, 31, 14))
    published = hours(local(2026, 12, 31, 0), [0.10] * 24)
    rows, _ = build_rows(EnergyData("kWh", 21.0, today=published), ELECTRICITY, periods, "essent", None,
                         local(2026, 12, 31, 14))
    last_2026 = row_at(rows, local(2026, 12, 31, 23, 30))
    first_2027 = row_at(rows, local(2027, 1, 1, 0, 30))  # synthetisch: contract, zonder publicatie
    assert last_2026.src == "p:essent"
    assert first_2027.src == "c" and first_2027.buy.total == 0.25  # nacht = dal
    resolver = PriceResolver(periods, Tariff(0.02, 0.09161, 21.0), local(2026, 12, 31, 23), custom=False)
    assert resolver.buy(local(2027, 1, 4, 12), local(2027, 1, 4, 12, 15), 0.99).total == 0.30  # beurs telt niet
    assert resolver.error_factor(local(2027, 1, 4, 12)) == 0.0  # vaste prijs: geen voorspelfout


def test_double_rate_boundaries_weekend_and_dst(real_tz):
    p = FIXED
    assert is_low(p, local(2027, 1, 4, 6, 45)) and not is_low(p, local(2027, 1, 4, 7, 0))  # maandag
    assert not is_low(p, local(2027, 1, 4, 22, 45)) and is_low(p, local(2027, 1, 4, 23, 0))
    assert is_low(p, local(2027, 1, 9, 12, 0))  # zaterdag
    # Maandag na de zomertijd-wissel (29-3-2027): 06:45 lokaal dal, 07:00 normaal.
    assert is_low(p, local(2027, 3, 29, 6, 45)) and not is_low(p, local(2027, 3, 29, 7, 0))
    # Maandag na de wintertijd-wissel (25-10-2027).
    assert is_low(p, local(2027, 10, 25, 6, 59)) and not is_low(p, local(2027, 10, 25, 7, 0))
    # Feestdagen alleen als dat aan staat (Tweede Paasdag 2027 = 29 maart).
    assert date(2027, 3, 29) in nl_holidays(2027)
    assert not is_low(p, local(2027, 3, 29, 12))
    assert is_low(_with(p, low_holidays=True), local(2027, 3, 29, 12))


def _with(period: Period, **changes) -> Period:
    from dataclasses import replace

    return replace(period, **changes)


def test_bare_and_all_in_rate_give_the_same_price(real_tz):
    bare = _with(FIXED, rate_mode="single", rate_input=RATE_INPUT_BARE, rate_elec=0.12)
    all_in = _with(FIXED, rate_mode="single", rate_elec=round((0.12 + 0.08802) * 1.21, 6))
    t = local(2027, 2, 1, 12)
    a = contract_slot(bare, ELECTRICITY, t, t + timedelta(hours=1), None)
    b = contract_slot(all_in, ELECTRICITY, t, t + timedelta(hours=1), None)
    assert a.total == pytest.approx(b.total, abs=1e-6)
    assert a.energy_ex == pytest.approx(b.energy_ex, abs=1e-6)


def test_fixed_contract_sell_2026_equals_buy_2027_is_rate_minus_costs(real_tz):
    fixed_2026 = _with(FIXED, start=date(2026, 1, 1), netting=True, tax_elec_ex=0.09161)
    t = local(2026, 6, 1, 12)
    buy = contract_slot(fixed_2026, ELECTRICITY, t, t + timedelta(hours=1), 0.10)
    assert sell_slot(fixed_2026, buy, t, buy.end, 0.10, None).total == buy.total
    t = local(2027, 6, 1, 12)  # dinsdag 12:00 = normaal
    buy = contract_slot(FIXED, ELECTRICITY, t, t + timedelta(hours=1), 0.10)
    assert sell_slot(FIXED, buy, t, buy.end, 0.10, None).total == pytest.approx(0.07 - 0.015)


def test_fixed_gas_uses_gas_rate(real_tz):
    gas = _with(FIXED, rate_gas=1.40, tax_gas_ex=0.61628)
    t = local(2027, 2, 1, 6)
    assert contract_slot(gas, GAS, t, t + timedelta(hours=1), 0.30).total == 1.40


def test_export_minimum_warning_only_2027_to_2029(real_tz):
    low = _with(FIXED, rate_mode="single", rate_input=RATE_INPUT_BARE, rate_elec=0.12, export_rate_ex=0.05)
    now = local(2026, 12, 20, 12)
    assert "export_below_minimum_2027-01-01" in period_issues((P2026, low), False, now)
    ok = _with(low, export_rate_ex=0.06)  # precies 50%
    assert "export_below_minimum_2027-01-01" not in period_issues((P2026, ok), False, now)
    later = _with(low, start=date(2030, 1, 1))
    assert not [k for k in period_issues((P2026, later), False, now) if k.startswith("export_below")]


def test_provisional_period_within_14_days_gives_a_repair(real_tz):
    provisional = _with(P2027, provisional=True)
    assert "period_incomplete_2027-01-01" in period_issues((P2026, provisional), True, local(2026, 12, 20))
    assert "period_incomplete_2027-01-01" not in period_issues((P2026, provisional), True, local(2026, 12, 1))
    assert not period_issues((P2026, P2027), True, local(2026, 12, 20))  # definitief en compleet


# --- opslag en migratie ------------------------------------------------------------------------


def test_initial_periods_current_plus_provisional_2027():
    data = {"supplier": "custom", "vat_percentage": 21, "markup_electricity": 0.02, "markup_gas": 0.05,
            "energy_tax_electricity": 0.09161, "energy_tax_gas": 0.60066}
    current, future = periods_from_options({"tariff_periods": initial_periods(data, True, date(2026, 11, 18))})
    assert current.start == date(2026, 1, 1) and current.netting and current.markup_elec_ex == 0.02
    assert future.start == date(2027, 1, 1) and future.provisional and not future.netting
    assert future.tax_elec_ex == 0.08802 and future.markup_elec_ex is None and future.export_costs_ex is None
    api = periods_from_options({"tariff_periods": initial_periods({"supplier": "essent"}, False, date(2026, 11, 18))})
    assert api[0].markup_elec_ex is None  # API-leverancier: opslag komt van de leverancier


def test_period_roundtrip_through_options():
    again = periods_from_options({"tariff_periods": [FIXED.to_dict(), P2026.to_dict()]})
    assert again == (P2026, FIXED)
    assert periods_from_options({"tariff_periods": [{"start": "x"}, P2026.to_dict()]}) == (P2026,)
