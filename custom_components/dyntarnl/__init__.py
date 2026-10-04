"""DynTarNL — dynamische tarieven voor meerdere NL-leveranciers."""

from __future__ import annotations

import hashlib

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_EV_SENSOR,
    DOMAIN,
    ELECTRICITY,
    ENSEMBLE,
    FC_MAX_HORIZON,
    FORECAST_DEFAULTS,
    GAS,
)
from .coordinator import DynTarNLConfigEntry, DynTarNLCoordinator
from .ev import ALL_KEYS as EV_KEYS, EvCostManager, storage_key as ev_storage_key
from .ev_sensor import ev_unique_ids, is_ev_unique_id
from .forecast.accuracy import STORAGE_VERSION
from .forecast.coordinator import DynTarNLForecastCoordinator, storage_key
from .forecast.model import ForecastConfig
from .forecast.sensor import forecast_unique_ids, is_forecast_unique_id
from .forecast.views import service_response
from .model import tomorrow_complete

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]

# Vanaf 13:00 kunnen de day-ahead prijzen van morgen binnenkomen -- stroom meestal
# rond het middaguur, gas beduidend later. We proberen het elk half uur opnieuw tot
# ze er zijn; zodra ze binnen zijn stopt het vanzelf, dus het blijft zuinig.
_TOMORROW_RETRY_HOURS = list(range(13, 24))


def _spread(entry_id: str, span: int) -> int:
    """Vaste, per installatie verschillende offset in minuten.

    Zonder dit klopt élke DynTarNL-installatie op exact dezelfde seconde bij de
    leverancier aan. De offset komt uit de entry_id, dus hij is stabiel over
    herstarts heen (geen willekeur die elke keer opnieuw loot).
    """
    return int(hashlib.sha256(entry_id.encode()).hexdigest(), 16) % span


SERVICE_REFRESH = "refresh"
SERVICE_GET_PRICES = "get_prices"
SERVICE_RESET_EV = "reset_ev_cost"
SERVICE_DELETE_EV_SESSION = "delete_ev_session"

RESET_EV_SCHEMA = vol.Schema({vol.Required("period"): vol.In([*EV_KEYS, "all"])})
DELETE_EV_SESSION_SCHEMA = vol.Schema({vol.Optional("session_id", default="last"): cv.string})

GET_PRICES_SCHEMA = vol.Schema(
    {
        vol.Optional("energy", default=ELECTRICITY): vol.In([ELECTRICITY, GAS]),
        vol.Optional("include_forecast", default=True): cv.boolean,
        vol.Optional("provider", default=ENSEMBLE): cv.string,
        vol.Optional("horizon"): vol.All(vol.Coerce(int), vol.Range(min=1, max=FC_MAX_HORIZON)),
    }
)


def _register_services(hass: HomeAssistant) -> None:
    """Registreer de dyntarnl-services (overal aanroepbaar)."""
    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):

        async def _handle_refresh(_call: ServiceCall) -> None:
            for entry in hass.config_entries.async_entries(DOMAIN):
                coordinator: DynTarNLCoordinator | None = getattr(entry, "runtime_data", None)
                if coordinator is not None:
                    await coordinator.async_request_refresh()

        hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh)

    if not hass.services.has_service(DOMAIN, SERVICE_GET_PRICES):

        async def _handle_get_prices(call: ServiceCall) -> ServiceResponse:
            """Volledige prijsreeks (gepubliceerd + optioneel voorspeld) als response."""
            return get_prices(hass, dict(call.data))

        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_PRICES,
            _handle_get_prices,
            schema=GET_PRICES_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_RESET_EV):

        async def _handle_reset_ev(call: ServiceCall) -> None:
            reset_ev_cost(hass, call.data["period"])

        hass.services.async_register(DOMAIN, SERVICE_RESET_EV, _handle_reset_ev, schema=RESET_EV_SCHEMA)

    if not hass.services.has_service(DOMAIN, SERVICE_DELETE_EV_SESSION):

        async def _handle_delete_session(call: ServiceCall) -> ServiceResponse:
            return {"deleted": delete_ev_session(hass, call.data["session_id"])}

        hass.services.async_register(
            DOMAIN,
            SERVICE_DELETE_EV_SESSION,
            _handle_delete_session,
            schema=DELETE_EV_SESSION_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )


def _ev_manager(hass: HomeAssistant) -> EvCostManager:
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator: DynTarNLCoordinator | None = getattr(entry, "runtime_data", None)
        if coordinator is not None and coordinator.ev is not None:
            return coordinator.ev
    raise ServiceValidationError("Laadkosten EV staat niet aan (kies een lader-sensor in Configureren)")


def delete_ev_session(hass: HomeAssistant, session_id: str) -> dict:
    """Haal een laadsessie (bijv. van een andere auto) van alle tellers af."""
    try:
        return _ev_manager(hass).delete_session(session_id)
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err


def reset_ev_cost(hass: HomeAssistant, period: str) -> None:
    """Zet een laadkosten-teller (of 'all') handmatig op 0."""
    _ev_manager(hass).reset(period)


