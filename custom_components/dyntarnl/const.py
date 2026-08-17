"""Constanten voor DynTarNederland — dynamische tarieven, meerdere leveranciers."""

from __future__ import annotations

import logging
from dataclasses import dataclass

DOMAIN = "dyntarnl"
NAME = "DynTarNL"

LOGGER = logging.getLogger(__package__)

# --- Platforms (databron-technieken) ---------------------------------------
PLATFORM_EON_APP = "eon_app"      # E.ON eon-app: volledige breakdown
PLATFORM_ENERGYZERO = "energyzero"  # EnergyZero: alleen all-in prijs
PLATFORM_FRANK = "frank"          # Frank Energie GraphQL: volledige breakdown
PLATFORM_EASYENERGY = "easyenergy"  # easyEnergy: EPEX + belasting
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
    # energyzero white-labels — marktprijs + NL-standaard belasting (geen opslag)
    Supplier("energyzero", "EnergyZero", PLATFORM_ENERGYZERO),
    Supplier("anwb", "ANWB Energie", PLATFORM_ENERGYZERO),
    Supplier("coolblue", "Coolblue Energie", PLATFORM_ENERGYZERO),
    Supplier("energievanons", "Energie VanOns", PLATFORM_ENERGYZERO),
    Supplier("groenestroomlokaal", "GroeneStroomLokaal", PLATFORM_ENERGYZERO),
    Supplier("samsam", "SamSam", PLATFORM_ENERGYZERO),
    Supplier("hegg", "Hegg Energy", PLATFORM_ENERGYZERO),
    # easyenergy — EPEX + belasting
    Supplier("nieuwestroom", "Nieuwestroom", PLATFORM_EASYENERGY),
    Supplier("easyenergy", "EasyEnergie", PLATFORM_EASYENERGY),
    # custom — EPEX + zelf ingevulde opslag/belasting/btw (voor login-only leveranciers)
    Supplier("custom", "Eigen leverancier (handmatig)", PLATFORM_CUSTOM),
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

# EPEX wordt ALTIJD opgehaald (via easyEnergy) als basis/fallback voor de
# beurs-waarde en de teruglever-drempels, ongeacht de gekozen leverancier.
ELECTRICITY = "electricity"
GAS = "gas"
