"""DynTarNL — dynamische tarieven voor meerdere NL-leveranciers."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.event import async_track_time_change

from .const import DOMAIN
from .coordinator import DynTarNLConfigEntry, DynTarNLCoordinator
from .model import tomorrow_complete

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]

# Vanaf 13:00 kunnen de day-ahead prijzen van morgen binnenkomen -- stroom meestal
# rond het middaguur, gas beduidend later. We proberen het elk half uur opnieuw tot
# ze er zijn; zodra ze binnen zijn stopt het vanzelf, dus het blijft zuinig.
_TOMORROW_RETRY_HOURS = list(range(13, 24))

SERVICE_REFRESH = "refresh"


def _register_services(hass: HomeAssistant) -> None:
    """Registreer de dyntarnl.refresh-service (overal aanroepbaar)."""
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        return

    async def _handle_refresh(_call: ServiceCall) -> None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            coordinator: DynTarNLCoordinator | None = getattr(entry, "runtime_data", None)
            if coordinator is not None:
                await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh)


async def async_setup_entry(hass: HomeAssistant, entry: DynTarNLConfigEntry) -> bool:
    coordinator = DynTarNLCoordinator(hass, entry)
    # Ophalen bij opstarten.
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)

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

    @callback
    def _retry_tomorrow(_now=None) -> None:
        """Elk half uur vanaf 13:00: alleen ophalen zolang morgen nog ontbreekt."""
        if not tomorrow_complete(coordinator.data):
            _fetch()

    # 2) Doorproberen tot de prijzen van morgen binnen zijn (ook 's avonds).
    entry.async_on_unload(
        async_track_time_change(
            hass, _retry_tomorrow, hour=_TOMORROW_RETRY_HOURS, minute=30, second=10
        )
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DynTarNLConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not hass.config_entries.async_loaded_entries(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_REFRESH)
    return unloaded
