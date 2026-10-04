"""Laadkosten EV: afrekenen, perioden, sessies, resetten, opslag en opties."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import dyntarnl
from dyntarnl.config_flow import DynTarNLOptionsFlow
from dyntarnl.const import CONF_EV_SENSOR, DOMAIN, FORECAST_DEFAULTS
from dyntarnl.ev import EvCostManager, EvCostTracker, period_key, period_start
from dyntarnl.ev_sensor import DynTarNLEvCostSensor, ev_unique_ids

from fc_hass import Entry, Hass, RegEntry
from fc_util import DAY_START, NOW, prices_data

UTC = timezone.utc
SENSOR = "sensor.smaev_3023109059_charging_session_energy"
# Vandaag 0,10 kaal tot 14:00 lokaal (12:00 UTC), daarna 0,30.
PRICES = prices_data([0.10] * 16 + [0.30] * 8)
CHEAP = PRICES["electricity"].today[0].total
DEAR = PRICES["electricity"].today[-1].total


def _state(value, unit="kWh"):
    return SimpleNamespace(state=str(value), attributes={"unit_of_measurement": unit})


def _manager(entry_id="ev1"):
    prices = SimpleNamespace(data=PRICES, async_add_listener=lambda cb: (lambda: None))
    return EvCostManager(None, Entry(entry_id=entry_id), prices, SENSOR)


# --- tracker ----------------------------------------------------------------------


def test_interval_is_split_over_quarters_with_their_own_price():
    tracker = EvCostTracker()
    start = DAY_START + timedelta(hours=15, minutes=30)   # 13:30 lokaal
    prices = {start: 0.2, start + timedelta(minutes=15): 0.2, start + timedelta(minutes=30): 0.4}
    tracker.add(start, start + timedelta(minutes=45), 3.0, lambda t: prices[t])
    assert tracker.total["kwh"] == pytest.approx(3.0)
    assert tracker.total["cost"] == pytest.approx(1.0 * 0.2 + 1.0 * 0.2 + 1.0 * 0.4)


def test_periods_and_quarter_keys():
    moment = datetime(2026, 8, 17, 12, tzinfo=UTC)
    assert period_key(moment, "quarter") == "2026-Q3"
    assert period_start(moment, "quarter").month == 7
    tracker = EvCostTracker()
    tracker.add(moment, moment + timedelta(minutes=15), 2.0, lambda t: 0.25)
    for key in ("session", "day", "month", "quarter", "year", "total"):
        assert tracker.value(key, moment)["cost"] == pytest.approx(0.5)
    # volgende dag: 'vandaag' begint weer op 0, maand/kwartaal/jaar niet
    tomorrow = moment + timedelta(days=1)
    assert tracker.value("day", tomorrow)["cost"] == 0
    assert tracker.value("month", tomorrow)["cost"] == pytest.approx(0.5)


def test_unpriced_energy_is_counted_but_not_costed():
    tracker = EvCostTracker()
    tracker.add(NOW, NOW + timedelta(minutes=15), 1.0, lambda t: None)
    assert tracker.total["kwh"] == 1.0 and tracker.total["unpriced_kwh"] == 1.0
    assert tracker.total["cost"] == 0


def test_reset_single_period_and_all():
    tracker = EvCostTracker()
    tracker.add(NOW, NOW + timedelta(minutes=15), 2.0, lambda t: 0.25)
    later = NOW + timedelta(hours=1)
    tracker.reset("month", later)
    assert tracker.value("month", later)["cost"] == 0
    assert tracker.value("day", later)["cost"] == pytest.approx(0.5)
    assert tracker.last_reset("month", later) > period_start(later, "month")
    tracker.reset("all", later)
    assert all(tracker.value(k, later)["cost"] == 0 for k in ("session", "day", "year", "total"))
    assert tracker.last_reset("total", later) is not None


# --- manager: kWh-teller ----------------------------------------------------------


def test_session_counter_is_priced_at_the_moment_of_use():
    m = _manager()
    t0 = DAY_START + timedelta(hours=10)                  # 10:00 UTC = goedkoop
    m.process(_state(0.0), t0)
    m.process(_state(1.5), t0 + timedelta(minutes=15))
    t1 = DAY_START + timedelta(hours=20)                  # duur deel
    m.process(_state(1.5), t1)
    m.process(_state(3.5), t1 + timedelta(minutes=15))
    total = m.tracker.value("total", t1)
    assert total["kwh"] == pytest.approx(3.5)
    assert total["cost"] == pytest.approx(1.5 * CHEAP + 2.0 * DEAR)
    assert m.mode == "energy"


def test_counter_reset_starts_new_session():
    m = _manager()
    t0 = DAY_START + timedelta(hours=10)
    m.process(_state(0.0), t0)
    m.process(_state(4.0), t0 + timedelta(minutes=30))
    m.process(_state(0.5), t0 + timedelta(hours=2))       # nieuwe sessie
    session = m.tracker.value("session", t0)
    assert session["kwh"] == pytest.approx(0.5)            # alleen de nieuwe sessie
    assert m.tracker.value("total", t0)["kwh"] == pytest.approx(4.5)


def test_unavailable_reading_is_skipped_and_wh_is_converted():
    m = _manager()
    t0 = DAY_START + timedelta(hours=10)
    m.process(_state(1000, "Wh"), t0)
    m.process(SimpleNamespace(state="unavailable", attributes={"unit_of_measurement": "Wh"}), t0 + timedelta(minutes=5))
    m.process(_state(3000, "Wh"), t0 + timedelta(minutes=15))
    assert m.tracker.value("total", t0)["kwh"] == pytest.approx(2.0)


# --- manager: vermogen --------------------------------------------------------------


def test_power_is_integrated_over_time():
    m = _manager()
    t0 = DAY_START + timedelta(hours=10)
    m.process(_state(7400, "W"), t0)
    m.process(_state(0, "W"), t0 + timedelta(hours=1))
    total = m.tracker.value("total", t0)
    assert total["kwh"] == pytest.approx(7.4)
    assert total["cost"] == pytest.approx(7.4 * CHEAP)
    assert m.mode == "power"


def test_power_session_starts_after_idle_gap():
    m = _manager()
    t0 = DAY_START + timedelta(hours=10)
    m.process(_state(11, "kW"), t0)
    m.process(_state(0, "kW"), t0 + timedelta(minutes=30))
    m.process(_state(11, "kW"), t0 + timedelta(hours=2))   # 90 min stil → nieuwe sessie
    m.process(_state(0, "kW"), t0 + timedelta(hours=2, minutes=15))
    assert m.tracker.value("session", t0)["kwh"] == pytest.approx(2.75)
    assert m.tracker.value("total", t0)["kwh"] == pytest.approx(5.5 + 2.75)


# --- opslag ---------------------------------------------------------------------------


def test_energy_counter_continues_across_restart(at_time):
    m = _manager("persist")
    t0 = DAY_START + timedelta(hours=10)
    m.process(_state(1.0), t0)
    m.process(_state(2.0), t0 + timedelta(minutes=15))

    restarted = _manager("persist")
    asyncio.run(restarted.async_load())
    restarted.process(_state(3.0), t0 + timedelta(hours=1))  # 1 kWh geladen terwijl HA uit stond
    assert restarted.tracker.value("total", t0)["kwh"] == pytest.approx(2.0)


# --- sensoren ------------------------------------------------------------------------


def test_sensors_show_cost_kwh_and_avg_price(at_time):
    at_time(DAY_START + timedelta(hours=11))
    m = _manager()
    t0 = DAY_START + timedelta(hours=10)
    m.process(_state(0.0), t0)
    m.process(_state(2.0), t0 + timedelta(minutes=15))
    today = DynTarNLEvCostSensor(m, "day")
    assert today.native_value == pytest.approx(round(2.0 * CHEAP, 4))
    attrs = today.extra_state_attributes
    assert attrs["energy_kwh"] == 2.0 and attrs["avg_price"] == pytest.approx(round(CHEAP, 5))
    assert today._attr_unique_id == "ev1_ev_cost_day"
    session = DynTarNLEvCostSensor(m, "session")
    assert session.extra_state_attributes["started"] is not None


# --- opties en setup -----------------------------------------------------------------


def _flow(options):
    flow = DynTarNLOptionsFlow()
    flow.config_entry = Entry(options=dict(options))
    flow.hass = None
    return flow


def test_options_menu_and_ev_step():
    flow = _flow(FORECAST_DEFAULTS)
    menu = asyncio.run(flow.async_step_init())
    assert menu["type"] == "menu" and set(menu["menu_options"]) == {"forecast_toggle", "ev"}
    result = asyncio.run(flow.async_step_ev({CONF_EV_SENSOR: SENSOR}))
    assert result["data"][CONF_EV_SENSOR] == SENSOR
    assert result["data"]["forecast_enabled"] is False  # rest blijft staan


def test_ev_sensor_can_be_cleared():
    flow = _flow({**FORECAST_DEFAULTS, CONF_EV_SENSOR: SENSOR})
    result = asyncio.run(flow.async_step_ev({}))
    assert result["data"][CONF_EV_SENSOR] == ""


@pytest.fixture
def setup(monkeypatch):
    from dyntarnl.coordinator import DynTarNLCoordinator
    import dyntarnl.sensor as sensor_platform

    async def fake_update(self):
        return PRICES

    monkeypatch.setattr(DynTarNLCoordinator, "_async_update_data", fake_update)
    monkeypatch.setattr(dyntarnl, "async_track_time_change", lambda *a, **k: (lambda: None))

    def run(options, registry=None, devices=None):
        hass = Hass(registry, devices)
        entry = Entry(options=options)
        hass.config_entries.entries.append(entry)
        asyncio.run(dyntarnl.async_setup_entry(hass, entry))
        entities = []
        asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))
        return hass, entry, entities

    return run


def test_ev_on_adds_six_cost_sensors(setup):
    hass, entry, entities = setup({**FORECAST_DEFAULTS, CONF_EV_SENSOR: SENSOR})
    ev = [e for e in entities if "_ev_" in e._attr_unique_id]
    assert {e._attr_unique_id for e in ev} == ev_unique_ids(entry.entry_id, True)
    assert len(ev) == 6
    assert "dyntarnl_ev_start" in entry.tasks
    assert (DOMAIN, "reset_ev_cost") in hass.services.registered


def test_ev_off_changes_nothing_and_cleans_up(setup):
    stale = [RegEntry("sensor.dyntarnl_ev_cost_today", "entry1_ev_cost_day", "entry1")]
    hass, entry, entities = setup(dict(FORECAST_DEFAULTS), registry=stale, devices={(DOMAIN, "entry1_ev"): "dev-ev"})
    assert entry.runtime_data.ev is None
    assert len(entities) == 45 and entry.tasks == []
    assert hass.entity_registry.removed == ["sensor.dyntarnl_ev_cost_today"]
    assert hass.device_registry.removed == ["dev-ev"]
    with pytest.raises(Exception, match="Laadkosten"):
        dyntarnl.reset_ev_cost(hass, "all")


# --- sessie verwijderen ---------------------------------------------------------------


def _two_sessions():
    m = _manager()
    t0 = DAY_START + timedelta(hours=10)
    m.process(_state(0.0), t0)
    m.process(_state(2.0), t0 + timedelta(minutes=15))     # sessie 1: 2 kWh
    m.process(_state(0.5), t0 + timedelta(hours=2))        # sessie 2 begint (teller reset)
    m.process(_state(1.5), t0 + timedelta(hours=2, minutes=15))
    return m, t0


def test_sessions_are_listed_newest_first():
    m, t0 = _two_sessions()
    sessions = m.tracker.sessions()
    assert [round(s["kwh"], 3) for s in sessions] == [1.5, 2.0]
    assert sessions[0]["id"] != sessions[1]["id"]


def test_deleting_a_finished_session_subtracts_it_everywhere():
    m, t0 = _two_sessions()
    first = m.tracker.sessions()[1]["id"]
    removed = m.delete_session(first)
    assert removed["kwh"] == pytest.approx(2.0)
    for key in ("day", "month", "quarter", "year", "total"):
        assert m.tracker.value(key, t0)["kwh"] == pytest.approx(1.5)
        assert m.tracker.value(key, t0)["cost"] == pytest.approx(1.5 * CHEAP)
    assert [round(s["kwh"], 3) for s in m.tracker.sessions()] == [1.5]


def test_deleting_the_running_session_stops_counting_it():
    m, t0 = _two_sessions()
    m.delete_session("last")
    m.process(_state(3.0), t0 + timedelta(hours=2, minutes=30))  # andere auto laadt door
    assert m.tracker.value("total", t0)["kwh"] == pytest.approx(2.0)
    assert m.tracker.value("session", t0)["kwh"] == 0
    m.process(_state(0.4), t0 + timedelta(hours=5))              # nieuwe sessie telt weer
    assert m.tracker.value("total", t0)["kwh"] == pytest.approx(2.4)


def test_delete_after_reset_does_not_subtract_twice():
    m, t0 = _two_sessions()
    m.tracker.reset("month", t0 + timedelta(hours=3))
    m.delete_session(m.tracker.sessions()[1]["id"])
    assert m.tracker.value("month", t0 + timedelta(hours=3))["kwh"] == 0  # niet negatief
    assert m.tracker.value("total", t0)["kwh"] == pytest.approx(1.5)


def test_unknown_session_is_an_error():
    m, _ = _two_sessions()
    with pytest.raises(ValueError):
        m.delete_session("1999-01-01 00:00")


def test_session_list_survives_restart():
    m, t0 = _two_sessions()
    restored = EvCostTracker()
    restored.load(m.tracker.to_dict())
    assert restored.sessions() == m.tracker.sessions()
    restored.delete_session(restored.sessions()[1]["id"])
    assert restored.total["kwh"] == pytest.approx(1.5)


def test_session_follows_the_charger_counter():
    """Bij een sessie-teller: start = moment van terugvallen, kWh = stand van de lader."""
    m = _manager()
    t0 = DAY_START + timedelta(hours=8)
    m.process(_state(0.0), t0)
    m.process(_state(9.6), t0 + timedelta(hours=1))              # vorige sessie
    drop = t0 + timedelta(hours=6)
    m.process(_state(0.0), drop)                                  # auto ingeplugd: teller naar 0
    m.process(_state(2.2), drop + timedelta(minutes=30))
    m.process(_state(4.1), drop + timedelta(minutes=45))
    session = m.tracker.value("session", drop)
    assert session["start"] == drop.isoformat()                   # niet de vorige meting
    assert session["kwh"] == pytest.approx(m.reading_kwh) == pytest.approx(4.1)
    assert DynTarNLEvCostSensor(m, "session").extra_state_attributes["charger_reading_kwh"] == 4.1
