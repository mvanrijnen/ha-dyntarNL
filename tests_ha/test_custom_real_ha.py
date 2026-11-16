"""CUSTOM-leverancier end-to-end in een ECHTE Home Assistant (geen stubs).

Doorloopt wat een gebruiker doet: integratie toevoegen → 'Custom supplier' → tarieven
invullen → sensoren gebruiken → opties openen → verwijderen. Alleen de prijs-API wordt
nagebootst met de vastgelegde easyEnergy-fixtures.
"""

import json
from pathlib import Path

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures"
EASY = "https://price-graph.mijn.easyenergy.com/api/prices"
DOMAIN = "dyntarnl"

CUSTOM_INPUT = {
    "vat_percentage": 21,
    "markup_electricity": 0.02,
    "markup_gas": 0.05,
    "energy_tax_electricity": 0.09161,
    "energy_tax_gas": 0.60066,
    "epex_source": "easyenergy",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
async def prices(hass, aioclient_mock, freezer):
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    freezer.move_to("2026-08-17T12:00:00+00:00")  # = 14:00 lokaal, past bij de fixtures
    elec = json.loads((FIX / "easyenergy_electricity.json").read_text(encoding="utf-8"))
    gas = json.loads((FIX / "easyenergy_gas.json").read_text(encoding="utf-8"))
    aioclient_mock.get(f"{EASY}?type=electricity", json=elec)
    aioclient_mock.get(f"{EASY}?type=gas", json=gas)
    return elec


async def _add_custom(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"supplier": "custom"})
    # Issue #3: hier kwam "Unknown error occurred" in plaats van dit formulier.
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "custom", result
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CUSTOM_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()
    return result["result"]


async def test_custom_supplier_can_be_set_up_and_used(hass, prices):
    entry = await _add_custom(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.title == "Custom supplier"
    assert entry.data["markup_electricity"] == 0.02
    assert entry.options["forecast_enabled"] is False  # voorspellingen standaard uit

    # Prijs van nu (14:00 lokaal) volgens de CUSTOM-formule.
    row = next(r for r in prices["prices"] if r["from"].startswith("2026-08-17T14:00"))
    market_ex = float(row["price"])
    expected = (market_ex + 0.02 + 0.09161) * 1.21  # sensor rondt af op 5 decimalen

    allin = hass.states.get("sensor.dyntarnl_e_all_in_now")
    market = hass.states.get("sensor.dyntarnl_e_market_now")
    fee = hass.states.get("sensor.dyntarnl_e_markup_incl_vat")
    assert allin is not None, sorted(hass.states.async_entity_ids("sensor"))[:20]
    assert float(allin.state) == pytest.approx(expected, abs=1e-5)
    assert float(market.state) == pytest.approx(market_ex * 1.21, abs=1e-5)
    assert float(fee.state) == pytest.approx(0.02 * 1.21, abs=1e-5)
    assert allin.attributes["unit_of_measurement"] == "€/kWh"
    assert len(allin.attributes["today"]) == 24

    gas = hass.states.get("sensor.dyntarnl_g_all_in_now")
    assert gas is not None and gas.state not in ("unknown", "unavailable")

    # Niets van de voorspellingen zolang die uit staan.
    assert not [e for e in hass.states.async_entity_ids() if "forecast" in e]

    # get_prices werkt ook zonder voorspellingen.
    resp = await hass.services.async_call(
        DOMAIN, "get_prices", {"energy": "electricity"}, blocking=True, return_response=True
    )
    assert resp["forecast_enabled"] is False
    assert len(resp["records"]) % 96 == 0 and len(resp["records"]) >= 96  # hele dagen per kwartier
    assert {r["source"] for r in resp["records"]} == {"published"}

    # Ververs-service.
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.dyntarnl_e_all_in_now").state) == pytest.approx(expected, abs=1e-5)


async def test_options_flow_opens_and_all_steps_validate(hass, prices):
    entry = await _add_custom(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "init"

    # Voorspellingen aan: elke stap moet een geldig formulier geven.
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"forecast_enabled": True})
    assert result["step_id"] == "forecast", result
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "forecast_providers": ["epexpredictor"],
            "forecast_ensemble": True,
            "forecast_weighting": "equal",
            "forecast_weight_exponent": "1",
            "forecast_accuracy": True,
            "forecast_horizon_hours": 72,
            "forecast_interval_hours": 6,
        },
    )
    assert result["step_id"] == "provider_settings", result
    assert "private_pro_url" in (result.get("description_placeholders") or {})


