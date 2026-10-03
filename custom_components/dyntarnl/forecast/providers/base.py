"""Provider-abstractie: één klasse per externe voorspelbron.

Een nieuwe bron toevoegen = een subklasse van `ForecastProvider` schrijven en hem in
`providers/__init__.py` aan `PROVIDERS` toevoegen (plus vertalingen voor zijn
opties). De rest van de voorspellaag kent alleen deze interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta

import aiohttp

from homeassistant.util import dt as dt_util

from ...const import FC_TIMEOUT
from ..model import QUARTER, ForecastPoint, floor_quarter

_TIMEOUT = aiohttp.ClientTimeout(total=FC_TIMEOUT)


class ProviderError(Exception):
    """De bron gaf iets terug waar we niets mee kunnen."""


class ProviderAuthError(ProviderError):
    """API-key afgewezen."""


def parse_utc(value: str) -> datetime:
    parsed = dt_util.parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        raise ProviderError(f"Ongeldig of tijdzoneloos tijdstip: {value!r}")
    return dt_util.as_utc(parsed)


def to_quarters(
    rows: Iterable[tuple[datetime, float]], native_minutes: int | None = None
) -> list[ForecastPoint]:
    """Normaliseer (start, prijs)-paren naar kwartierpunten.

    Levert een bron uurwaarden (expliciet via `native_minutes=60`, of afgeleid uit de
    stapgrootte), dan wordt elk uur over vier kwartieren verdeeld en gemarkeerd als
    `expanded`. Dubbele kwartieren: de laatste wint.
    """
    rows = sorted(rows, key=lambda r: r[0])
    if native_minutes is None:
        steps = {b[0] - a[0] for a, b in zip(rows, rows[1:])}
        native_minutes = 60 if steps and min(steps) >= timedelta(hours=1) else 15

    points: dict[datetime, ForecastPoint] = {}
    for start, price in rows:
        start = floor_quarter(start)
        if native_minutes >= 60:
            for i in range(4):
                q = start + i * QUARTER
                points[q] = ForecastPoint(q, price, expanded=True)
        else:
            points[start] = ForecastPoint(start, price)
    return [points[k] for k in sorted(points)]


class ForecastProvider(ABC):
    """Basis voor een voorspelbron. Instanties zijn goedkoop; per fetch hoeft niets."""

    key: str = ""
    name: str = ""
    #: Opties die deze provider zelf in entry.options gebruikt (met default in
    #: FORECAST_DEFAULTS). De options flow toont ze in de stap 'provider_settings'.
    option_keys: tuple[str, ...] = ()
    #: Maximale horizon die de bron kan leveren (uren).
    max_horizon_hours: int = 168

    def __init__(self, options: Mapping) -> None:
        self.options = options

    def option_fields(self) -> dict:
        """voluptuous-velden voor de options flow (leeg = geen eigen instellingen)."""
        return {}

    @abstractmethod
    async def async_fetch(
        self, session: aiohttp.ClientSession, horizon_hours: int
    ) -> list[ForecastPoint]:
        """Haal de voorspelling op: alleen ECHTE voorspellingen, kale EPEX in
        €/kWh excl. btw, per kwartier in UTC. Day-ahead-waarden die de bron zelf
        meelevert horen hier niet bij (gepubliceerd komt altijd van de leverancier)."""

    async def async_validate(self, session: aiohttp.ClientSession) -> str | None:
        """Test-call voor de options flow; geeft een fout-sleutel of None."""
        try:
            await self.async_fetch(session, 24)
        except ProviderAuthError:
            return "invalid_api_key"
        except Exception:  # noqa: BLE001 - elke fout = niet bereikbaar
            return "cannot_connect"
        return None

    async def _get_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> dict:
        async with session.get(url, params=params, headers=headers, timeout=_TIMEOUT) as resp:
            if resp.status in (401, 403):
                raise ProviderAuthError(f"HTTP {resp.status}")
            resp.raise_for_status()
            payload = await resp.json(content_type=None)
        if not isinstance(payload, dict):
            raise ProviderError("Antwoord is geen JSON-object")
        return payload
