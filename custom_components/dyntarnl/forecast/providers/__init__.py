"""Register van voorspelbronnen. Nieuwe bron = één regel hier."""

from __future__ import annotations

from .base import ForecastProvider, ProviderAuthError, ProviderError
from .energypriceforecast import EnergyPriceForecastProvider
from .epexpredictor import EpexPredictorProvider

PROVIDERS: dict[str, type[ForecastProvider]] = {
    cls.key: cls for cls in (EpexPredictorProvider, EnergyPriceForecastProvider)
}

__all__ = ["PROVIDERS", "ForecastProvider", "ProviderAuthError", "ProviderError"]
