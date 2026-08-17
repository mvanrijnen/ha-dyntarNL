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

## Entiteiten

Na installatie krijg je per energietype een device: **`DynTarNL <leverancier> Stroom`** en
**`DynTarNL <leverancier> Gas`**. Alle bedragen zijn in €/kWh (stroom) of €/m³ (gas).

> Stroom is per uur; **gas** volgt de Nederlandse **gasdag** (06:00–06:00): de prijs verspringt
> om 06:00 en is daartussen constant.

### Prijs-sensoren

Voor **Stroom** én **Gas**, en telkens in twee smaken — **all-in** (wat je betaalt) en **beurs**
(kale EPEX-marktprijs):

| Sensor | Betekenis |
| --- | --- |
| `… vorig uur` | Prijs van het vorige uur |
| `… huidige prijs` | Prijs van het huidige uur (draagt `today`/`tomorrow` arrays als attribuut) |
| `… volgend uur` | Prijs van het eerstvolgende uur |
| `… vandaag laagste` / `… vandaag gemiddeld` / `… vandaag hoogste` | Dagstatistiek vandaag |
| `… morgen laagste` / `… morgen hoogste` | Morgen (leeg tot de day-ahead prijzen 's middags binnen zijn) |

Voorbeeld entity-id: `sensor.dyntarnl_<leverancier>_stroom_all_in_huidige_prijs`.

### Component-sensoren

De opbouw van het huidige uur, per energietype, **incl. én excl. btw**:

| Sensor | Betekenis |
| --- | --- |
| `energiebelasting incl/excl btw` | Overheidsheffing per kWh/m³ |
| `inkoopvergoeding incl/excl btw` | Opslag van de leverancier per kWh/m³ |

### Attributen op "huidige prijs"

De `huidige prijs`-sensoren dragen de volledige dag-arrays mee, klaar voor
[ApexCharts](https://github.com/RomRider/apexcharts-card):

- `today` / `tomorrow` — lijst van `{ start, end, price }`
- `market_price`, `purchase_fee`, `energy_tax` — opbouw van het huidige uur
- `unit`, `vat_percentage`

## Triggers — teruglevering & negatieve prijzen (alleen stroom)

Bij lage/negatieve prijzen loont terugleveren niet meer. Deze entiteiten zijn bedoeld om er
**direct op te schakelen** — bv. ZeroExport op een PV-omvormer inschakelen of een accu
geforceerd laten laden.

**Binary sensors** (perfecte automatiserings-triggers)

| Entiteit | Aan wanneer |
| --- | --- |
| `prijs negatief nu` / `… vorig uur` / `… volgend uur` | beursprijs < 0 |
| `terugleveren kost geld nu` / `… volgend uur` | terugleververgoeding < 0 (beursprijs ≤ opslag) |
| `morgen beschikbaar` (stroom & gas) | de prijzen van morgen zijn gepubliceerd |

**Sensors**

| Entiteit | Waarde |
| --- | --- |
| `terugleververgoeding nu` | €/kWh die je krijgt voor export (kan negatief zijn) |
| `terugleverkosten nu` / `… volgend uur` | €/kWh die export je kost (0 als het niets kost) |
| `negatieve uren vandaag` | aantal uren met beursprijs < 0 |
| `uren terugleveren kost geld vandaag` | aantal uren dat export geld kost |

### Voorbeeld-automatisering: ZeroExport bij ongunstige teruglevering

```yaml
automation:
  - alias: ZeroExport aan bij negatieve teruglevering
    trigger:
      - platform: state
        entity_id: binary_sensor.dyntarnl_<leverancier>_stroom_terugleveren_kost_geld_nu
        to: "on"
    action:
      - service: switch.turn_on
        target: { entity_id: switch.omvormer_zero_export }
  - alias: ZeroExport uit als teruglevering weer loont
    trigger:
      - platform: state
        entity_id: binary_sensor.dyntarnl_<leverancier>_stroom_terugleveren_kost_geld_nu
        to: "off"
    action:
      - service: switch.turn_off
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

## Verversen

De prijs-*array* verandert maar een paar keer per dag, dus de integratie is zuinig met de API:

- **Data ophalen:** bij opstarten, kort na middernacht (nieuwe dag), en 's middags
  (13:30/14:30/15:30/16:30) tot de prijzen van morgen binnen zijn.
- **Elk heel uur:** de sensoren rollen mee (huidige prijs, en om 06:00 de gasprijs) — **zonder**
  netwerk-call, puur uit de cache.
- **Handmatig:** elke leverancier heeft een knop **"Ververs tarieven"** (op de device-pagina,
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
