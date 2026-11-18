"""Coordinator: kiest de juiste databron o.b.v. de gekozen leverancier."""

from __future__ import annotations

from datetime import timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_SUPPLIER,
    DOMAIN,
    LOGGER,
    PLATFORM_CUSTOM,
    PLATFORM_EASYENERGY,
    PLATFORM_ENERGYZERO,
    PLATFORM_EON_APP,
    PLATFORM_FRANK,
    supplier_by_key,
)
from .const import CONF_EPEX_SOURCE, DEFAULT_EPEX_SOURCE, ELECTRICITY
from .model import PriceData, bucket_by_day
from .tariffs import legacy_periods, periods_from_options
from .sources import (
    EpexMap,
    SourceError,
    _epex_source,
    price_with_periods,
    fetch_easyenergy,
    fetch_energyzero,
    fetch_eon_app,
    fetch_epex,
    fetch_frank,
)

type DynTarNLConfigEntry = ConfigEntry[DynTarNLCoordinator]


def _fill_market_from_epex(data: PriceData, epex: EpexMap) -> None:
    """Vul de beurs-waarde uit de EPEX voor slots zonder eigen breakdown."""
    for energy, ed in data.items():
        emap = epex.get(energy, {})
        for slots in (ed.yesterday, ed.today, ed.tomorrow):
            for s in slots or []:
                if s.has_breakdown:
                    continue
                market_ex = emap.get(s.start.isoformat())
                if market_ex is not None:
                    s.market_ex = market_ex
                    s.market = round(market_ex * (1 + ed.vat_percentage / 100), 6)


def _needs_epex_fallback(data: PriceData) -> bool:
    """True als een bron geen eigen beursprijs levert (dan pas EPEX ophalen)."""
    for ed in data.values():
        for slots in (ed.today, ed.tomorrow, ed.yesterday):
            for s in slots or []:
                # Niet op market_ex == 0 testen: een beursprijs van precies 0 is echt.
                if not s.has_breakdown:
                    return True
    return False


