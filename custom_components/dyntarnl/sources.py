"""Databron-parsers per platform. Elke functie levert het gedeelde PriceData-model."""

from __future__ import annotations

from datetime import timedelta

import aiohttp

from homeassistant.util import dt as dt_util

from .const import (
    CONF_EPEX_SOURCE,
    CONF_MARKUP_ELEC,
    CONF_MARKUP_GAS,
    CONF_TAX_ELEC,
    CONF_TAX_GAS,
    CONF_VAT,
    DEFAULT_EPEX_SOURCE,
    DEFAULT_TAX_ELEC,
    DEFAULT_TAX_GAS,
    DEFAULT_VAT,
    EASYENERGY_URL,
    ELECTRICITY,
    ENERGYZERO_URL,
    EON_APP_HEADERS,
    EON_APP_PATH,
    FRANK_URL,
    GAS,
    GROUP_FEE,
    GROUP_MARKET,
    GROUP_TAX,
)
from .model import EnergyData, PriceData, Slot, bucket_by_day, parse_dt

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_UNIT_MAP = {"kWh": "kWh", "m3": "m³", "m³": "m³"}


async def _get_json(session: aiohttp.ClientSession, url: str, **kwargs) -> dict:
    async with session.get(url, timeout=_TIMEOUT, **kwargs) as resp:
        resp.raise_for_status()
        return await resp.json()


def _date_range() -> tuple[str, str]:
    """Gisteren t/m overmorgen (lokale datums), zodat we altijd de rand meepakken."""
    now = dt_util.now()
    start = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    end = (now + timedelta(days=2)).strftime("%Y-%m-%d")
    return start, end


# --- eon-app (Essent, Energiedirect): volledige breakdown -------------------


def _group(groups: list[dict], gtype: str, key: str = "amount") -> float:
    for g in groups:
        if g.get("type") == gtype:
            return float(g.get(key) or 0.0)
    return 0.0


async def fetch_eon_app(session: aiohttp.ClientSession, host: str) -> PriceData:
    url = f"https://{host}{EON_APP_PATH}"
    payload = await _get_json(session, url, headers=EON_APP_HEADERS)

    per_energy: dict[str, list[Slot]] = {ELECTRICITY: [], GAS: []}
    unit_vat: dict[str, tuple[str, float]] = {}
    for day in payload.get("prices", []):
        for energy in (ELECTRICITY, GAS):
            block = day.get(energy)
            if not block or not block.get("tariffs"):
                continue
            unit_vat[energy] = (
                _UNIT_MAP.get(block.get("unitOfMeasurement", ""), block.get("unitOfMeasurement", "")),
                float(block.get("vatPercentage") or 0),
            )
            for t in block["tariffs"]:
                g = t.get("groups", [])
                per_energy[energy].append(
                    Slot(
                        start=parse_dt(t["startDateTime"]),
                        end=parse_dt(t["endDateTime"]),
                        total=float(t["totalAmount"]),
                        market=_group(g, GROUP_MARKET),
                        market_ex=_group(g, GROUP_MARKET, "amountEx"),
                        fee=_group(g, GROUP_FEE),
                        fee_ex=_group(g, GROUP_FEE, "amountEx"),
                        tax=_group(g, GROUP_TAX),
                        tax_ex=_group(g, GROUP_TAX, "amountEx"),
                        vat=float(t.get("totalAmountVat") or 0.0),
                    )
                )

    result: PriceData = {}
    for energy, slots in per_energy.items():
        if slots:
            unit, vat = unit_vat[energy]
            result[energy] = bucket_by_day(slots, unit, vat)
    return result


# --- easyEnergy (Nieuwestroom) + EPEX-bron ----------------------------------


def _clean_ts(value: str) -> str:
    # easyEnergy geeft 7 fractionele cijfers; kap af op hele seconden.
    return value[:19]


async def _easyenergy_rows(
    session: aiohttp.ClientSession, energy_type: str, granularity: str
) -> list[dict]:
    start, end = _date_range()
    params = {"start": start, "end": end, "type": energy_type, "granularity": granularity}
    payload = await _get_json(session, EASYENERGY_URL, params=params)
    return payload.get("prices", [])


