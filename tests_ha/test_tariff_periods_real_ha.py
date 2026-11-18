"""Tariefperiodes en de inkoop-/verkooptabel in een echte Home Assistant."""

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from test_custom_real_ha import DOMAIN, _add_custom, prices  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def _state(hass, entity_id):
    state = hass.states.get(entity_id)
    assert state is not None, sorted(hass.states.async_entity_ids("sensor"))
    return state


async def test_new_entities_and_tables(hass, prices):
    await _add_custom(hass)
    allin = float(_state(hass, "sensor.dyntarnl_e_all_in_now").state)
    # 2026: saldering → verkoop == inkoop, spread 0.
    assert float(_state(hass, "sensor.dyntarnl_e_sell_now").state) == pytest.approx(allin)
    assert float(_state(hass, "sensor.dyntarnl_e_spread_now").state) == 0
    assert _state(hass, "binary_sensor.dyntarnl_e_sell_negative_now").state == "off"
    buy = _state(hass, "sensor.dyntarnl_e_buy_table")
    sell = _state(hass, "sensor.dyntarnl_e_sell_table")
    assert float(buy.state) == pytest.approx(allin)
    assert buy.attributes["fields"] == sell.attributes["fields"]
    assert len(buy.attributes["rows"]) == len(sell.attributes["rows"]) >= 24
    assert buy.attributes["start"] == sell.attributes["start"]
    assert _state(hass, "sensor.dyntarnl_g_buy_table").attributes["unit"] == "€/m³"
    period = _state(hass, "sensor.dyntarnl_tariff_period")
    assert period.state == "2026-01-01" and period.attributes["next_start"] == "2027-01-01"
    assert period.attributes["provisional_planned"] is True

    resp = await hass.services.async_call(
        DOMAIN, "get_prices", {"format": "table"}, blocking=True, return_response=True
    )
    assert resp["buy"]["rows"] == buy.attributes["rows"] and resp["sell"]["fields"] == sell.attributes["fields"]


async def test_period_change_in_options_is_applied_without_restart(hass, prices):
    entry = await _add_custom(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] is FlowResultType.MENU
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "tariffs"})
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "period_add"})
    assert flow["step_id"] == "period_general"
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {"start": "2026-08-17", "contract": "dynamic", "netting": False, "vat": 21,
         "tax_elec_ex": 0.09161, "tax_gas_ex": 0.60066, "provisional": False},
    )
    assert flow["step_id"] == "period_details_dynamic", flow
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {"buy": {"markup_elec_ex": 0.02, "markup_gas_ex": 0.05},
         "sell": {"export_markup_ex": 0.0, "export_costs_ex": 0.01}},
    )
    assert flow["type"] is FlowResultType.CREATE_ENTRY, flow
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    allin = float(_state(hass, "sensor.dyntarnl_e_all_in_now").state)
    market = float(_state(hass, "sensor.dyntarnl_e_market_now").state)
    sell = float(_state(hass, "sensor.dyntarnl_e_sell_now").state)
    assert sell == pytest.approx(market / 1.21 - 0.01, abs=1e-4)  # kaal − kosten, geen belasting/btw
    assert float(_state(hass, "sensor.dyntarnl_e_spread_now").state) == pytest.approx(allin - sell, abs=1e-5)


async def test_provisional_period_starting_soon_creates_a_repair(hass, prices):
    entry = await _add_custom(hass)
    periods = [dict(p) for p in entry.options["tariff_periods"]]
    periods.append({"start": "2026-08-25", "netting": False, "vat": 21.0, "tax_elec_ex": 0.09,
                    "provisional": True})
    hass.config_entries.async_update_entry(entry, options={**entry.options, "tariff_periods": periods})
    await hass.async_block_till_done()
    issues = ir.async_get(hass).issues
    assert (DOMAIN, "period_incomplete_2026-08-25") in issues


async def test_migration_from_1_2_adds_tariff_periods(hass, prices):
    entry = MockConfigEntry(
        domain=DOMAIN, version=1, minor_version=2,
        data={"supplier": "custom", "vat_percentage": 21, "markup_electricity": 0.02, "markup_gas": 0.05,
              "energy_tax_electricity": 0.09161, "energy_tax_gas": 0.60066, "epex_source": "easyenergy"},
        options={"forecast_enabled": False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.minor_version == 3
    starts = [p["start"] for p in entry.options["tariff_periods"]]
    assert starts == ["2026-01-01", "2027-01-01"]
    assert entry.options["tariff_periods"][1]["provisional"] is True