class DynTarNLCoordinator(DataUpdateCoordinator[PriceData]):
    """Haalt de tarieven van de gekozen leverancier op.

    Het ophalen wordt door __init__.py aangestuurd (opstart, na middernacht en in de
    middag); het interval staat daarom op None. De EPEX-beursprijs wordt alleen als
    fallback opgehaald wanneer de gekozen bron zelf geen beursprijs levert.
    """

    config_entry: DynTarNLConfigEntry

    def __init__(self, hass: HomeAssistant, entry: DynTarNLConfigEntry) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=None,
        )
        self._session = async_get_clientsession(hass)
        self.supplier = supplier_by_key(entry.data.get(CONF_SUPPLIER, ""))
        self.custom = self.supplier is not None and self.supplier.platform == PLATFORM_CUSTOM
        # Tariefperiodes (opties). Leeg bij Custom = de oude platte instellingen.
        periods = periods_from_options(entry.options)
        if not periods and self.custom:
            periods = legacy_periods({**entry.options, **entry.data})
        self.periods = periods
        self._cache: dict = {}
        # Optionele voorspel-coordinator (forecast/coordinator.py); None = uit.
        self.forecast = None

    def _forecast_records(self) -> list | None:
        fc = self.forecast
        return fc.data.merged if fc is not None and getattr(fc, "data", None) is not None else None

    def rows(self, energy: str):
        """Inkoop/verkoop per tijdslot (tables.build_rows), één keer per data-versie."""
        from homeassistant.util import dt as dt_util

        from .tables import build_rows

        records = self._forecast_records() if energy == ELECTRICITY else None
        now = dt_util.now()
        key = ("rows", energy, id(self.data), id(records), now.date())
        if key not in self._cache:
            self._cache = {k: v for k, v in self._cache.items() if k[0] != "rows" or k[1] != energy}
            ed = (self.data or {}).get(energy)
            supplier_key = self.supplier.key if self.supplier else ""
            self._cache[key] = build_rows(ed, energy, self.periods, supplier_key, records, now)
        return self._cache[key]

    def sell_data(self):
        """Verkoopprijzen (gepubliceerd) als EnergyData: zelfde vorm als de inkoop, voor de
        afgeleide sensoren (nu, vorige, volgende, min/gem/max)."""
        from .model import EnergyData
        from .tariffs import sell_slots

        ed = (self.data or {}).get(ELECTRICITY)
        key = ("sell", id(self.data))
        if key not in self._cache:
            self._cache = {k: v for k, v in self._cache.items() if k[0] != "sell"}
            self._cache[key] = (
                None
                if ed is None
                else EnergyData(
                    unit=ed.unit,
                    vat_percentage=ed.vat_percentage,
                    today=sell_slots(ed.today, self.periods),
                    tomorrow=sell_slots(ed.tomorrow, self.periods) if ed.tomorrow else None,
                    yesterday=sell_slots(ed.yesterday, self.periods) if ed.yesterday else None,
                )
            )
        return self._cache[key]

    async def async_refresh_all(self) -> None:
        """Handmatig verversen (knop/service): de prijzen én — als die aan staan — direct de
        voorspellingen, buiten hun eigen 6-uursschema om. Alleen op verzoek van de gebruiker,
        dus geen extra belasting van de (gratis) voorspellers."""
        await self.async_request_refresh()
        if self.forecast is not None:
            await self.forecast.async_tick(force=True)

    def async_rebucket(self) -> None:
        """Nieuwe dag: gisteren/vandaag/morgen opnieuw indelen uit de cache (geen netwerk).

        Zo kloppen de sensoren direct na middernacht, ook als de fetch van 00:00 mislukt.
        """
        if not self.data:
            return
        self.data = {
            energy: bucket_by_day(
                (ed.yesterday or []) + ed.today + (ed.tomorrow or []), ed.unit, ed.vat_percentage
            )
            for energy, ed in self.data.items()
        }
        self.async_update_listeners()

    async def _async_update_data(self) -> PriceData:
        supplier = self.supplier
        if supplier is None:
            raise UpdateFailed("Onbekende leverancier geconfigureerd")

        try:
            if supplier.platform == PLATFORM_EON_APP:
                data = await fetch_eon_app(self._session, supplier.host or "")
            elif supplier.platform == PLATFORM_EASYENERGY:
                data = await fetch_easyenergy(self._session)
            elif supplier.platform == PLATFORM_FRANK:
                data = await fetch_frank(self._session)
            elif supplier.platform == PLATFORM_ENERGYZERO:
                data = await fetch_energyzero(self._session)
            elif supplier.platform == PLATFORM_CUSTOM:
                # Alleen de kale beurs van de gekozen bron; de tarieven komen uit de periodes.
                cfg = {**self.config_entry.options, **self.config_entry.data}
                data = await _epex_source(self._session, cfg.get(CONF_EPEX_SOURCE, DEFAULT_EPEX_SOURCE))
            else:
                raise UpdateFailed(
                    f"Platform '{supplier.platform}' is nog niet geïmplementeerd"
                )

            # EPEX alleen ophalen als de bron zelf geen beursprijs bevat.
            if _needs_epex_fallback(data):
                epex = await fetch_epex(self._session)
                _fill_market_from_epex(data, epex)
        except (aiohttp.ClientError, TimeoutError, SourceError) as err:
            raise UpdateFailed(f"Fout bij ophalen tarieven: {err}") from err
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            # Onverwachte vorm van het antwoord (bijv. null, lijst i.p.v. object, kapotte
            # JSON): nette fout i.p.v. een traceback; de cache blijft staan.
            raise UpdateFailed(f"Onverwacht antwoord van de bron: {type(err).__name__}: {err}") from err

        if not data:
            raise UpdateFailed("Geen tarieven ontvangen")
        # Een half antwoord (bijv. wel gas, geen stroom) of een antwoord zonder 'vandaag'
        # mag de goede gecachte prijzen niet vervangen: als mislukt behandelen, dan blijft
        # de cache staan en wordt het elk uur opnieuw geprobeerd.
        if missing := [energy for energy in (self.data or {}) if energy not in data]:
            raise UpdateFailed(f"Onvolledig antwoord: geen {', '.join(missing)}")
        if empty := [energy for energy, ed in data.items() if not ed.today]:
            raise UpdateFailed(f"Geen prijzen voor vandaag: {', '.join(empty)}")
        # Tariefperiode per slot: Custom-formule of contracttarief. Een API-leverancier met
        # een dynamisch contract blijft ongewijzigd.
        if self.custom or any(not p.is_dynamic for p in self.periods):
            data = price_with_periods(data, self.periods, self.custom)
        return data
