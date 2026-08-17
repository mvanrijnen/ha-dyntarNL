# DynTarNL — Dynamische stroom- & gastarieven (NL)

[![Tests](https://github.com/mvanrijnen/ha-dyntarNL/actions/workflows/test.yml/badge.svg)](https://github.com/mvanrijnen/ha-dyntarNL/actions/workflows/test.yml)
[![Validate](https://github.com/mvanrijnen/ha-dyntarNL/actions/workflows/validate.yml/badge.svg)](https://github.com/mvanrijnen/ha-dyntarNL/actions/workflows/validate.yml)
[![hacs](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Home Assistant integratie die de **dynamische energietarieven van meerdere Nederlandse
leveranciers** als sensoren publiceert. Je kiest je leverancier; de integratie bepaalt zelf
welk platform en welke publieke prijs-API erbij hoort. **Geen account of API-sleutel nodig**
voor de ondersteunde leveranciers.

> ## 🧪 Experimenteel — v0.0.1
>
> Deze integratie is **nog experimenteel en volop in ontwikkeling**. Dingen kunnen wijzigen
> of (tijdelijk) stuk zijn, en niet elke leverancier is even uitgebreid getest. Gebruik op
> eigen risico. Onofficieel; geen affiliatie met de genoemde leveranciers.
>
> Feedback en bugreports zijn welkom via de [issues](https://github.com/mvanrijnen/ha-dyntarNL/issues).

## Ondersteunde leveranciers

Je kiest je merk in de config-flow; het **platform** wordt automatisch bepaald. Alleen
leveranciers die een **volledige, exacte uitsplitsing** (beurs + opslag + belasting) via een
publieke API leveren staan in de lijst — dan hoef je niets in te vullen. Alle andere
leveranciers gebruik je via **CUSTOM**.

| Leverancier | Platform | Uitsplitsing |
| --- | --- | --- |
| Essent, Energiedirect | eon-app | volledig (beurs + opslag + belasting) |
| Frank Energie | frank | volledig |
| EasyEnergie | easyenergy | volledig |
| **Eigen leverancier (handmatig)** | custom | jij vult opslag + belasting + btw in |

**Waarom niet elke leverancier in de lijst?** Sommige bronnen geven **alleen een
marktprijs** door, geen opslag/all-in — dan zouden we een verkeerde all-in tonen. Voor die
leveranciers is **CUSTOM** de juiste route:

- **EnergyZero-merken** (ANWB, Coolblue, Energie VanOns, GroeneStroomLokaal, SamSam, Hegg,
  EnergyZero): de API geeft alleen de marktprijs.
- **Nieuwestroom**: heeft een dynamische opslag die niet in de API zit.
- **Login-only** leveranciers (**Vattenfall, Eneco, Tibber, Greenchoice, Zonneplan, ENGIE,
  DELTA, Vandebron, OXXIO** e.a.): geen publieke prijs-API.

Bij **CUSTOM** vul je éénmalig je opslag + energiebelasting (excl. btw) + btw in; de EPEX-
beursprijs (voor iedereen gelijk) wordt automatisch opgehaald en je all-in exact berekend.

## Entiteiten (referentie)

Je kiest **één** leverancier; de `entity_id`'s bevatten daarom **geen** leverancier-naam.
Je krijgt drie devices: **DynTarNL Stroom**, **DynTarNL Gas** en **DynTarNL** (voor de knop).
De gekozen leverancier staat als *fabrikant* op de device-pagina. Bedragen incl. btw, in
€/kWh (stroom) of €/m³ (gas).

> Stroom is per uur; **gas** volgt de Nederlandse **gasdag** (06:00–06:00): de prijs verspringt
> om 06:00 en is daartussen constant.

### Prijs-sensoren (stroom & gas, all-in & beurs)

`X` = `stroom` of `gas`, `Y` = `all_in` of `beurs`.

| entity_id | Betekenis |
| --- | --- |
| `sensor.dyntarnl_X_Y_vorig_uur` | Prijs van het vorige uur |
| `sensor.dyntarnl_X_Y_huidige_prijs` | Prijs van het huidige uur (met `today`/`tomorrow` als attribuut) |
| `sensor.dyntarnl_X_Y_volgend_uur` | Prijs van het eerstvolgende uur |
| `sensor.dyntarnl_X_Y_vandaag_laagste` | Laagste prijs vandaag |
| `sensor.dyntarnl_X_Y_vandaag_gemiddeld` | Gemiddelde prijs vandaag |
| `sensor.dyntarnl_X_Y_vandaag_hoogste` | Hoogste prijs vandaag |
| `sensor.dyntarnl_X_Y_morgen_laagste` | Laagste prijs morgen (leeg tot gepubliceerd) |
| `sensor.dyntarnl_X_Y_morgen_hoogste` | Hoogste prijs morgen (leeg tot gepubliceerd) |

Bijv. `sensor.dyntarnl_stroom_all_in_huidige_prijs`, `sensor.dyntarnl_gas_beurs_vandaag_gemiddeld`.

### Component-sensoren (opbouw huidig uur)

| entity_id | Betekenis |
| --- | --- |
| `sensor.dyntarnl_X_energiebelasting_incl_btw` / `..._excl_btw` | Energiebelasting per eenheid |
| `sensor.dyntarnl_X_inkoopvergoeding_incl_btw` / `..._excl_btw` | Opslag van de leverancier |

### Teruglever-sensoren (alleen stroom)

| entity_id | Waarde |
| --- | --- |
| `sensor.dyntarnl_stroom_terugleververgoeding_nu` | €/kWh voor export dit uur (kan negatief) |
| `sensor.dyntarnl_stroom_terugleverkosten_nu` | €/kWh die export kost (0 = kost niets) |
| `sensor.dyntarnl_stroom_terugleverkosten_volgend_uur` | idem, volgend uur |
| `sensor.dyntarnl_stroom_negatieve_uren_vandaag` | aantal uren met beursprijs < 0 |
| `sensor.dyntarnl_stroom_uren_terugleveren_kost_geld_vandaag` | aantal uren dat export geld kost |

### Binary sensors (triggers)

| entity_id | Aan wanneer |
| --- | --- |
| `binary_sensor.dyntarnl_stroom_prijs_negatief_nu` | beursprijs huidige uur < 0 |
| `binary_sensor.dyntarnl_stroom_prijs_negatief_vorig_uur` | beursprijs vorige uur < 0 |
| `binary_sensor.dyntarnl_stroom_prijs_negatief_volgend_uur` | beursprijs volgende uur < 0 |
| `binary_sensor.dyntarnl_stroom_terugleveren_kost_geld_nu` | terugleververgoeding < 0 (beurs ≤ opslag) |
| `binary_sensor.dyntarnl_stroom_terugleveren_kost_geld_volgend_uur` | idem, volgend uur |
| `binary_sensor.dyntarnl_stroom_morgen_beschikbaar` / `..._gas_...` | prijzen van morgen gepubliceerd |

### Knop / action

| entity_id | Actie |
| --- | --- |
| `button.dyntarnl_ververs_tarieven` | Haalt de tarieven direct opnieuw op (roep aan met `button.press`) |

### Attributen op "huidige prijs"

De `huidige prijs`-sensoren dragen de dag-arrays mee, klaar voor
[ApexCharts](https://github.com/RomRider/apexcharts-card):

- `today` / `tomorrow` — lijst van `{ start, end, price }`
- `market_price`, `purchase_fee`, `energy_tax` — opbouw van het huidige uur
- `unit`, `vat_percentage`

### Voorbeeld-automatisering: ZeroExport bij ongunstige teruglevering

```yaml
automation:
  - alias: ZeroExport aan/uit op teruglevering
    trigger:
      - platform: state
        entity_id: binary_sensor.dyntarnl_stroom_terugleveren_kost_geld_nu
    action:
      - service: "switch.turn_{{ 'on' if trigger.to_state.state == 'on' else 'off' }}"
        target: { entity_id: switch.omvormer_zero_export }
```

## Automatische detectie van platform, opslag & tarieven

Je kiest alleen je **merk**; de integratie regelt de rest zelf:

1. **Platform-detectie.** Elk merk is intern gekoppeld aan het juiste platform (eon-app,
   Frank of easyEnergy). De bijbehorende publieke prijs-API en het responseformaat worden
   automatisch gekozen — jij hoeft geen URL of API-type te weten.

2. **Volledige uitsplitsing, automatisch.** Alle leveranciers in de lijst leveren de complete
   breakdown (beursprijs + opslag + energiebelasting) via hun API. De component-sensoren en de
   teruglever-drempel `beursprijs ≤ opslag` worden dus **automatisch met de echte
   leverancier-opslag** gevuld — je hoeft niets in te vullen.

3. **Alleen bij CUSTOM vul je zelf gegevens in.** Leveranciers die geen bruikbare opslag/all-in
   doorgeven (alleen een marktprijs, of een dynamische opslag) staan bewust niet in de lijst;
   die gebruik je via CUSTOM. Zie [Ondersteunde leveranciers](#ondersteunde-leveranciers).

4. **EPEX alleen indien nodig.** Omdat elke bron zelf al een beursprijs meelevert, wordt de
   kale EPEX **niet** apart opgehaald. Alleen als een bron ooit wél een all-in maar géén
   beurs zou geven, haalt de integratie EPEX op als vangnet om de beurs af te leiden.

5. **CUSTOM: zelf de tarieven.** Kies je "Eigen leverancier", dan reken je je all-in prijs op
   basis van de EPEX + je eigen (excl. btw) opslag en energiebelasting; de btw wordt er
   automatisch overheen gerekend:

```
all-in = (EPEX + opslag + energiebelasting) × (1 + btw%)     (alle invoer excl. btw)
beurs  = EPEX × (1 + btw%)
```

Bij CUSTOM kies je ook **waar de kale EPEX vandaan komt** (easyEnergy, EnergyZero, Frank of
Essent). Voor **stroom** is de beurs bij elke bron gelijk; voor **gas** verschilt 'ie licht per
bron. Standaard staat 'ie op easyEnergy — laat dat gerust staan als je twijfelt.

## Verversen

De prijs-*array* verandert maar een paar keer per dag, dus de integratie is zuinig met de API:

- **Data ophalen:** bij opstarten, kort na middernacht (nieuwe dag), en 's middags
  (13:30/14:30/15:30/16:30) tot de prijzen van morgen binnen zijn.
- **Elk heel uur:** de sensoren rollen mee (huidige prijs, en om 06:00 de gasprijs) — **zonder**
  netwerk-call, puur uit de cache.
- **Handmatig:** de knop **"Ververs tarieven"** (`button.dyntarnl_ververs_tarieven`, op de device-pagina,
  onder Configuratie). Die kun je ook vanuit automatiseringen aanroepen via `button.press`.

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

## Tools: prijzen naar CSV (PowerShell)

Los van Home Assistant kun je met [`tools/Get-DynTarPrices.ps1`](tools/Get-DynTarPrices.ps1)
de huidige + komende uurprijs (stroom & gas, beurs/all-in/opslag) van alle platforms ophalen
en naar CSV wegschrijven (Excel-klaar, `;`-gescheiden, NL-notatie):

```powershell
.\tools\Get-DynTarPrices.ps1 -Path prijzen.csv
```

## Licentie

[MIT](LICENSE) © Maurits van Rijnen
