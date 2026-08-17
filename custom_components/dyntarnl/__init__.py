"""DynTarNL — dynamische tarieven voor meerdere NL-leveranciers."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change

from .coordinator import DynTarNLConfigEntry, DynTarNLCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]

# Uren in de middag waarop de day-ahead prijzen van morgen doorgaans binnenkomen.
_AFTERNOON_RETRY_HOURS = [13, 14, 15, 16]


async def async_setup_entry(hass: HomeAssistant, entry: DynTarNLConfigEntry) -> bool:
    coordinator = DynTarNLCoordinator(hass, entry)
    # Ophalen bij opstarten.
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _fetch(_now=None) -> None:
        """Data daadwerkelijk (opnieuw) ophalen."""
        entry.async_create_background_task(
            hass, coordinator.async_request_refresh(), "dyntarnl_fetch"
        )

    @callback
    def _hourly(now) -> None:
        """Elk heel uur. Na middernacht opnieuw ophalen (nieuwe dag → herbucketen);
        de overige uren alleen de sensoren laten meerollen — zónder netwerk-call."""
        if now.hour == 0:
            _fetch()
        else:
            coordinator.async_update_listeners()

    # 1) Elk heel uur: meerollen (of om 00:00 opnieuw ophalen).
    entry.async_on_unload(
        async_track_time_change(hass, _hourly, minute=0, second=10)
    )
    # 2) 's Middags extra ophalen tot de prijzen van morgen binnen zijn.
    entry.async_on_unload(
        async_track_time_change(
            hass, lambda now: _fetch(), hour=_AFTERNOON_RETRY_HOURS, minute=30, second=10
        )
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DynTarNLConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