async def fetch_easyenergy(session: aiohttp.ClientSession) -> PriceData:
    """EasyEnergie: volledige breakdown uit de API.

    easyEnergy levert per uur beurs (price/priceIncVat), energiebelasting (energyTax),
    de eigen opslag (purchasePrice) en de all-in (invoicePrice) — allemaal incl. btw.
    """
    result: PriceData = {}
    for energy, unit, gran in ((ELECTRICITY, "kWh", "hour"), (GAS, "m³", "day")):
        rows = await _easyenergy_rows(session, energy, gran)
        slots: list[Slot] = []
        for r in rows:
            market_ex = float(r["price"])
            market = float(r.get("priceIncVat") or market_ex * 1.21)
            tax = float(r.get("energyTax") or 0.0)  # incl. btw
            fee = float(r.get("purchasePrice") or 0.0)  # opslag, incl. btw
            factor = market / market_ex if market_ex else 1.21
            total = float(r["invoicePrice"]) if r.get("invoicePrice") else round(market + fee + tax, 6)
            slots.append(
                Slot(
                    start=parse_dt(_clean_ts(r["from"])),
                    end=parse_dt(_clean_ts(r["until"])),
                    total=round(total, 6),
                    market=market,
                    market_ex=market_ex,
                    fee=fee,
                    fee_ex=round(fee / factor, 6) if factor else fee,
                    tax=tax,
                    tax_ex=round(tax / factor, 6) if factor else tax,
                    vat=round(total - (market_ex + fee / factor + tax / factor), 6) if factor else 0.0,
                )
            )
        if slots:
            result[energy] = bucket_by_day(slots, unit, 21.0)
    return result


type EpexMap = dict[str, dict[str, float]]  # energy -> {iso-uur: market_ex}


async def fetch_epex(session: aiohttp.ClientSession) -> EpexMap:
    """Altijd-opgehaalde kale EPEX-beursprijs (excl. btw) per uur, via easyEnergy."""
    epex: EpexMap = {ELECTRICITY: {}, GAS: {}}
    for energy, gran in ((ELECTRICITY, "hour"), (GAS, "day")):
        for r in await _easyenergy_rows(session, energy, gran):
            key = parse_dt(_clean_ts(r["from"])).isoformat()
            epex[energy][key] = float(r["price"])
    return epex


# --- CUSTOM: EPEX + zelf ingevulde opslag/belasting/btw ---------------------


async def _epex_source(session: aiohttp.ClientSession, source: str) -> PriceData:
    """Haal de beursprijs-slots op bij de gekozen bron (elk platform levert een beurs)."""
    if source == "energyzero":
        return await fetch_energyzero(session)
    if source == "frank":
        return await fetch_frank(session)
    if source == "essent":
        return await fetch_eon_app(session, "www.essent.nl")
    return await fetch_easyenergy(session)  # default: easyEnergy


async def build_custom(session: aiohttp.ClientSession, cfg: dict) -> PriceData:
    """Reken all-in uit de kale EPEX + door de gebruiker ingevulde (excl. btw) waarden.

    all-in = (EPEX + opslag + energiebelasting) × (1 + btw%);  beurs = EPEX incl. btw.
    De kale EPEX komt van de gekozen bron (`epex_source`, default easyEnergy). Alleen de
    beursprijs van die bron wordt gebruikt; de opslag/belasting komen van de gebruiker.
    """
    vat = float(cfg.get(CONF_VAT, DEFAULT_VAT))
    factor = 1 + vat / 100
    base = await _epex_source(session, cfg.get(CONF_EPEX_SOURCE, DEFAULT_EPEX_SOURCE))
    params = {
        ELECTRICITY: (float(cfg.get(CONF_MARKUP_ELEC, 0.0)), float(cfg.get(CONF_TAX_ELEC, 0.0)), "kWh"),
        GAS: (float(cfg.get(CONF_MARKUP_GAS, 0.0)), float(cfg.get(CONF_TAX_GAS, 0.0)), "m³"),
    }

    def to_custom(s: Slot, markup_ex: float, tax_ex: float) -> Slot:
        subtotal_ex = s.market_ex + markup_ex + tax_ex
        total = subtotal_ex * factor
        return Slot(
            start=s.start,
            end=s.end,
            total=round(total, 6),
            market=round(s.market_ex * factor, 6),
            market_ex=s.market_ex,
            fee=round(markup_ex * factor, 6),
            fee_ex=markup_ex,
            tax=round(tax_ex * factor, 6),
            tax_ex=tax_ex,
            vat=round(total - subtotal_ex, 6),
        )

    result: PriceData = {}
    for energy, (markup_ex, tax_ex, unit) in params.items():
        ed = base.get(energy)
        if not ed:
            continue

        def mapped(slots):
            return [to_custom(s, markup_ex, tax_ex) for s in slots] if slots else None

        result[energy] = EnergyData(
            unit=unit,
            vat_percentage=vat,
            today=mapped(ed.today) or [],
            tomorrow=mapped(ed.tomorrow),
            yesterday=mapped(ed.yesterday),
        )
    return result


