"""Energy Price Forecast EU (https://energypriceforecast.eu), markt NL.

GET https://api.energypriceforecast.eu/api/v1/home-assistant/prices
    ?country=nl&hours=H&price_mode=base     (optioneel: Authorization: Bearer <key>)
→ {"format": "home-assistant-prices", "country": "NL", "unit": "EUR/kWh",
   "entries": [{"start": "...Z", "end": "...Z", "value": 0.18, "source":
   "day_ahead"|"forecast", "native_resolution_minutes": 60,
   "expansion_method": "repeat_hourly"}, ...],
   "meta": {"allowed_horizon_hours": 48, "api_key_state": "missing", ...}}

Er is geen gepubliceerde OpenAPI-spec; dit is het endpoint dat de eigen HA-integratie
van de maker gebruikt. Daarom controleren we `format` en `country` en parsen we
defensief. `price_mode=base` = kale beursprijs (nooit de retail-modus van de bron).
Zonder key maximaal 48 uur, met key tot 120 uur.
"""

from __future__ import annotations

import aiohttp
import voluptuous as vol

from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from ...const import CONF_FC_EPF_API_KEY, EPF_URL, PROVIDER_EPF
from ..model import ForecastPoint
from .base import ForecastProvider, ProviderAuthError, ProviderError, parse_utc, to_quarters

_REJECTED_KEY_STATES = frozenset({"invalid", "invalid_format", "revoked", "inactive", "expired"})
_UNIT_FACTOR = {"EUR/kWh": 1.0, "EUR/MWh": 0.001}


class EnergyPriceForecastProvider(ForecastProvider):
    key = PROVIDER_EPF
    name = "Energy Price Forecast EU"
    option_keys = (CONF_FC_EPF_API_KEY,)
    max_horizon_hours = 120

    @property
    def api_key(self) -> str:
        return str(self.options.get(CONF_FC_EPF_API_KEY) or "").strip()

    def option_fields(self) -> dict:
        return {
            # suggested_value i.p.v. default: anders vult HA een leeggemaakt veld
            # weer met de oude key en kun je hem nooit meer weghalen.
            vol.Optional(CONF_FC_EPF_API_KEY, description={"suggested_value": self.api_key}): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            )
        }

    async def async_fetch(
        self, session: aiohttp.ClientSession, horizon_hours: int
    ) -> list[ForecastPoint]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        params = {
            "country": "nl",
            "hours": str(min(horizon_hours, self.max_horizon_hours)),
            "price_mode": "base",
        }
        payload = await self._get_json(session, EPF_URL, params=params, headers=headers)

        if payload.get("format") != "home-assistant-prices":
            raise ProviderError("Energy Price Forecast: onverwacht antwoordformaat")
        if str(payload.get("country", "")).upper() != "NL":
            raise ProviderError("Energy Price Forecast: verkeerde markt in antwoord")
        meta = payload.get("meta") or {}
        if self.api_key and meta.get("api_key_state") in _REJECTED_KEY_STATES:
            raise ProviderAuthError(f"API-key afgewezen ({meta.get('api_key_state')})")
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise ProviderError("Energy Price Forecast: 'entries' ontbreekt")

        default_unit = payload.get("unit", "EUR/kWh")
        quarter_rows, hourly_rows = [], []
        forecast_starts = set()
        for e in entries:
            if e.get("source") != "forecast":
                continue  # day_ahead = al gepubliceerd, geen voorspelling
            factor = _UNIT_FACTOR.get(e.get("unit", default_unit))
            if factor is None:
                raise ProviderError(f"Onbekende eenheid: {e.get('unit', default_unit)}")
            price = float(e["value"]) * factor
            forecast_starts.add(parse_utc(e["start"]))
            native = int(e.get("native_resolution_minutes") or 15)
            if native >= 60 or e.get("expansion_method") == "repeat_hourly":
                # De bron herhaalt het uur zelf al over vier kwartieren; wij nemen het
                # uur één keer en verdelen het zelf, zodat het als `expanded` telt.
                native_start = parse_utc(e.get("native_start") or e["start"])
                hourly_rows.append((native_start, price))
            else:
                quarter_rows.append((parse_utc(e["start"]), price))

        points = {p.start: p for p in to_quarters(hourly_rows, native_minutes=60)}
        points.update({p.start: p for p in to_quarters(quarter_rows, native_minutes=15)})
        # Alleen kwartieren die de bron zelf als voorspelling markeert (een uur kan
        # half day-ahead, half voorspelling zijn).
        return [points[k] for k in sorted(points) if k in forecast_starts]
