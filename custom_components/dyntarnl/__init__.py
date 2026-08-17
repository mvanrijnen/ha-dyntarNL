"""DynTarNL — dynamische tarieven voor meerdere NL-leveranciers."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change

from .coordinator import DynTarNLConfigEntry, DynTarNLCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]
_AFTERNOON_RETRY_HOURS = [13, 14, 15, 16]


async def async_setup_entry(hass: HomeAssistant, entry: DynTarNLConfigEntry) -> bool:
    coordinator = DynTarNLCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _scheduled_refresh(_now) -> None:
        entry.async_create_background_task(
            hass, coordinator.async_request_refresh(), "dyntarnl_refresh"
        )

    entry.async_on_unload(
        async_track_time_change(hass, _scheduled_refresh, minute=0, second=10)
    )
    entry.async_on_unload(
        async_track_time_change(
            hass, _scheduled_refresh, hour=_AFTERNOON_RETRY_HOURS, minute=30, second=10
        )
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DynTarNLConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
