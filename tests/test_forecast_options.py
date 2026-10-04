"""Options flow, migratie en — vooral — 'uit = exact het oude gedrag'."""

import asyncio

import pytest

import dyntarnl
import dyntarnl.sensor as sensor_platform
from dyntarnl.config_flow import DynTarNLConfigFlow, DynTarNLOptionsFlow
from dyntarnl.const import (
    CONF_FC_ACCURACY,
    CONF_FC_PROVIDERS,
    CONF_FC_WEIGHTING,
    CONF_FORECAST,
    DOMAIN,
    FORECAST_DEFAULTS,
)
from dyntarnl.coordinator import DynTarNLCoordinator
from dyntarnl.forecast.model import ForecastConfig
from dyntarnl.forecast.providers import PROVIDERS
from dyntarnl.forecast.sensor import forecast_unique_ids

from fc_hass import Entry, Hass, RegEntry
from fc_util import cfg, prices_data

A, B = "epexpredictor", "energypriceforecast"


# --- migratie -----------------------------------------------------------------


def test_migration_adds_defaults_off_and_keeps_options():
    hass = Hass()
    entry = Entry(options={"vat_percentage": 9.0}, minor_version=1)
    assert asyncio.run(dyntarnl.async_migrate_entry(hass, entry)) is True
    assert entry.minor_version == 2
    assert entry.options[CONF_FORECAST] is False
    assert entry.options["vat_percentage"] == 9.0  # bestaande opties blijven
    assert {k: entry.options[k] for k in FORECAST_DEFAULTS} == FORECAST_DEFAULTS


def test_migration_refuses_newer_major_version():
    assert asyncio.run(dyntarnl.async_migrate_entry(Hass(), Entry(version=2))) is False


def test_config_flow_is_minor_version_bump():
    assert DynTarNLConfigFlow.VERSION == 1 and DynTarNLConfigFlow.MINOR_VERSION == 2


def test_missing_options_mean_forecast_off():
    assert ForecastConfig.from_options({}).enabled is False
    assert ForecastConfig.from_options(None).enabled is False
    assert forecast_unique_ids("e", ForecastConfig.from_options({})) == set()


# --- options flow -------------------------------------------------------------


def _flow(options=None):
    flow = DynTarNLOptionsFlow()
    flow.config_entry = Entry(options=dict(options or FORECAST_DEFAULTS))
    flow.hass = None
    return flow


def _general(**overrides):
    base = {
        CONF_FC_PROVIDERS: [A, B],
        "forecast_ensemble": True,
        CONF_FC_WEIGHTING: "accuracy",
        "forecast_weight_exponent": "1",
        CONF_FC_ACCURACY: True,
        "forecast_horizon_hours": 72.0,
        "forecast_interval_hours": 6.0,
    }
    return {**base, **overrides}


ADVANCED = {
    "forecast_min_samples": 96.0,
    "forecast_weight_floor": 0.1,
    "forecast_bias_correction": False,
    "forecast_window_days": 28.0,
    "forecast_cheapest_hours": 3.0,
}


@pytest.fixture
def no_network(monkeypatch):
    async def ok(self, session):
        return None

    for cls in PROVIDERS.values():
        monkeypatch.setattr(cls, "async_validate", ok)


def test_options_off_saves_immediately_and_keeps_rest():
    flow = _flow({**FORECAST_DEFAULTS, CONF_FC_PROVIDERS: [B]})
    result = asyncio.run(flow.async_step_forecast_toggle({CONF_FORECAST: False}))
    assert result["type"] == "create_entry"
    assert result["data"][CONF_FORECAST] is False
    assert result["data"][CONF_FC_PROVIDERS] == [B]


def test_options_full_flow(no_network):
    flow = _flow()
    assert asyncio.run(flow.async_step_forecast_toggle({CONF_FORECAST: True}))["step_id"] == "forecast"
    assert asyncio.run(flow.async_step_forecast(_general()))["step_id"] == "provider_settings"
    result = asyncio.run(
        flow.async_step_provider_settings(
            {"forecast_epexpredictor_url": "http://local:8000", "forecast_energypriceforecast_api_key": ""}
        )
    )
    assert result["step_id"] == "advanced"
    result = asyncio.run(flow.async_step_advanced(dict(ADVANCED)))
    assert result["type"] == "create_entry"
    data = result["data"]
    assert data[CONF_FORECAST] is True
    assert data["forecast_epexpredictor_url"] == "http://local:8000"
    assert data["forecast_weight_exponent"] == 1 and data["forecast_horizon_hours"] == 72
    parsed = ForecastConfig.from_options(data)
    assert parsed.enabled and parsed.providers == (A, B)


def test_options_require_a_provider():
    flow = _flow()
    asyncio.run(flow.async_step_forecast_toggle({CONF_FORECAST: True}))
    result = asyncio.run(flow.async_step_forecast(_general(**{CONF_FC_PROVIDERS: []})))
    assert result["errors"] == {CONF_FC_PROVIDERS: "no_providers"}


def test_accuracy_weighting_needs_accuracy():
    flow = _flow()
    asyncio.run(flow.async_step_forecast_toggle({CONF_FORECAST: True}))
    result = asyncio.run(flow.async_step_forecast(_general(**{CONF_FC_ACCURACY: False})))
    assert result["errors"] == {CONF_FC_WEIGHTING: "weighting_needs_accuracy"}


