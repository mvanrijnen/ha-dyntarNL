"""Laadkosten EV: het verbruik van een lader afrekenen tegen de all-in prijs.

Optioneel (CONF_EV_SENSOR leeg = uit). Werkt met twee soorten sensoren, herkend aan
de eenheid:
- een kWh-teller (Wh/kWh/MWh), totaal of per sessie: de toename tussen twee standen
  wordt afgerekend. Valt de teller terug (nieuwe sessie), dan begint ook de sessie
  hier opnieuw en telt de nieuwe stand als verbruik sinds de reset;
- een vermogen-sensor (W/kW): vermogen × tijd, stapsgewijs geïntegreerd (de laatste
  waarde geldt tot de volgende), elke 5 minuten bijgewerkt. Een sessie begint als er
  na minstens SESSION_GAP stilstand weer geladen wordt.

Een interval wordt over kwartiergrenzen verdeeld en elk stuk krijgt de all-in prijs
van zijn eigen kwartier. Totalen per sessie, dag, maand, kwartaal en jaar (lokale tijd)
en in totaal; elk is te resetten. Alles staat in een HA Store.

Alles hier is los van het ophalen van de prijzen: een fout wordt gelogd en raakt de
prijssensoren nooit.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, ELECTRICITY, LOGGER
from .prices import slot_at

ENERGY_UNITS = {"Wh": 0.001, "kWh": 1.0, "MWh": 1000.0}   # → kWh
POWER_UNITS = {"W": 0.001, "kW": 1.0}                     # → kW
PERIODS = ("day", "month", "quarter", "year")
SESSION = "session"
TOTAL = "total"
ALL_KEYS = (SESSION, *PERIODS, TOTAL)
SESSION_GAP = timedelta(minutes=30)   # vermogen: zo lang 0 = volgende keer nieuwe sessie
STORAGE_VERSION = 1
_SAVE_DELAY = 30
_STEP = timedelta(minutes=15)

type PriceFn = Callable[[datetime], float | None]


def storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.ev_{entry_id}"


def period_key(moment: datetime, period: str) -> str:
    local = dt_util.as_local(moment)
    if period == "day":
        return local.strftime("%Y-%m-%d")
    if period == "month":
        return local.strftime("%Y-%m")
    if period == "quarter":
        return f"{local.year}-Q{(local.month - 1) // 3 + 1}"
    return local.strftime("%Y")


def period_start(moment: datetime, period: str) -> datetime:
    local = dt_util.as_local(moment).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        local = local.replace(day=1)
    elif period == "quarter":
        local = local.replace(day=1, month=(local.month - 1) // 3 * 3 + 1)
    elif period == "year":
        local = local.replace(day=1, month=1)
    return local


def _next_boundary(moment: datetime) -> datetime:
    base = moment.replace(minute=moment.minute - moment.minute % 15, second=0, microsecond=0)
    return base + _STEP


def _empty() -> dict:
    return {"cost": 0.0, "kwh": 0.0, "unpriced_kwh": 0.0}


MAX_SESSIONS = 100


def _session(start: str | None) -> dict:
    return {
        # Lokale starttijd als id, bijv. "2026-10-04 18:05" (zo staat hij ook op de kaart).
        "id": dt_util.as_local(dt_util.parse_datetime(start)).strftime("%Y-%m-%d %H:%M") if start else None,
        "start": start,
        "end": None,
        "excluded": False,                      # verwijderd terwijl hij nog liep
        "contrib": {},                          # periode -> sleutel -> [kWh, kosten, zonder prijs]
        **_empty(),
    }


class EvCostTracker:
    """Pure boekhouding: kWh over een interval verdelen en afrekenen.

    Elke sessie onthoudt per periode wat hij heeft bijgedragen, zodat een sessie
    later (bijv. een andere auto) precies weer van alle tellers af kan.
    """

    def __init__(self) -> None:
        self.periods: dict[str, dict] = {p: {"key": None, "reset_at": None, **_empty()} for p in PERIODS}
        self.session: dict = _session(None)
        self.history: list[dict] = []   # afgesloten sessies, nieuwste laatst
        self.total: dict = {"reset_at": None, **_empty()}

    # --- boeken -----------------------------------------------------------------

    def start_session(self, moment: datetime) -> None:
        current = self.session
        if current["start"] and not current["excluded"] and current["kwh"]:
            self.history = [*self.history, current][-MAX_SESSIONS:]
        self.session = _session(dt_util.as_utc(moment).isoformat())

    def add(self, start: datetime, end: datetime, kwh: float, price_fn: PriceFn) -> None:
        """Verdeel `kwh` gelijkmatig over [start, end) en reken per kwartier af."""
        if kwh == 0:
            return
        if self.session["start"] is None:
            self.start_session(start)
        if self.session["excluded"]:
            return  # deze sessie is verwijderd (andere auto): niets meer boeken
        if end <= start:
            self._book(end, kwh, price_fn(end))
            return
        seconds = (end - start).total_seconds()
        moment = start
        while moment < end:
            nxt = min(end, _next_boundary(moment))
            share = kwh * (nxt - moment).total_seconds() / seconds
            self._book(moment, share, price_fn(moment))
            moment = nxt

    def _book(self, moment: datetime, kwh: float, price: float | None) -> None:
        session = self.session
        for period in PERIODS:
            bucket = self.periods[period]
            key = period_key(moment, period)
            if bucket["key"] != key:
                if bucket["key"] is not None and key < bucket["key"]:
                    continue  # verbruik uit een al afgesloten periode: niet terugboeken
                bucket.update({"key": key, "reset_at": None, **_empty()})
            self._apply(bucket, kwh, price)
            part = session["contrib"].setdefault(period, {}).setdefault(key, [0.0, 0.0, 0.0])
            part[0] += kwh
            part[1] += 0.0 if price is None else kwh * price
            part[2] += kwh if price is None else 0.0
        self._apply(session, kwh, price)
        self._apply(self.total, kwh, price)
        session["end"] = dt_util.as_utc(moment).isoformat()

    @staticmethod
    def _apply(bucket: dict, kwh: float, price: float | None) -> None:
        bucket["kwh"] += kwh
        if price is None:
            bucket["unpriced_kwh"] += kwh  # geen prijs bekend voor dat moment
        else:
            bucket["cost"] += kwh * price

    # --- sessie verwijderen -------------------------------------------------------

    def sessions(self) -> list[dict]:
        """Sessies (afgesloten + lopende), nieuwste eerst, zonder interne details."""
        out = []
        for s in [*self.history, self.session][::-1]:
            if s["start"] and not s["excluded"]:
                out.append({k: s[k] for k in ("id", "start", "end", "kwh", "cost", "unpriced_kwh")})
        return out

    def delete_session(self, session_id: str) -> dict:
        """Haal een sessie van alle tellers af. 'last' = de lopende of laatste sessie.

        Een periode (of het totaal) die ná de start van de sessie handmatig is gereset,
        bevat die sessie niet meer en blijft dus ongemoeid. Geeft de verwijderde sessie
        terug; ValueError als hij niet bestaat.
        """
        candidates = [s for s in [*self.history, self.session] if s["start"] and not s["excluded"]]
        if session_id == "last":
            with_energy = [s for s in candidates if s["kwh"]]
            target = with_energy[-1] if with_energy else None
        else:
            target = next((s for s in candidates if s["id"] == session_id), None)
        if target is None:
            raise ValueError(f"Onbekende laadsessie: {session_id}")

        started = target["start"]
        for period, parts in target["contrib"].items():
            bucket = self.periods[period]
            if bucket["reset_at"] and bucket["reset_at"] >= started:
                continue
            part = parts.get(bucket["key"])
            if part:
                self._subtract(bucket, part)
        if not (self.total["reset_at"] and self.total["reset_at"] >= started):
            self._subtract(self.total, [target["kwh"], target["cost"], target["unpriced_kwh"]])

        removed = {k: target[k] for k in ("id", "start", "end", "kwh", "cost", "unpriced_kwh")}
        if target is self.session:
            # Loopt nog: de rest van deze sessie telt ook niet mee.
            self.session = {**_session(target["start"]), "excluded": True}
        else:
            self.history = [s for s in self.history if s is not target]
        return removed

    @staticmethod
    def _subtract(bucket: dict, part: list[float]) -> None:
        bucket["kwh"] = max(0.0, bucket["kwh"] - part[0])
        bucket["cost"] -= part[1]
        bucket["unpriced_kwh"] = max(0.0, bucket["unpriced_kwh"] - part[2])

    # --- uitlezen / resetten ----------------------------------------------------

    def value(self, key: str, now: datetime) -> dict:
        """Totalen voor `key` (0 als de periode inmiddels verstreken is)."""
        if key == TOTAL:
            return dict(self.total)
        if key == SESSION:
            return {k: self.session[k] for k in ("start", "end", *_empty())}
        bucket = self.periods[key]
        return dict(bucket) if bucket["key"] == period_key(now, key) else _empty()

    def last_reset(self, key: str, now: datetime) -> datetime | None:
        """Begin van de lopende periode, of het moment van een handmatige reset."""
        if key == SESSION:
            return dt_util.parse_datetime(self.session["start"]) if self.session["start"] else None
        if key == TOTAL:
            return dt_util.parse_datetime(self.total["reset_at"]) if self.total["reset_at"] else None
        start = period_start(now, key)
        bucket = self.periods[key]
        if bucket["key"] == period_key(now, key) and bucket["reset_at"]:
            return max(start, dt_util.as_local(dt_util.parse_datetime(bucket["reset_at"])))
        return start

    def reset(self, key: str, now: datetime) -> None:
        """Handmatig op 0 zetten; 'all' reset alles (de sessielijst blijft)."""
        stamp = dt_util.as_utc(now).isoformat()
        keys = ALL_KEYS if key == "all" else (key,)
        for k in keys:
            if k == SESSION:
                self.start_session(now)
            elif k == TOTAL:
                self.total = {"reset_at": stamp, **_empty()}
            else:
                self.periods[k] = {"key": period_key(now, k), "reset_at": stamp, **_empty()}

    # --- opslag -------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "periods": self.periods,
            "session": self.session,
            "history": self.history,
            "total": self.total,
        }

    def load(self, data: dict | None) -> None:
        if not data:
            return
        for period in PERIODS:
            if period in (data.get("periods") or {}):
                self.periods[period] = {"key": None, "reset_at": None, **_empty(), **data["periods"][period]}
        self.session = {**_session(None), **(data.get("session") or {})}
        self.history = list(data.get("history") or [])
        self.total = {"reset_at": None, **_empty(), **(data.get("total") or {})}


class EvCostManager:
    """Koppelt de sensor van de lader aan de tracker en de prijzen."""

    def __init__(self, hass: HomeAssistant, entry, prices, entity_id: str) -> None:
        self.hass = hass
        self.entry = entry
        self.prices = prices
        self.entity_id = entity_id
        self.tracker = EvCostTracker()
        self.mode: str | None = None          # "energy" / "power"
        self._last_energy: tuple[float, datetime] | None = None   # (kWh-stand, tijd)
        self._last_power: tuple[float, datetime] | None = None    # (kW, tijd)
        self._last_charging: datetime | None = None               # laatste moment met vermogen > 0
        self._warned_unit: str | None = None
        self._listeners: list[Callable[[], None]] = []
        self._store = Store(hass, STORAGE_VERSION, storage_key(entry.entry_id))

    # --- prijzen -------------------------------------------------------------

    def price_at(self, moment: datetime) -> float | None:
        ed = (self.prices.data or {}).get(ELECTRICITY)
        if ed is None:
            return None
        slots = (ed.yesterday or []) + ed.today + (ed.tomorrow or [])
        slot = slot_at(slots, moment)
        return slot.total if slot else None

    # --- verwerken -------------------------------------------------------------

    def process(self, state, now: datetime) -> None:
        """Verwerk een (nieuwe) toestand van de lader-sensor."""
        try:
            self._process(state, now)
        except Exception:  # noqa: BLE001 - laadkosten mogen nooit iets anders breken
            LOGGER.exception("Laadkosten bijwerken mislukt")
            return
        self._changed()

    def _process(self, state, now: datetime) -> None:
        value = None
        unit = None
        if state is not None:
            unit = state.attributes.get("unit_of_measurement")
            try:
                value = float(state.state)
            except (TypeError, ValueError):
                value = None  # unavailable / unknown

        if unit in POWER_UNITS or (value is None and self.mode == "power"):
            self.mode = "power"
            self._integrate(now)
            kw = None if value is None else value * POWER_UNITS[unit]
            if kw and kw > 0:
                if self._last_charging is None or now - self._last_charging >= SESSION_GAP:
                    if not (self._last_power and self._last_power[0] > 0):
                        self.tracker.start_session(now)
                self._last_charging = now
            self._last_power = None if kw is None else (kw, now)
            return
        if unit in ENERGY_UNITS:
            self.mode = "energy"
            if value is None:
                return  # teller tijdelijk weg: stand bewaren, later verder
            reading = value * ENERGY_UNITS[unit]
            if self._last_energy is not None:
                last, since = self._last_energy
                delta = reading - last
                if delta < 0:
                    # Teller is teruggevallen: nieuwe sessie (vanaf nu), en wat er sinds
                    # de reset al op de teller staat telt mee.
                    self.tracker.start_session(now)
                    delta = reading
                if delta > 0:
                    self.tracker.add(since, now, delta, self.price_at)
            self._last_energy = (reading, now)
            return
        if value is not None and unit != self._warned_unit:
            self._warned_unit = unit
            LOGGER.warning(
                "Laadkosten: %s heeft eenheid %r; verwacht W/kW of Wh/kWh", self.entity_id, unit
            )

    def _integrate(self, now: datetime) -> None:
        """Vermogen × tijd sinds de vorige meting (de laatste waarde geldt tot nu)."""
        if self._last_power is None:
            return
        kw, since = self._last_power
        if now > since and kw > 0:
            self.tracker.add(since, now, kw * (now - since).total_seconds() / 3600, self.price_at)
            self._last_charging = now
        self._last_power = (kw, now)

    @property
    def reading_kwh(self) -> float | None:
        """Laatste stand van de kWh-teller van de lader (om mee te vergelijken)."""
        return self._last_energy[0] if self._last_energy else None

    @callback
    def tick(self, now: datetime | None = None) -> None:
        """Periodiek: lopend vermogen tot nu afrekenen (en sensoren laten meerollen)."""
        try:
            self._integrate(dt_util.utcnow())
        except Exception:  # noqa: BLE001
            LOGGER.exception("Laadkosten bijwerken mislukt")
        self._changed()

    @callback
    def reset(self, key: str) -> None:
        self.tracker.reset(key, dt_util.utcnow())
        self._changed()

    @callback
    def delete_session(self, session_id: str) -> dict:
        removed = self.tracker.delete_session(session_id)
        self._changed()
        return removed

    # --- listeners / opslag ----------------------------------------------------

    def add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    @callback
    def _changed(self) -> None:
        self._store.async_delay_save(self._data_to_save, _SAVE_DELAY)
        for update in list(self._listeners):
            update()

    def _data_to_save(self) -> dict:
        data = {"tracker": self.tracker.to_dict(), "entity_id": self.entity_id}
        if self._last_energy is not None:
            data["last_energy"] = [self._last_energy[0], self._last_energy[1].isoformat()]
        return data

    async def async_load(self) -> None:
        try:
            stored = await self._store.async_load() or {}
        except Exception:  # noqa: BLE001
            LOGGER.exception("Opgeslagen laadkosten konden niet worden gelezen")
            stored = {}
        self.tracker.load(stored.get("tracker"))
        # Alleen een kWh-stand van dezelfde sensor mag over een herstart heen worden
        # doorgerekend; vermogen niet (wat er gebeurde terwijl HA uit stond is onbekend).
        last = stored.get("last_energy")
        if last and stored.get("entity_id") == self.entity_id:
            self._last_energy = (float(last[0]), dt_util.as_utc(dt_util.parse_datetime(last[1])))

    async def async_start(self) -> None:
        await self.async_load()

        @callback
        def _state_changed(event: Event) -> None:
            self.process(event.data.get("new_state"), dt_util.utcnow())

        self.entry.async_on_unload(
            async_track_state_change_event(self.hass, [self.entity_id], _state_changed)
        )
        self.entry.async_on_unload(
            async_track_time_change(self.hass, self.tick, minute=list(range(0, 60, 5)), second=30)
        )
        # Ook bij elke prijs-update (o.a. het uurlijkse meerollen) de sensoren bijwerken.
        self.entry.async_on_unload(self.prices.async_add_listener(self.tick))
        self.process(self.hass.states.get(self.entity_id), dt_util.utcnow())