async def test_options_off_keeps_custom_tariffs(hass, prices):
    entry = await _add_custom(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"forecast_enabled": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data["markup_electricity"] == 0.02
    assert hass.states.get("sensor.dyntarnl_e_all_in_now").state not in ("unknown", "unavailable")


async def test_unload_and_remove(hass, prices):
    entry = await _add_custom(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()


# --- Opnieuw configureren ---------------------------------------------------------

ESSENT = "https://www.essent.nl/api/public/dynamicpricing/dynamic-prices/v1"


async def _reconfigure(hass, entry):
    from homeassistant.config_entries import SOURCE_RECONFIGURE

    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )


async def test_reconfigure_custom_to_essent_keeps_entities(hass, prices, aioclient_mock, caplog):
    aioclient_mock.get(ESSENT, json=json.loads((FIX / "essent.json").read_text(encoding="utf-8")))
    entry = await _add_custom(hass)
    entity_id = "sensor.dyntarnl_e_all_in_now"
    from homeassistant.helpers import entity_registry as er

    unique_before = er.async_get(hass).async_get(entity_id).unique_id

    result = await _reconfigure(hass, entry)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"supplier": "essent"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.data == {"supplier": "essent"} and entry.title == "Essent"
    assert entry.options["forecast_enabled"] is False  # opties behouden
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1  # geen tweede entry
    # HA 2026.11 waarschuwt bij dubbel herladen (update-listener + reload); stopt in 2026.12.
    assert "should use it for scheduling a reload" not in caplog.text
    assert er.async_get(hass).async_get(entity_id).unique_id == unique_before
    state = hass.states.get(entity_id)  # zelfde entity_id, nu met Essent-prijzen
    assert state is not None and state.state not in ("unknown", "unavailable")
    essent = json.loads((FIX / "essent.json").read_text(encoding="utf-8"))
    day = next(d for d in essent["prices"] if d["date"] == "2026-08-17")
    row = next(t for t in day["electricity"]["tariffs"] if t["startDateTime"].startswith("2026-08-17T14:00"))
    assert float(state.state) == pytest.approx(row["totalAmount"], abs=1e-5)


async def test_reconfigure_custom_tariffs_are_prefilled_and_applied(hass, prices, caplog):
    entry = await _add_custom(hass)
    result = await _reconfigure(hass, entry)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"supplier": "custom"})
    assert result["step_id"] == "reconfigure_custom"
    # Huidige tarieven staan voorgevuld.
    defaults = {str(k): k.default() for k in result["data_schema"].schema if hasattr(k, "default")}
    assert defaults["markup_electricity"] == 0.02

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**CUSTOM_INPUT, "markup_electricity": 0.03}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    row = next(r for r in prices["prices"] if r["from"].startswith("2026-08-17T14:00"))
    expected = (float(row["price"]) + 0.03 + 0.09161) * 1.21
    assert float(hass.states.get("sensor.dyntarnl_e_all_in_now").state) == pytest.approx(expected, abs=1e-5)
    assert entry.data["markup_electricity"] == 0.03
    assert "should use it for scheduling a reload" not in caplog.text


async def test_leftover_ev_device_and_entities_are_cleaned_up(hass, prices):
    """Zoals bij een installatie die eerder de laadkosten-testversie draaide."""
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    entry = await _add_custom(hass)
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{entry.entry_id}_ev")}, name="DynTarNL EV"
    )
    ent = er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_ev_cost_total", config_entry=entry, device_id=dev.id
    )

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert dr.async_get(hass).async_get(dev.id) is None
    assert er.async_get(hass).async_get(ent.entity_id) is None
    # De echte devices en sensoren blijven.
    left = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    idents = {i for d in left for _, i in d.identifiers}
    assert f"{entry.entry_id}_electricity" in idents and f"{entry.entry_id}_ev" not in idents
    assert hass.states.get("sensor.dyntarnl_e_all_in_now").state not in ("unknown", "unavailable")
