"""Constanten voor DynTarNederland — dynamische tarieven, meerdere leveranciers."""

from __future__ import annotations

import logging
from dataclasses import dataclass

DOMAIN = "dyntarnl"
NAME = "DynTarNL"

LOGGER = logging.getLogger(__package__)

# --- Platforms (databron-technieken) ---------------------------------------
PLATFORM_EON_APP = "eon_app"      # E.ON eon-app: volledige breakdown
PLATFORM_ENERGYZERO = "energyzero"  # EnergyZero: alleen marktprijs (EPEX-bron voor CUSTOM)
PLATFORM_FRANK = "frank"          # Frank Energie GraphQL: volledige breakdown
PLATFORM_EASYENERGY = "easyenergy"  # easyEnergy: volledige breakdown
PLATFORM_CUSTOM = "custom"        # eigen leverancier: EPEX + zelf ingevulde opslag/btw


@dataclass(frozen=True)
class Supplier:
    """Eén kiesbare leverancier; koppelt merk aan een platform onder water."""

    key: str          # opgeslagen configwaarde
    name: str         # weergavenaam in de dropdown
    platform: str
    host: str | None = None   # voor eon_app: het domein
    implemented: bool = True   # False = nog niet gebouwd (niet tonen)


# Registry: merk -> platform. De gebruiker kiest een merk; de plugin bepaalt
# het platform. Login-only leveranciers (Vattenfall, Eneco, …) worden afgevangen
# via de CUSTOM-optie (zelfde EPEX, eigen opslag/belasting).
SUPPLIERS: tuple[Supplier, ...] = (
    # eon-app — volledige breakdown (beurs + opslag + belasting)
    Supplier("essent", "Essent", PLATFORM_EON_APP, "www.essent.nl"),
    Supplier("energiedirect", "Energiedirect", PLATFORM_EON_APP, "www.energiedirect.nl"),
    # frank — volledige breakdown
    Supplier("frank", "Frank Energie", PLATFORM_FRANK),
    # easyenergy — volledige breakdown (beurs + opslag + belasting)
    Supplier("easyenergy", "easyEnergy", PLATFORM_EASYENERGY),
    # custom — EPEX + zelf ingevulde opslag/belasting/btw. Voor álle overige
    # leveranciers, incl. de EnergyZero-merken (ANWB, Coolblue, …) en Nieuwestroom:
    # die geven geen bruikbare opslag/all-in door (alleen een marktprijs), dus daar
    # is CUSTOM de juiste route.
    Supplier("custom", "Custom supplier (manual)", PLATFORM_CUSTOM),
)


def supplier_by_key(key: str) -> Supplier | None:
    return next((s for s in SUPPLIERS if s.key == key), None)


# --- Config keys ------------------------------------------------------------
CONF_SUPPLIER = "supplier"

# CUSTOM: alle bedragen exclusief btw; de plugin zet er de btw overheen.
CONF_VAT = "vat_percentage"
CONF_MARKUP_ELEC = "markup_electricity"   # opslag stroom €/kWh excl. btw
CONF_MARKUP_GAS = "markup_gas"            # opslag gas €/m³ excl. btw
CONF_TAX_ELEC = "energy_tax_electricity"  # energiebelasting stroom €/kWh excl. btw
CONF_TAX_GAS = "energy_tax_gas"           # energiebelasting gas €/m³ excl. btw

DEFAULT_VAT = 21.0
# Indicatieve NL-tarieven 2026 (excl. btw) als voorinvulling voor CUSTOM.
DEFAULT_TAX_ELEC = 0.09161
DEFAULT_TAX_GAS = 0.60066

# --- Tariefperiodes (opties): één bron van waarheid voor tarieven per ingangsdatum ---
# Elke periode geldt vanaf 00:00 (Europe/Amsterdam) op `start` tot de volgende periode.
CONF_PERIODS = "tariff_periods"

CONTRACT_DYNAMIC = "dynamic"
CONTRACT_FIXED = "fixed"
CONTRACT_VARIABLE = "variable"  # = vast, met tussentijdse wijzigingen als nieuwe periodes
CONTRACTS = (CONTRACT_DYNAMIC, CONTRACT_FIXED, CONTRACT_VARIABLE)
RATE_SINGLE = "single"
RATE_DOUBLE = "double"          # normaal/dal
RATE_INPUT_ALL_IN = "all_in"    # tarief incl. energiebelasting en btw
RATE_INPUT_BARE = "bare"        # kaal leveringstarief excl. btw

# Voorlopige waarden 2027 (gecorrigeerd Belastingplan 2027; definitief pas in december 2026).
PROVISIONAL_2027_START = "2027-01-01"
PROVISIONAL_TAX_ELEC_2027 = 0.08802   # = € 0,1065 incl. btw
PROVISIONAL_TAX_GAS_2027 = 0.61628    # = € 0,7457 incl. btw
# Wettelijk minimum terugleververgoeding 2027–2029: 50% van het kale leveringstarief.
EXPORT_MINIMUM_SHARE = 0.5
EXPORT_MINIMUM_FIRST_YEAR = 2027
EXPORT_MINIMUM_LAST_YEAR = 2029
# Waarschuw zoveel dagen vóór een periode ingaat als die nog voorlopig of onvolledig is.
PERIOD_WARN_DAYS = 14