def get_prices(hass: HomeAssistant, params: dict) -> dict:
    """Kern van `dyntarnl.get_prices`. Werkt ook met de voorspel-optie uit
    (dan alleen gepubliceerde prijzen, `forecast_enabled: false`)."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator: DynTarNLCoordinator | None = getattr(entry, "runtime_data", None)
        if coordinator is not None:
            break
    else:
        raise ServiceValidationError("DynTarNL is niet geladen")
    fc = coordinator.forecast
    try:
        return service_response(
            coordinator.data,
            fc.data if fc else None,
            fc.cfg if fc else None,
            coordinator.supplier.name if coordinator.supplier else "",
            params.get("energy", ELECTRICITY),
            params.get("include_forecast", True),
            params.get("provider", ENSEMBLE),
            params.get("horizon"),
            dt_util.utcnow(),
        )
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err


async def async_migrate_entry(hass: HomeAssistant, entry: DynTarNLConfigEntry) -> bool:
    """1.1 → 1.2: voorspel-opties toevoegen met hun standaardwaarde (UIT).

    Alleen de minor-versie gaat omhoog, zodat terug naar 1.x zonder verwijderen kan:
    oudere code negeert de extra opties gewoon.
    """
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        hass.config_entries.async_update_entry(
            entry, options={**FORECAST_DEFAULTS, **entry.options}, minor_version=2
        )
    return True


def _cleanup_optional_entities(
    hass: HomeAssistant, entry: DynTarNLConfigEntry, cfg: ForecastConfig, ev_enabled: bool
) -> None:
    """Ruim voorspel- en laadkosten-entiteiten op die bij de huidige opties niet meer
    horen (optie uit, of een provider uitgezet); anders blijven er 'unavailable'-wezen."""
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    entry_id = entry.entry_id
    registry = er.async_get(hass)
    keep = forecast_unique_ids(entry_id, cfg) | ev_unique_ids(entry_id, ev_enabled)
    for ent in er.async_entries_for_config_entry(registry, entry_id):
        optional = is_forecast_unique_id(entry_id, ent.unique_id) or is_ev_unique_id(
            entry_id, ent.unique_id
        )
        if optional and ent.unique_id not in keep:
            registry.async_remove(ent.entity_id)
    devices = dr.async_get(hass)
    for suffix, enabled in (("forecast", cfg.enabled), ("ev", ev_enabled)):
        if enabled:
            continue
        device = devices.async_get_device(identifiers={(DOMAIN, f"{entry_id}_{suffix}")})
        if device is not None:
            devices.async_remove_device(device.id)


def _setup_forecast(
    hass: HomeAssistant, entry: DynTarNLConfigEntry, coordinator: DynTarNLCoordinator
) -> None:
    """Voorspel-timers. Volledig los van de prijzen: alles draait als achtergrondtaak."""
    fc: DynTarNLForecastCoordinator = coordinator.forecast

    # Na elke prijs-update (ook het uurlijkse meerollen): opnieuw samenvoegen en
    # afrekenen, zonder netwerk.
    entry.async_on_unload(coordinator.async_add_listener(fc.handle_prices_update))

    @callback
    def _tick(_now=None) -> None:
        entry.async_create_background_task(hass, fc.async_tick(), "dyntarnl_forecast")

    # Elk kwartier kijken of een provider aan de beurt is (meestal niet: standaard
    # haalt elke provider maar eens per 6 uur op). Minuut verschilt per installatie.
    offset = _spread(entry.entry_id, 15)
    entry.async_on_unload(
        async_track_time_change(hass, _tick, minute=[offset + 15 * i for i in range(4)], second=40)
    )
    entry.async_create_background_task(hass, fc.async_start(), "dyntarnl_forecast_start")


async def _async_options_updated(hass: HomeAssistant, entry: DynTarNLConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: DynTarNLConfigEntry) -> bool:
    coordinator = DynTarNLCoordinator(hass, entry)
    # Ophalen bij opstarten.
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Voorspellingen: alleen als de optie aan staat. Uit = exact het oude gedrag.
    forecast_cfg = ForecastConfig.from_options(entry.options)
    ev_sensor = entry.options.get(CONF_EV_SENSOR) or ""
    _cleanup_optional_entities(hass, entry, forecast_cfg, bool(ev_sensor))
    if forecast_cfg.enabled:
        coordinator.forecast = DynTarNLForecastCoordinator(hass, entry, coordinator, forecast_cfg)
    # Laadkosten EV: alleen als er een lader-sensor gekozen is.
    if ev_sensor:
        coordinator.ev = EvCostManager(hass, entry, coordinator, ev_sensor)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

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

    # 2) Doorproberen tot de prijzen van morgen binnen zijn (ook 's avonds). Elk half
    #    uur, op een minuut die per installatie verschilt — anders vragen alle
    #    installaties tegelijk aan bij de leverancier.
    offset = _spread(entry.entry_id, 30)
    entry.async_on_unload(
        async_track_time_change(
            hass,
            _retry_tomorrow,
            hour=_TOMORROW_RETRY_HOURS,
            minute=[offset, offset + 30],
            second=20,
        )
    )

    # 3) Voorspellingen (optioneel): eigen, losgekoppelde timers.
    if coordinator.forecast is not None:
        _setup_forecast(hass, entry, coordinator)
    # 4) Laadkosten EV (optioneel): luistert naar de sensor van de lader.
    if coordinator.ev is not None:
        entry.async_create_background_task(hass, coordinator.ev.async_start(), "dyntarnl_ev_start")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DynTarNLConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not hass.config_entries.async_loaded_entries(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_REFRESH)
        hass.services.async_remove(DOMAIN, SERVICE_GET_PRICES)
        hass.services.async_remove(DOMAIN, SERVICE_RESET_EV)
        hass.services.async_remove(DOMAIN, SERVICE_DELETE_EV_SESSION)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: DynTarNLConfigEntry) -> None:
    """Integratie verwijderd: ook de opgeslagen voorspellingen/statistiek weg."""
    await Store(hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()
    await Store(hass, 1, ev_storage_key(entry.entry_id)).async_remove()
