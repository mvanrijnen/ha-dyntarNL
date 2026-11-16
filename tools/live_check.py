"""Live-controle van alle bronnen die zonder inlog werken.

Haalt echte data op bij elke leverancier, elke Custom-EPEX-bron en de voorspellers, en
controleert of het klopt: data aanwezig, het huidige uur gevonden, plausibele bedragen en
een all-in die uit de onderdelen volgt. Exitcode 1 als er iets mis is, zodat een
API-wijziging bij een bron direct opvalt (lokaal, en dagelijks in CI).

Vereist Home Assistant in de omgeving (voor homeassistant.util.dt), bijvoorbeeld:
    pip install pytest-homeassistant-custom-component   # of: pip install homeassistant
    python tools/live_check.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))

import aiohttp  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402

dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Amsterdam"))

from dyntarnl import sources  # noqa: E402
from dyntarnl.const import (  # noqa: E402
    EPEX_SOURCES,
    PLATFORM_CUSTOM,
    PLATFORM_EASYENERGY,
    PLATFORM_EON_APP,
    PLATFORM_FRANK,
    SUPPLIERS,
)
from dyntarnl.forecast.providers import PROVIDERS  # noqa: E402
from dyntarnl.prices import slot_at  # noqa: E402

CUSTOM = {"vat_percentage": 21, "markup_electricity": 0.02, "markup_gas": 0.05,
          "energy_tax_electricity": 0.09161, "energy_tax_gas": 0.60066}
failures: list[str] = []


def check(ok: bool, name: str, detail: str) -> None:
    print(f"  {'OK  ' if ok else 'FOUT'} {name}: {detail}")
    if not ok:
        failures.append(f"{name}: {detail}")


def check_prices(name: str, data: dict, custom: bool = False) -> None:
    now = dt_util.now()
    for energy, (low, high) in (("electricity", (-1.0, 2.0)), ("gas", (0.0, 5.0))):
        ed = data.get(energy)
        if ed is None:
            check(False, f"{name} {energy}", "geen data")
            continue
        cur = slot_at(ed.today, now)
        hours = len(ed.today)
        detail = f"{hours} slots vandaag, morgen {len(ed.tomorrow or [])}"
        if cur is None:
            check(False, f"{name} {energy}", f"geen slot voor nu ({detail})")
            continue
        parts = cur.market + cur.fee + cur.tax
        consistent = abs(cur.total - parts) < 0.002
        plausible = low <= cur.total <= high and 20 <= hours <= 25
        if custom:
            expected = (cur.market_ex + CUSTOM[f"markup_{energy}"] + CUSTOM[f"energy_tax_{energy}"]) * 1.21
            consistent = consistent and abs(cur.total - expected) < 1e-5
        check(consistent and plausible, f"{name} {energy}",
              f"nu all-in {cur.total:.5f} (beurs {cur.market:.5f} + opslag {cur.fee:.5f} + EB {cur.tax:.5f}), {detail}")


async def main() -> int:
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        print("Leveranciers:")
        for sup in SUPPLIERS:
            try:
                if sup.platform == PLATFORM_EON_APP:
                    data = await sources.fetch_eon_app(session, sup.host)
                elif sup.platform == PLATFORM_EASYENERGY:
                    data = await sources.fetch_easyenergy(session)
                elif sup.platform == PLATFORM_FRANK:
                    data = await sources.fetch_frank(session)
                elif sup.platform == PLATFORM_CUSTOM:
                    continue  # hieronder per EPEX-bron
                else:
                    continue
                check_prices(sup.name, data)
            except Exception as err:  # noqa: BLE001
                check(False, sup.name, f"{type(err).__name__}: {err}")

        print("Custom, per EPEX-bron:")
        for src in EPEX_SOURCES:
            try:
                check_prices(f"Custom/{src}", await sources.build_custom(session, {**CUSTOM, "epex_source": src}), custom=True)
            except Exception as err:  # noqa: BLE001
                check(False, f"Custom/{src}", f"{type(err).__name__}: {err}")

        print("Voorspellers (zonder key):")
        for key, cls in PROVIDERS.items():
            provider = cls({})
            try:
                points = await provider.async_fetch(session, 48)
                prices = [p.price_raw for p in points]
                ok = len(points) >= 4 * 12 and all(-1.0 <= x <= 2.0 for x in prices)
                info = f", info {provider.info}" if provider.info else ""
                span = f"{dt_util.as_local(points[0].start):%d-%m %H:%M} t/m {dt_util.as_local(points[-1].start):%d-%m %H:%M}" if points else "-"
                check(ok, key, f"{len(points)} kwartieren ({span}), min {min(prices, default=0):.4f} max {max(prices, default=0):.4f}{info}")
            except Exception as err:  # noqa: BLE001
                check(False, key, f"{type(err).__name__}: {err}")

    print()
    if failures:
        print(f"{len(failures)} probleem/problemen:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("Alle bronnen OK.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
