"""Options flow: tariefperiodes toevoegen, wijzigen, verwijderen en valideren."""

from __future__ import annotations

import asyncio

from dyntarnl.config_flow import DynTarNLOptionsFlow
from dyntarnl.const import CONF_PERIODS, FORECAST_DEFAULTS
from dyntarnl.tariffs import initial_periods, periods_from_options

from fc_hass import Entry

TODAY = "2026-08-17"  # = DEFAULT_NOW


def _flow(supplier="custom"):
    data = {"supplier": supplier, "vat_percentage": 21, "markup_electricity": 0.02, "markup_gas": 0.05,
            "energy_tax_electricity": 0.09161, "energy_tax_gas": 0.60066}
    from datetime import date

    options = {**FORECAST_DEFAULTS, CONF_PERIODS: initial_periods(data, supplier == "custom", date(2026, 8, 17))}
    flow = DynTarNLOptionsFlow()
    flow.config_entry = Entry(data=data, options=options)
    flow.hass = None
    return flow


def run(coro):
    return asyncio.run(coro)


def general(**kw):
    base = {"start": "2027-07-01", "contract": "dynamic", "netting": False, "vat": 21.0,
            "tax_elec_ex": 0.08802, "tax_gas_ex": 0.61628, "provisional": False, "note": ""}
    return {**base, **kw}


def test_init_is_a_menu_with_forecast_and_tariffs():
    result = run(_flow().async_step_init())
    assert result["type"] == "menu" and result["menu_options"] == ["forecast_switch", "tariffs"]
    assert run(_flow().async_step_tariffs())["menu_options"] == ["period_add", "period_edit", "period_delete"]


def test_add_dynamic_period_with_sections_saves_sorted():
    flow = _flow()
    run(flow.async_step_period_add())
    result = run(flow.async_step_period_general(general()))
    assert result["step_id"] == "period_details_dynamic"
    result = run(flow.async_step_period_details_dynamic(
        {"buy": {"markup_elec_ex": 0.021, "markup_gas_ex": 0.05},
         "sell": {"export_markup_ex": -0.005, "export_costs_ex": 0.012}}
    ))
    assert result["type"] == "create_entry"
    periods = periods_from_options(result["data"])
    assert [p.start.isoformat() for p in periods] == ["2026-01-01", "2027-01-01", "2027-07-01"]
    new = periods[-1]
    assert new.export_markup_ex == -0.005 and new.export_costs_ex == 0.012 and new.export_costs_year is None
    assert result["data"]["forecast_enabled"] is False  # overige opties blijven


def test_duplicate_start_and_gap_before_first_are_refused():
    flow = _flow()
    run(flow.async_step_period_add())
    assert run(flow.async_step_period_general(general(start="2027-01-01")))["errors"] == {"start": "duplicate_start"}
    flow = _flow()
    run(flow.async_step_period_edit({"start": "2026-01-01"}))
    result = run(flow.async_step_period_general(general(start="2026-09-01")))  # eerste periode na vandaag
    assert result["errors"] == {"start": "gap_before_first"}


def test_missing_required_values_are_refused_unless_provisional():
    flow = _flow()
    run(flow.async_step_period_add())
    run(flow.async_step_period_general(general()))
    result = run(flow.async_step_period_details_dynamic({"buy": {"markup_elec_ex": 0.02}, "sell": {}}))
    assert result["errors"] == {"base": "missing_fields"}
    assert "export_costs_ex" in result["description_placeholders"]["missing"]
    flow = _flow()
    run(flow.async_step_period_add())
    run(flow.async_step_period_general(general(provisional=True)))
    assert run(flow.async_step_period_details_dynamic({"buy": {}, "sell": {}}))["type"] == "create_entry"


def test_fixed_period_form_and_values():
    flow = _flow("essent")
    run(flow.async_step_period_add())
    result = run(flow.async_step_period_general(general(contract="fixed")))
    assert result["step_id"] == "period_details_fixed"
    result = run(flow.async_step_period_details_fixed(
        {"buy": {"rate_mode": "double", "rate_input": "all_in", "rate_elec": 0.30, "rate_elec_low": 0.25,
                 "low_start": "23:00:00", "low_end": "07:00:00", "low_weekend": True, "low_holidays": True},
         "sell": {"export_rate_ex": 0.07, "export_costs_ex": 0.015}}
    ))
    assert result["type"] == "create_entry"
    fixed = periods_from_options(result["data"])[-1]
    assert fixed.contract == "fixed" and fixed.rate_elec_low == 0.25 and fixed.low_holidays
    assert fixed.markup_elec_ex is None  # geen verborgen waarden van het andere contracttype


def test_edit_clears_emptied_field_and_delete_keeps_a_period():
    flow = _flow()
    run(flow.async_step_period_edit({"start": "2026-01-01"}))
    run(flow.async_step_period_general(general(start="2026-01-01", netting=True, tax_elec_ex=0.09161)))
    result = run(flow.async_step_period_details_dynamic({"buy": {"markup_elec_ex": 0.03, "markup_gas_ex": 0.05}, "sell": {}}))
    edited = periods_from_options(result["data"])[0]
    assert edited.markup_elec_ex == 0.03 and edited.export_markup_ex is None
    flow = _flow()
    result = run(flow.async_step_period_delete({"start": "2026-01-01"}))
    assert result["errors"] == {"base": "gap_before_first"}  # dan begint de eerste periode pas in 2027
    result = run(_flow().async_step_period_delete({"start": "2027-01-01"}))
    assert [p.start.isoformat() for p in periods_from_options(result["data"])] == ["2026-01-01"]
