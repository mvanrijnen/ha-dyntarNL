# DynTarNL — Dynamische stroom- & gastarieven (NL)

[![Tests](https://github.com/mvanrijnen/ha-dyntarNL/actions/workflows/test.yml/badge.svg)](https://github.com/mvanrijnen/ha-dyntarNL/actions/workflows/test.yml)
[![Validate](https://github.com/mvanrijnen/ha-dyntarNL/actions/workflows/validate.yml/badge.svg)](https://github.com/mvanrijnen/ha-dyntarNL/actions/workflows/validate.yml)
[![hacs](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Home Assistant integratie die de **dynamische energietarieven van meerdere Nederlandse
leveranciers** als sensoren publiceert. Je kiest je leverancier; de integratie bepaalt zelf
welk platform en welke publieke prijs-API erbij hoort. **Geen account of API-sleutel nodig**
voor de ondersteunde leveranciers.

> ⚠️ In ontwikkeling (v0.0.1). Onofficieel; geen affiliatie met de leveranciers.

## Ondersteunde leveranciers

Je kiest je merk in de config-flow; het **platform** wordt automatisch bepaald.

| Leverancier | Platform | Detail |
| --- | --- | --- |
| Essent, Energiedirect | eon-app | volledige breakdown (beurs + opslag + belasting) |
| Frank Energie | frank | volledige breakdown |
| EnergyZero, ANWB Energie, Coolblue Energie, Energie VanOns, GroeneStroomLokaal, SamSam, Hegg Energy | energyzero | marktprijs + NL-belasting (geen opslag) |
| Nieuwestroom, EasyEnergie | easyenergy | EPEX + belasting |
| **Eigen leverancier (handmatig)** | custom | EPEX + je eigen btw/opslag/belasting |

**Staat je leverancier er niet bij?** Kies **CUSTOM**. De EPEX-beursprijs is voor iedereen
gelijk; je vult éénmalig je opslag + energiebelasting (excl. btw) + btw in, en de integratie
rekent je all-in prijs exact uit. Zo werken ook login-only leveranciers zoals **Vattenfall,
Eneco, Tibber, Greenchoice, Zonneplan, ENGIE, DELTA, Vandebron, OXXIO** e.a.

De integratie haalt **altijd** de kale EPEX-beursprijs op als basis/fallback, zodat de
beurs-waarde en de teruglever-drempels bij élke leverancier werken.

## Sensoren

Per energietype (**Stroom**, **Gas**) en per prijsbasis (**all-in** en **beurs**): vorig uur,
huidige prijs, volgend uur, en vandaag/morgen laagste/gemiddeld/hoogste. Plus:

- **Component-sensoren**: energiebelasting en inkoopvergoeding (incl. én excl. btw).
- **Teruglever-sensoren (stroom)**: terugleververgoeding, terugleverkosten, en tellingen —
  drempel `beursprijs ≤ opslag`.
- **Binary sensors** om direct op te schakelen (ZeroExport / accu): `prijs negatief` en
  `terugleveren kost geld` (nu / volgend uur), plus `morgen beschikbaar`.

De `huidige prijs`-sensoren dragen `today` / `tomorrow` arrays mee (klaar voor ApexCharts).

## CUSTOM-berekening

```
all-in = (EPEX + opslag + energiebelasting) × (1 + btw%)     (alle invoer excl. btw)
beurs  = EPEX × (1 + btw%)
```

## Installatie (HACS)

**Snel — via de knop** (vereist dat HACS al geïnstalleerd is):

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mvanrijnen&repository=ha-dyntarNL&category=integration)

Klik de knop → HACS opent op jouw Home Assistant met deze repo al ingevuld → **Download** en
herstart Home Assistant. Voeg daarna de integratie toe met de knop hieronder (of via
**Instellingen → Apparaten & Services → Integratie toevoegen** → *DynTarNL*):

[![Add integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=dyntarnl)

**Handmatig:**

1. HACS → ⋮ → **Custom repositories** → `https://github.com/mvanrijnen/ha-dyntarNL`, categorie **Integration**.
2. Installeer **DynTarNL** en herstart Home Assistant.
3. **Instellingen → Apparaten & Services → Integratie toevoegen** → zoek *DynTarNL* → kies je leverancier.

Of handmatig: kopieer `custom_components/dyntarnl/` naar je Home Assistant `config/custom_components/` en herstart.

## Ontwikkeling / tests

```bash
pip install pytest
pytest
```

De tests draaien zonder HA-installatie (Home Assistant wordt gestubd) en gebruiken vastgelegde
JSON-fixtures — geen live API-calls.

## Licentie

[MIT](LICENSE) © Maurits van Rijnen