def test_provider_validation_error_is_shown(monkeypatch):
    async def bad(self, session):
        return "cannot_connect"

    monkeypatch.setattr(PROVIDERS[A], "async_validate", bad)
    flow = _flow()
    asyncio.run(flow.async_step_forecast_toggle({CONF_FORECAST: True}))
    asyncio.run(flow.async_step_forecast(_general(**{CONF_FC_PROVIDERS: [A]})))
    result = asyncio.run(flow.async_step_provider_settings({"forecast_epexpredictor_url": "http://nope"}))
    assert result["errors"] == {"forecast_epexpredictor_url": "cannot_connect"}


# --- setup: uit = ongewijzigd ---------------------------------------------------


@pytest.fixture
def setup_env(monkeypatch):
    """Nep-HA rond async_setup_entry; telt timers en provider-calls."""
    timers, provider_calls = [], []
    monkeypatch.setattr(dyntarnl, "async_track_time_change", lambda *a, **k: timers.append(k) or (lambda: None))

    async def fake_update(self):
        return prices_data()

    monkeypatch.setattr(DynTarNLCoordinator, "_async_update_data", fake_update)

    async def fetch(self, session, horizon):
        provider_calls.append(self.key)
        return []

    for cls in PROVIDERS.values():
        monkeypatch.setattr(cls, "async_fetch", fetch)

    def run(options, registry=None, devices=None):
        hass = Hass(registry, devices)
        entry = Entry(options=options)
        hass.config_entries.entries.append(entry)
        asyncio.run(dyntarnl.async_setup_entry(hass, entry))
        entities = []
        asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))
        return hass, entry, entities

    return run, timers, provider_calls


def test_off_is_exactly_the_old_behaviour(setup_env):
    run, timers, provider_calls = setup_env
    hass, entry, entities = run(dict(FORECAST_DEFAULTS))

    assert entry.runtime_data.forecast is None
    assert len(timers) == 2              # alleen het uur-tik en de morgen-retry
    assert entry.tasks == []             # geen achtergrondtaken
    assert provider_calls == []          # geen enkele voorspel-call
    assert len(entities) == 45           # exact de bestaande sensorset
    assert not any("forecast" in e._attr_unique_id for e in entities)
    assert entry.runtime_data._listeners == []  # niemand luistert mee


def test_on_adds_separate_timer_entities_and_service(setup_env):
    run, timers, _ = setup_env
    hass, entry, entities = run({**FORECAST_DEFAULTS, CONF_FORECAST: True})

    assert entry.runtime_data.forecast is not None
    assert len(timers) == 3
    assert entry.tasks == ["dyntarnl_forecast_start"]  # eerste fetch op de achtergrond
    forecast = [e for e in entities if "forecast" in e._attr_unique_id]
    assert {e._attr_unique_id for e in forecast} == forecast_unique_ids(entry.entry_id, cfg())
    assert len(entities) == 45 + len(forecast)
    assert (DOMAIN, "get_prices") in hass.services.registered


def test_get_prices_is_registered_and_works_when_off(setup_env):
    run, _, _ = setup_env
    hass, entry, _ = run(dict(FORECAST_DEFAULTS))
    out = dyntarnl.get_prices(hass, {"energy": "electricity"})
    assert out["forecast_enabled"] is False and len(out["records"]) == 96


def test_turning_off_removes_forecast_entities_and_device(setup_env):
    run, _, _ = setup_env
    stale = [
        RegEntry("sensor.dyntarnl_forecast_epexpredictor_mae", "entry1_forecast_epexpredictor_mae", "entry1"),
        RegEntry("sensor.dyntarnl_e_cheapest_block_start", "entry1_electricity_forecast_cheapest_start", "entry1"),
        RegEntry("sensor.dyntarnl_e_all_in_now", "entry1_electricity_total_current", "entry1"),
    ]
    hass, _, _ = run(dict(FORECAST_DEFAULTS), registry=stale, devices={(DOMAIN, "entry1_forecast"): "dev1"})
    assert sorted(hass.entity_registry.removed) == sorted(stale[i].entity_id for i in (0, 1))
    assert hass.device_registry.removed == ["dev1"]


def test_deselected_provider_entities_are_removed(setup_env):
    run, _, _ = setup_env
    stale = [RegEntry("sensor.x", "entry1_forecast_energypriceforecast_mae", "entry1")]
    hass, _, _ = run({**FORECAST_DEFAULTS, CONF_FORECAST: True, CONF_FC_PROVIDERS: [A]}, registry=stale)
    assert hass.entity_registry.removed == ["sensor.x"]


def test_api_key_can_be_cleared(no_network):
    """Een leeggemaakt optioneel veld stuurt HA niet mee; dat moet 'leeg' worden."""
    flow = _flow({**FORECAST_DEFAULTS, "forecast_energypriceforecast_api_key": "oude-key"})
    asyncio.run(flow.async_step_forecast_toggle({CONF_FORECAST: True}))
    form = asyncio.run(flow.async_step_forecast(_general()))
    key_field = next(k for k in form["data_schema"].schema if k == "forecast_energypriceforecast_api_key")
    assert key_field.default is None  # geen default die HA weer invult

    asyncio.run(flow.async_step_provider_settings({"forecast_epexpredictor_url": "https://epexpredictor.batzill.com"}))
    result = asyncio.run(flow.async_step_advanced(dict(ADVANCED)))
    assert result["data"]["forecast_energypriceforecast_api_key"] == ""