# --- Frank Energie (GraphQL): volledige breakdown ---------------------------

_FRANK_FIELDS = "from till marketPrice marketPriceTax sourcingMarkupPrice energyTaxPrice"


async def _post_json(session: aiohttp.ClientSession, url: str, payload: dict) -> dict:
    async with session.post(url, json=payload, timeout=_TIMEOUT) as resp:
        resp.raise_for_status()
        return await resp.json()


async def fetch_frank(session: aiohttp.ClientSession) -> PriceData:
    # Frank geeft per call maar één dag terug -> gisteren/vandaag/morgen apart ophalen.
    now = dt_util.now()
    rows_per_energy: dict[str, list[dict]] = {ELECTRICITY: [], GAS: []}
    for offset in (-1, 0, 1):
        start = (now + timedelta(days=offset)).strftime("%Y-%m-%d")
        end = (now + timedelta(days=offset + 1)).strftime("%Y-%m-%d")
        query = (
            f'query{{marketPricesElectricity(startDate:"{start}",endDate:"{end}"){{{_FRANK_FIELDS}}} '
            f'marketPricesGas(startDate:"{start}",endDate:"{end}"){{{_FRANK_FIELDS}}}}}'
        )
        data = (await _post_json(session, FRANK_URL, {"query": query})).get("data") or {}
        rows_per_energy[ELECTRICITY].extend(data.get("marketPricesElectricity") or [])
        rows_per_energy[GAS].extend(data.get("marketPricesGas") or [])

    result: PriceData = {}
    for energy, unit in ((ELECTRICITY, "kWh"), (GAS, "m³")):
        slots: list[Slot] = []
        for r in rows_per_energy[energy]:
            market_ex = float(r["marketPrice"])
            market = round(market_ex + float(r.get("marketPriceTax") or 0.0), 6)
            factor = market / market_ex if market_ex else 1.21
            # sourcingMarkupPrice (opslag) is AL incl. btw — net als energyTaxPrice;
            # alleen marketPrice heeft een aparte btw-regel (marketPriceTax).
            fee = float(r.get("sourcingMarkupPrice") or 0.0)
            fee_ex = round(fee / factor, 6) if factor else fee
            tax = float(r.get("energyTaxPrice") or 0.0)  # incl. btw
            tax_ex = round(tax / factor, 6) if factor else tax
            total = round(market + fee + tax, 6)
            slots.append(
                Slot(
                    start=parse_dt(r["from"]),
                    end=parse_dt(r["till"]),
                    total=total,
                    market=market,
                    market_ex=market_ex,
                    fee=fee,
                    fee_ex=fee_ex,
                    tax=tax,
                    tax_ex=tax_ex,
                    vat=round(total - (market_ex + fee_ex + tax_ex), 6),
                )
            )
        if slots:
            result[energy] = bucket_by_day(slots, unit, 21.0)
    return result


# --- EnergyZero (ANWB): alleen marktprijs; belasting via NL-standaard --------


async def fetch_energyzero(session: aiohttp.ClientSession) -> PriceData:
    """EnergyZero levert alleen de kale marktprijs; energiebelasting via NL-default,
    geen leverancier-opslag. Voor exacte opslag: gebruik CUSTOM."""
    now = dt_util.now()
    from_date = (now - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000Z")
    till_date = (now + timedelta(days=1)).strftime("%Y-%m-%dT23:59:59.999Z")

    result: PriceData = {}
    per_energy = (
        (ELECTRICITY, 1, "kWh", DEFAULT_TAX_ELEC),
        (GAS, 2, "m³", DEFAULT_TAX_GAS),
    )
    for energy, usage, unit, tax_ex in per_energy:
        params = {
            "fromDate": from_date,
            "tillDate": till_date,
            "interval": "4",
            "usageType": str(usage),
            "inclBtw": "false",
        }
        payload = await _get_json(session, ENERGYZERO_URL, params=params)
        factor = 1.21
        tax = round(tax_ex * factor, 6)
        slots: list[Slot] = []
        for r in payload.get("Prices", []):
            start = parse_dt(r["readingDate"])
            market_ex = float(r["price"])
            market = round(market_ex * factor, 6)
            total = round(market + tax, 6)
            slots.append(
                Slot(
                    start=start,
                    end=start + timedelta(hours=1),
                    total=total,
                    market=market,
                    market_ex=market_ex,
                    tax=tax,
                    tax_ex=tax_ex,
                    vat=round((market - market_ex) + (tax - tax_ex), 6),
                )
            )
        if slots:
            result[energy] = bucket_by_day(slots, unit, 21.0)
    return result
