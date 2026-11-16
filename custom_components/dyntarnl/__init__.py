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

from .const import DOMAIN, ELECTRICITY, ENSEMBLE, FC_MAX_HORIZON, FORECAST_DEFAULTS, GAS
from .coordinator import DynTarNLConfigEntry, DynTarNLCoordinator
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


# Alleen via de UI in te stellen; services worden bij het laden van de integratie
# geregistreerd, zodat ze ook bestaan als het opzetten (nog) mislukt.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    _register_services(hass)
    return True


SERVICE_REFRESH = "refresh"
SERVICE_GET_PRICES = "get_prices"

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


def _expected_devices(entry_id: str, cfg: ForecastConfig) -> set[str]:
    """Device-identifiers die DynTarNL bij deze opties zelf aanmaakt."""
    expected = {f"{entry_id}_electricity", f"{entry_id}_gas", f"{entry_id}_service"}
    if cfg.enabled:
        expected.add(f"{entry_id}_forecast")
    return expected


def _cleanup_forecast_entities(
    hass: HomeAssistant, entry: DynTarNLConfigEntry, cfg: ForecastConfig
) -> None:
    """Ruim op wat bij de huidige opties niet meer hoort; anders blijven er
    'unavailable'-wezen staan.

    - voorspel-entiteiten van een uitgezette optie of provider;
    - eigen devices die DynTarNL niet (meer) aanmaakt, bijv. het Forecast-device met de
      optie uit, of restanten van een testversie (zoals 'DynTarNL EV'). HA verwijdert de
      entiteiten van zo'n device automatisch mee.
    """
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    registry = er.async_get(hass)
    keep = forecast_unique_ids(entry.entry_id, cfg)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        if is_forecast_unique_id(entry.entry_id, ent.unique_id) and ent.unique_id not in keep:
            registry.async_remove(ent.entity_id)

    devices = dr.async_get(hass)
    expected = _expected_devices(entry.entry_id, cfg)
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        ours = {ident for domain, ident in device.identifiers if domain == DOMAIN}
        if ours and not ours & expected:
            for ent in er.async_entries_for_device(registry, device.id, include_disabled_entities=True):
                registry.async_remove(ent.entity_id)
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
    entry.async_on_unload(fc.async_flush)


async def _async_options_updated(hass: HomeAssistant, entry: DynTarNLConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: DynTarNLConfigEntry) -> bool:
    coordinator = DynTarNLCoordinator(hass, entry)
    # Ophalen bij opstarten.
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Voorspellingen: alleen als de optie aan staat. Uit = exact het oude gedrag.
    forecast_cfg = ForecastConfig.from_options(entry.options)
    _cleanup_forecast_entities(hass, entry, forecast_cfg)
    if forecast_cfg.enabled:
        coordinator.forecast = DynTarNLForecastCoordinator(hass, entry, coordinator, forecast_cfg)

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
        """Elk heel uur. Na middernacht eerst uit de cache herindelen (nieuwe dag) en dan
        opnieuw ophalen. Mislukte de vorige fetch, dan elk uur opnieuw proberen; anders
        alleen de sensoren laten meerollen — zónder netwerk-call."""
        if now.hour == 0:
            coordinator.async_rebucket()
            _fetch()
        elif not coordinator.last_update_success:
            _fetch()
        else:
            coordinator.async_update_listeners()

    # 1) Elk heel uur: meerollen (of om 00:00 opnieuw ophalen).
    entry.async_on_unload(
        async_track_time_change(hass, _hourly, minute=0, second=10)
    )

    @callback
    def _retry_tomorrow(_now=None) -> None:
        """Elk half uur vanaf 13:00: alleen ophalen zolang morgen nog ontbreekt (of de
        vorige poging mislukte)."""
        if not coordinator.last_update_success or not tomorrow_complete(coordinator.data):
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
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DynTarNLConfigEntry) -> bool:
    # Services blijven geregistreerd (zie async_setup); zonder geladen entry geven ze een
    # nette foutmelding.
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: DynTarNLConfigEntry) -> None:
    """Integratie verwijderd: ook de opgeslagen voorspellingen/statistiek weg."""
    await Store(hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()
