"""EpexPredictor (https://github.com/b3nn0/EpexPredictor), regio NL.

GET {url}/prices?region=NL&unit=EUR_PER_MWH&timezone=UTC&hours=H
→ {"prices": [{"startsAt": "...Z", "total": 87.3}, ...], "knownUntil": "...Z"}

Alles t/m `knownUntil` is de al gepubliceerde day-ahead; dat slaan we over. De
opslag-/btw-parameters van de API (`surcharge`, `taxPercent`) sturen we nooit mee:
we willen de kale prijs en passen zelf de leveranciersformule toe.
"""

from __future__ import annotations

import aiohttp
import voluptuous as vol

from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from ...const import CONF_FC_EPEXPREDICTOR_URL, EPEXPREDICTOR_URL, PROVIDER_EPEXPREDICTOR
from ..model import ForecastPoint
from .base import ForecastProvider, ProviderError, parse_utc, to_quarters


class EpexPredictorProvider(ForecastProvider):
    key = PROVIDER_EPEXPREDICTOR
    name = "EpexPredictor"
    option_keys = (CONF_FC_EPEXPREDICTOR_URL,)
    max_horizon_hours = 168

    @property
    def base_url(self) -> str:
        url = str(self.options.get(CONF_FC_EPEXPREDICTOR_URL) or EPEXPREDICTOR_URL).strip()
        return url.rstrip("/")

    def option_fields(self) -> dict:
        return {
            vol.Required(CONF_FC_EPEXPREDICTOR_URL, default=self.base_url): TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)
            )
        }

    async def async_fetch(
        self, session: aiohttp.ClientSession, horizon_hours: int
    ) -> list[ForecastPoint]:
        params = {
            "region": "NL",
            "unit": "EUR_PER_MWH",
            "timezone": "UTC",
            "hours": str(min(horizon_hours, self.max_horizon_hours)),
        }
        payload = await self._get_json(session, f"{self.base_url}/prices", params=params)
        prices = payload.get("prices")
        if not isinstance(prices, list):
            raise ProviderError("EpexPredictor: 'prices' ontbreekt")
        known_until = payload.get("knownUntil")
        known = parse_utc(known_until) if known_until else None

        rows = []
        for row in prices:
            start = parse_utc(row["startsAt"])
            if known is not None and start <= known:
                continue  # echte day-ahead, geen voorspelling
            rows.append((start, float(row["total"]) / 1000))  # €/MWh → €/kWh
        # Resolutie afleiden: de publieke instantie levert kwartieren, maar een
        # oudere self-hosted versie kan uurwaarden geven.
        return to_quarters(rows)
