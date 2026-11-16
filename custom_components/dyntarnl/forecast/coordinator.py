"""Aparte coordinator voor voorspellingen, volledig los van de gewone prijzen.

- De gewone coordinator wacht nooit op deze; voorspelde data komt nooit in
  `coordinator.data` terecht.
- Elke provider heeft een eigen time-out, eigen foutafhandeling en eigen
  exponentiële backoff. Eén kapotte bron raakt de andere niet.
- Na elke update van de gewone prijzen wordt (zonder netwerk) opnieuw
  samengevoegd en afgerekend.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from ..const import (
    DOMAIN,
    ELECTRICITY,
    ENSEMBLE,
    FC_BACKOFF_MAX_MIN,
    FC_BACKOFF_START_MIN,
    FC_TIMEOUT,
    LOGGER,
)
from .accuracy import STORAGE_VERSION, AccuracyTracker
from .engine import ForecastData, all_slots, build, ensemble_snapshot, published_records
from .model import QUARTER, ForecastConfig, ForecastPoint, ProviderSeries
from .providers import PROVIDERS, ForecastProvider

# Een fetch die net iets te vroeg valt (tick om de 15 min) telt toch als op tijd.
_DUE_TOLERANCE = timedelta(minutes=5)
_SAVE_DELAY = 60


def storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.forecast_{entry_id}"


def backoff(failures: int) -> timedelta:
    """30 min, 1 u, 2 u, … tot maximaal 12 u."""
    minutes = FC_BACKOFF_START_MIN * 2 ** max(failures - 1, 0)
    return timedelta(minutes=min(minutes, FC_BACKOFF_MAX_MIN))


@dataclass(slots=True)
class ProviderStatus:
    last_attempt: datetime | None = None
    last_success: datetime | None = None
    last_error: str | None = None
    failures: int = 0
    next_attempt: datetime | None = None

    @property
    def state(self) -> str:
        if self.failures:
            return "backoff"
        return "ok" if self.last_success else "pending"


class DynTarNLForecastCoordinator(DataUpdateCoordinator[ForecastData | None]):
    """Haalt voorspellingen op volgens een eigen, zuinig schema (zie `async_tick`)."""

    def __init__(self, hass: HomeAssistant, entry, prices, cfg: ForecastConfig) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_forecast",
            update_interval=None,
        )
        self.data = None
        self.prices = prices
        self.cfg = cfg
        self.supplier_name = prices.supplier.name if prices.supplier else ""
        self.providers: dict[str, ForecastProvider] = {
            key: PROVIDERS[key](cfg.options) for key in cfg.providers if key in PROVIDERS
        }
        self.series: dict[str, ProviderSeries] = {}
        self.status: dict[str, ProviderStatus] = {k: ProviderStatus() for k in self.providers}
        self.tracker = AccuracyTracker(cfg.window_days) if cfg.accuracy else None
        self._session = async_get_clientsession(hass)
        self._store = Store(hass, STORAGE_VERSION, storage_key(entry.entry_id))
        self._tick_lock = asyncio.Lock()
        # Opgeslagen statistiek bewaren als de meting (tijdelijk) uit staat, zodat
        # 'nauwkeurigheid uit' de opgebouwde historie niet weggooit.
        self._stored_accuracy: dict | None = None

    # --- ophalen -----------------------------------------------------------

    def is_due(self, key: str, now: datetime) -> bool:
        status = self.status[key]
        if status.next_attempt is not None and now < status.next_attempt:
            return False
        series = self.series.get(key)
        interval = timedelta(hours=self.cfg.interval_hours)
        return series is None or now - series.fetched_at >= interval - _DUE_TOLERANCE

    async def async_tick(self, force: bool = False) -> None:
        """Haal op bij providers die aan de beurt zijn. Faalt nooit naar buiten."""
        if self._tick_lock.locked():
            return  # er loopt al een ronde (bijv. opstart + eerste kwartier-tik)
        async with self._tick_lock:
            now = dt_util.utcnow()
            due = [k for k in self.providers if force or self.is_due(k, now)]
            if not due:
                return
            results = await asyncio.gather(*(self._fetch_one(k, now) for k in due))
            fetched = [k for k, ok in zip(due, results) if ok]
            self.rebuild(snapshot=fetched)

    async def _fetch_one(self, key: str, now: datetime) -> bool:
        status = self.status[key]
        status.last_attempt = now
        try:
            async with asyncio.timeout(FC_TIMEOUT):
                points = await self.providers[key].async_fetch(self._session, self.cfg.horizon_hours)
            if not points:
                raise ValueError("lege voorspelling")
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - een provider mag nooit iets breken
            status.failures += 1
            status.last_error = f"{type(err).__name__}: {err}"
            status.next_attempt = now + backoff(status.failures)
            LOGGER.warning(
                "Voorspelling van %s mislukt (%s); volgende poging na %s",
                key, status.last_error, status.next_attempt.isoformat(),
            )
            return False
        status.failures = 0
        status.last_error = None
        status.next_attempt = None
        status.last_success = now
        self.series[key] = ProviderSeries(key, now, points)
        return True

    async def _async_update_data(self) -> ForecastData | None:
        await self.async_tick(force=True)
        return self.data

    # --- samenvoegen / afrekenen (geen netwerk) -----------------------------

    @callback
    def handle_prices_update(self) -> None:
        """Listener op de gewone coordinator: opnieuw samenvoegen en afrekenen."""
        self.rebuild()

    @callback
    def rebuild(self, snapshot: list[str] | tuple[str, ...] = ()) -> None:
        try:
            now = dt_util.utcnow()
            prices = self.prices.data or {}
            # Kwartieren met een gepubliceerde prijs zijn geen voorspelling meer: niet als
            # snapshot bewaren, anders tellen ze (met een verkeerde looptijd) mee in de MAE.
            known = {r.start for r in published_records(prices.get(ELECTRICITY), "")}
            if self.tracker is not None:
                for key in snapshot:
                    s = self.series[key]
                    self.tracker.add_snapshot(
                        key, s.fetched_at, ((p.start, p.price_raw, None) for p in s.points if p.start not in known)
                    )
                self.tracker.settle(all_slots(prices.get(ELECTRICITY)), now)
            data, combined = build(prices, self.series, self.tracker, self.cfg, self.supplier_name, now)
            if self.tracker is not None and snapshot and combined:
                self.tracker.add_snapshot(
                    ENSEMBLE, now, [x for x in ensemble_snapshot(combined) if x[0] not in known]
                )
            self._store.async_delay_save(self._data_to_save, _SAVE_DELAY)
        except Exception:  # noqa: BLE001 - nooit de listener-keten van de prijzen breken
            LOGGER.exception("Voorspellingen samenvoegen mislukt")
            return
        self.async_set_updated_data(data)

    # --- opslag ------------------------------------------------------------

    def _data_to_save(self) -> dict:
        series = {}
        for key, s in self.series.items():
            if not s.points:
                continue
            first = s.points[0].start
            length = int((s.points[-1].start - first) / QUARTER) + 1
            values: list = [None] * length
            expanded: list = [0] * length
            for p in s.points:
                i = int((p.start - first) / QUARTER)
                values[i] = round(p.price_raw, 6)
                expanded[i] = int(p.expanded)
            series[key] = {
                "f": s.fetched_at.isoformat(),
                "s": first.isoformat(),
                "v": values,
                "e": expanded,
                "i": self.providers[key].info if key in self.providers else {},
            }
        return {
            "series": series,
            "accuracy": self.tracker.to_dict() if self.tracker is not None else self._stored_accuracy,
        }

    async def async_load(self) -> None:
        """Herstel reeksen en statistiek na een herstart (scheelt calls)."""
        try:
            stored = await self._store.async_load() or {}
        except Exception:  # noqa: BLE001
            LOGGER.exception("Opgeslagen voorspellingen konden niet worden gelezen")
            stored = {}
        for key, s in (stored.get("series") or {}).items():
            if key not in self.providers:
                continue
            try:
                first = dt_util.as_utc(dt_util.parse_datetime(s["s"]))
                points = [
                    ForecastPoint(first + i * QUARTER, v, bool(e))
                    for i, (v, e) in enumerate(zip(s["v"], s["e"]))
                    if v is not None
                ]
                fetched_at = dt_util.as_utc(dt_util.parse_datetime(s["f"]))
            except Exception:  # noqa: BLE001 - een kapotte reeks overslaan, opnieuw ophalen
                LOGGER.warning("Opgeslagen voorspelling van %s overgeslagen (onleesbaar)", key)
                continue
            self.series[key] = ProviderSeries(key, fetched_at, points)
            # Wat de bron bij die fetch over zichzelf meldde (plan, horizon, …) ook terug.
            self.providers[key].info = dict(s.get("i") or {})
            # Anders blijft de last_fetch-sensor na een herstart leeg tot de volgende
            # fetch (tot 6 uur later), terwijl de reeks gewoon vers is.
            self.status[key].last_success = fetched_at
        self._stored_accuracy = stored.get("accuracy")
        if self.tracker is not None:
            try:
                self.tracker.load(self._stored_accuracy)
            except Exception:  # noqa: BLE001
                LOGGER.warning("Opgeslagen nauwkeurigheid onleesbaar; begint opnieuw")
        self.rebuild()

    async def async_flush(self) -> None:
        """Bij unload: nu opslaan. Anders schrijft een nog lopende vertraagde save (tot 60 s)
        na het verwijderen het opslagbestand terug, of overschrijft hij na een reload de
        nieuwere data."""
        await self._store.async_save(self._data_to_save())

    async def async_start(self) -> None:
        """Eerste run (achtergrondtaak): opslag laden, dan ophalen wat nodig is."""
        await self.async_load()
        await self.async_tick()
