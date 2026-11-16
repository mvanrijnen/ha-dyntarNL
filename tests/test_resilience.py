"""Herstel na een mislukte fetch: niets mag een hele dag 'unavailable' blijven."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import dyntarnl
from dyntarnl.button import DynTarNLRefreshButton
from dyntarnl.const import ELECTRICITY, FORECAST_DEFAULTS
from dyntarnl.coordinator import DynTarNLCoordinator
from dyntarnl.sensor import DynTarNLComponentSensor, _COMPONENT_SENSORS

from fc_hass import Entry, Hass
from fc_util import prices_data

AMS = timezone(timedelta(hours=2))


@pytest.fixture
def started(monkeypatch):
    """Setup met vastgelegde timer-callbacks en een fetch-teller."""
    callbacks, fetches = [], []
    monkeypatch.setattr(dyntarnl, "async_track_time_change", lambda hass, cb, **kw: callbacks.append(cb) or (lambda: None))

    async def fake_update(self):
        fetches.append(1)
        return prices_data(tomorrow=[0.2] * 24)

    monkeypatch.setattr(DynTarNLCoordinator, "_async_update_data", fake_update)
    hass = Hass()
    entry = Entry(options=dict(FORECAST_DEFAULTS))
    hass.config_entries.entries.append(entry)
    asyncio.run(dyntarnl.async_setup_entry(hass, entry))
    hourly, retry = callbacks
    return entry, hourly, retry, fetches


def test_failed_fetch_is_retried_every_hour(started):
    entry, hourly, _, fetches = started
    coordinator = entry.runtime_data
    entry.tasks.clear()
    coordinator.last_update_success = False
    hourly(datetime(2026, 8, 17, 5, 0, 10, tzinfo=AMS))
    assert entry.tasks == ["dyntarnl_fetch"]
    coordinator.last_update_success = True
    entry.tasks.clear()
    hourly(datetime(2026, 8, 17, 6, 0, 10, tzinfo=AMS))
    assert entry.tasks == []  # gewoon meerollen, geen netwerk


def test_tomorrow_retry_leaves_failures_to_the_hourly_tick(started):
    """Na een mislukte fetch probeert het uur-tik opnieuw; de half-uur-retry niet ook nog
    (anders 3 aanroepen per uur tijdens een storing)."""
    entry, _, retry, _ = started
    coordinator = entry.runtime_data
    entry.tasks.clear()
    retry()
    assert entry.tasks == []  # morgen compleet
    coordinator.last_update_success = False
    retry()
    assert entry.tasks == []


def test_sensors_keep_rolling_forward_during_an_outage(started):
    """Na een tweede mislukte fetch stuurt HA geen updates meer; het uur-tik moet de
    sensoren dan zelf laten meerollen, anders blijft 'nu' op het uur van de storing staan."""
    entry, hourly, _, _ = started
    coordinator = entry.runtime_data
    calls = []
    coordinator.async_add_listener(lambda: calls.append(1))
    coordinator.last_update_success = False
    entry.tasks.clear()
    hourly(datetime(2026, 8, 17, 15, 0, 10, tzinfo=AMS))
    assert calls and entry.tasks == ["dyntarnl_fetch"]


def test_midnight_rebuckets_from_cache_before_fetching(started, at_time):
    entry, hourly, _, _ = started
    coordinator = entry.runtime_data
    tomorrow = coordinator.data[ELECTRICITY].tomorrow
    at_time(datetime(2026, 8, 18, 0, 0, 10, tzinfo=AMS))
    hourly(datetime(2026, 8, 18, 0, 0, 10, tzinfo=AMS))
    ed = coordinator.data[ELECTRICITY]
    assert ed.today == tomorrow and ed.tomorrow is None and ed.yesterday  # nieuwe dag, uit cache
    assert "dyntarnl_fetch" in entry.tasks


def test_entities_stay_available_with_cached_prices_after_a_failure(started):
    entry, *_ = started
    coordinator = entry.runtime_data
    coordinator.last_update_success = False
    sensor = DynTarNLComponentSensor(coordinator, ELECTRICITY, _COMPONENT_SENSORS[0])
    assert sensor.available
    assert DynTarNLRefreshButton(coordinator).available  # juist dan wil je verversen


def test_refresh_button_also_fetches_forecasts(started):
    """Handmatig verversen = prijzen én (als die aan staan) direct de voorspellingen."""
    entry, *_ = started
    coordinator = entry.runtime_data
    ticks = []

    async def tick(force=False):
        ticks.append(force)

    coordinator.forecast = SimpleNamespace(async_tick=tick)
    asyncio.run(DynTarNLRefreshButton(coordinator).async_press())
    assert ticks == [True]
    coordinator.forecast = None
    asyncio.run(DynTarNLRefreshButton(coordinator).async_press())  # zonder voorspellingen: alleen prijzen
    assert ticks == [True]