# CUSTOM: bron voor de kale EPEX-beursprijs. Elk platform levert een beursprijs;
# voor stroom is die identiek, voor gas verschilt 'ie licht per bron.
CONF_EPEX_SOURCE = "epex_source"
EPEX_SOURCES = ("easyenergy", "energyzero", "frank", "essent")
DEFAULT_EPEX_SOURCE = "easyenergy"

# --- eon-app group-types ----------------------------------------------------
GROUP_MARKET = "MARKET_PRICE"
GROUP_FEE = "PURCHASING_FEE"
GROUP_TAX = "TAX"

# --- Databron-URL's ---------------------------------------------------------
EON_APP_PATH = "/api/public/dynamicpricing/dynamic-prices/v1"
EON_APP_HEADERS = {"x-request-origin": "client"}
EASYENERGY_URL = "https://price-graph.mijn.easyenergy.com/api/prices"
ENERGYZERO_URL = "https://api.energyzero.nl/v1/energyprices"
FRANK_URL = "https://graphql.frankenergie.nl"

# EPEX (via easyEnergy) wordt alleen als vangnet opgehaald, voor een bron die geen eigen
# beursprijs levert (zie coordinator._needs_epex_fallback); de huidige bronnen doen dat wel.
ELECTRICITY = "electricity"
GAS = "gas"

# --- Voorspellingen (optioneel, standaard UIT) ------------------------------
# Alles hieronder doet niets zolang CONF_FORECAST uit staat: geen extra calls,
# geen extra entiteiten. Alle sleutels hebben het voorvoegsel `forecast_`, zodat
# ze nooit botsen met de CUSTOM-velden (de coordinator leest data + options).
CONF_FORECAST = "forecast_enabled"
CONF_FC_PROVIDERS = "forecast_providers"
CONF_FC_ENSEMBLE = "forecast_ensemble"
CONF_FC_WEIGHTING = "forecast_weighting"
CONF_FC_WEIGHT_EXPONENT = "forecast_weight_exponent"
CONF_FC_ACCURACY = "forecast_accuracy"
CONF_FC_HORIZON = "forecast_horizon_hours"
CONF_FC_INTERVAL = "forecast_interval_hours"
CONF_FC_MIN_SAMPLES = "forecast_min_samples"
CONF_FC_WEIGHT_FLOOR = "forecast_weight_floor"
CONF_FC_BIAS = "forecast_bias_correction"
CONF_FC_WINDOW = "forecast_window_days"
CONF_FC_CHEAPEST_HOURS = "forecast_cheapest_hours"
CONF_FC_EPEXPREDICTOR_URL = "forecast_epexpredictor_url"
CONF_FC_EPF_API_KEY = "forecast_energypriceforecast_api_key"

WEIGHTING_EQUAL = "equal"
WEIGHTING_ACCURACY = "accuracy"

PROVIDER_EPEXPREDICTOR = "epexpredictor"
PROVIDER_EPF = "energypriceforecast"
ENSEMBLE = "ensemble"

EPEXPREDICTOR_URL = "https://epexpredictor.batzill.com"
EPF_URL = "https://api.energypriceforecast.eu/api/v1/home-assistant/prices"
# Voor de uitleg in de options flow (hassfest staat geen URL's in vertalingen toe).
EPF_PRIVATE_PRO_URL = "https://energypriceforecast.eu/en/private-pro/"

FORECAST_DEFAULTS: dict = {
    CONF_FORECAST: False,
    CONF_FC_PROVIDERS: [PROVIDER_EPEXPREDICTOR, PROVIDER_EPF],
    CONF_FC_ENSEMBLE: True,
    CONF_FC_WEIGHTING: WEIGHTING_ACCURACY,
    CONF_FC_WEIGHT_EXPONENT: 1,
    CONF_FC_ACCURACY: True,
    CONF_FC_HORIZON: 72,
    CONF_FC_INTERVAL: 6,
    CONF_FC_MIN_SAMPLES: 96,     # kwartieren = één dag
    CONF_FC_WEIGHT_FLOOR: 0.10,  # minimaal 10% gewicht per provider
    CONF_FC_BIAS: False,
    CONF_FC_WINDOW: 28,
    CONF_FC_CHEAPEST_HOURS: 3,
    CONF_FC_EPEXPREDICTOR_URL: EPEXPREDICTOR_URL,
    CONF_FC_EPF_API_KEY: "",
}

# Looptijd-buckets (uren tussen ophalen en doelkwartier): 0–1, 2–3 en 4–7 dagen.
# Bewust een constante: instelbare grenzen maken de statistiek onvergelijkbaar.
LEAD_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("d0_1", 0, 24),
    ("d2_3", 24, 72),
    ("d4_7", 72, 168),
)
FC_MAX_HORIZON = 168
FC_TIMEOUT = 20                    # seconden per provider-call
FC_BACKOFF_START_MIN = 30          # eerste retry na een fout
FC_BACKOFF_MAX_MIN = 12 * 60       # nooit langer wachten dan dit
FC_SERIES_MAX_AGE_H = 48           # oudere providerreeks telt niet meer mee
FC_SNAPSHOT_MAX_AGE_D = 8          # niet-afgerekende snapshots daarna weg
FC_ERROR_CLIP = 0.15               # €/kWh: fout per kwartier begrensd (uitschieters)
FC_CHEAPEST_LOOKAHEAD_H = 48
