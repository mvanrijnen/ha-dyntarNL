<p align="center">
  <img src="brand/logo.svg" alt="DynTarNL" width="380">
</p>

# DynTarNL — Dynamische stroom- & gastarieven (NL)

[![Tests](https://github.com/mvanrijnen/ha-dyntarNL/actions/workflows/test.yml/badge.svg)](https://github.com/mvanrijnen/ha-dyntarNL/actions/workflows/test.yml)
[![Validate](https://github.com/mvanrijnen/ha-dyntarNL/actions/workflows/validate.yml/badge.svg)](https://github.com/mvanrijnen/ha-dyntarNL/actions/workflows/validate.yml)
[![hacs](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![release](https://img.shields.io/github/v/release/mvanrijnen/ha-dyntarNL)](https://github.com/mvanrijnen/ha-dyntarNL/releases)

Home Assistant integratie die de **dynamische energietarieven van meerdere Nederlandse
leveranciers** als sensoren publiceert. Je kiest je leverancier; de integratie bepaalt zelf
welk platform en welke publieke prijs-API erbij hoort. **Geen account of API-sleutel nodig**
voor de ondersteunde leveranciers.

> ## Stabiel — klaar voor dagelijks gebruik
>
> De integratie draait in productie en de entiteiten liggen vast: vanaf 1.0 verandert er niets
> meer aan bestaande `entity_id`'s zonder een major release. Niet elke leverancier is even
> uitgebreid in de praktijk beproefd — wijkt een prijs af, meld het gerust.
> Onofficieel; geen affiliatie met de genoemde leveranciers.
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
De namen zijn **compact & Engels** (alleen `a-z0-9_`). Je krijgt drie devices:
**DynTarNL E** (stroom), **DynTarNL G** (gas) en **DynTarNL** (voor de knop). De gekozen
leverancier staat als *fabrikant* op de device-pagina. Bedragen incl. btw, in €/kWh (stroom)
of €/m³ (gas).

> Stroom is per uur; **gas** volgt de Nederlandse **gasdag** (06:00–06:00): de prijs verspringt
> om 06:00 en is daartussen constant.

### Prijs-sensoren (import) — stroom & gas, all-in & beurs

`X` = `e` (stroom) of `g` (gas), `Y` = `all_in` of `market`.

| entity_id | Betekenis |
| --- | --- |
| `sensor.dyntarnl_X_Y_prev` | Prijs van het vorige uur |
| `sensor.dyntarnl_X_Y_now` | Prijs van het huidige uur (met `today`/`tomorrow` als attribuut) |
| `sensor.dyntarnl_X_Y_next` | Prijs van het eerstvolgende uur |
| `sensor.dyntarnl_X_Y_today_min` / `_today_avg` / `_today_max` | Laagste / gemiddelde / hoogste vandaag |
| `sensor.dyntarnl_X_Y_tomorrow_min` / `_tomorrow_max` | Laagste / hoogste morgen (leeg tot gepubliceerd) |

Bijv. `sensor.dyntarnl_e_all_in_now`, `sensor.dyntarnl_g_market_today_avg`.

### Component-sensoren (opbouw huidig uur)

| entity_id | Betekenis |
| --- | --- |
| `sensor.dyntarnl_X_tax_incl_vat` / `_tax_excl_vat` | Energiebelasting per eenheid |
| `sensor.dyntarnl_X_markup_incl_vat` / `_markup_excl_vat` | Opslag (markup) van de leverancier |

### Export-sensoren (teruglevering, alleen stroom = `e`)

| entity_id | Waarde |
| --- | --- |
| `sensor.dyntarnl_e_export_price_now` | €/kWh voor export dit uur (kan negatief) |
| `sensor.dyntarnl_e_export_cost_now` / `_export_cost_next` | €/kWh die export kost (0 = kost niets) |
| `sensor.dyntarnl_e_neg_hours_today` | aantal uren met beursprijs < 0 |
| `sensor.dyntarnl_e_export_loss_today` | aantal uren dat export geld kost |

### Binary sensors (triggers, alleen stroom = `e`)

| entity_id | Aan wanneer |
| --- | --- |
| `binary_sensor.dyntarnl_e_neg_price_now` / `_neg_price_prev` / `_neg_price_next` | beursprijs < 0 |
| `binary_sensor.dyntarnl_e_export_loss_now` / `_export_loss_next` | export levert niets op (beurs ≤ opslag) |
| `binary_sensor.dyntarnl_e_tomorrow_available` / `..._g_...` | prijzen van morgen gepubliceerd |

### Knop / actions

| Aanroep | Wat |
| --- | --- |
| `button.dyntarnl_refresh` (via `button.press`) | Knop op de device-pagina; verse data ophalen |
| **`dyntarnl.refresh`** | Service — overal aanroepbaar, ververst de tarieven direct |
| **`dyntarnl.get_prices`** | Service met response data — de volledige prijsreeks, optioneel met voorspellingen (zie [Prijsvoorspellingen](#prijsvoorspellingen-optioneel)) |

```yaml
# overal aanroepbaar, bijv. in een automatisering of script:
action: dyntarnl.refresh
```

### Attributen op de `now`-prijssensor

De `now`-prijssensoren dragen alle uren mee die de integratie in cache heeft:

- `prices` — **kant-en-klare grafiekreeks** over álle dagen: lijst van `[epoch-ms, prijs]`
- `yesterday` / `today` / `tomorrow` — lijst van `{ start, end, price }`, handig in templates
  (`yesterday` en `tomorrow` zijn `null` zolang die dag er niet is)
- `market_price`, `purchase_fee`, `energy_tax` — opbouw van het huidige uur
- `unit`, `vat_percentage`

De prijs is steeds die van de sensor zelf: op `..._all_in_now` staan de all-in prijzen, op
`..._market_now` de beursprijzen.

### Voorbeeld-kaart: all-in én beurs in één grafiek

`prices` staat al in het formaat dat [ApexCharts](https://github.com/RomRider/apexcharts-card)
verwacht, dus de `data_generator` is per serie één regel. Elke `now`-sensor draagt zijn eigen
reeks: `..._all_in_now` de all-in prijzen, `..._market_now` de kale beurs.

![Grafiek: all-in prijzen als kolommen, beursprijs als lijn, gisteren t/m morgen](docs/priceoverview.png)

> De kaart ververst zichzelf elke 5 minuten. Dat is nodig omdat apexcharts-card standaard
> alleen hertekent bij een **state**-wijziging, terwijl deze grafiek uit de **attributen** leest:
> zonder `update_interval` verschijnen de prijzen van morgen pas zodra de prijs van het huidige
> uur verandert.

*De kolommen zijn de all-in prijs — groen onder €0,25, geel daarboven. De blauwe stepline is de
kale beurs; het verschil ertussen is je opslag + energiebelasting. De stippellijn is `nu`. Het
laatste etmaal is nog leeg omdat de prijzen van morgen op dat moment nog niet gepubliceerd waren.*

```yaml
type: custom:apexcharts-card
grid_options:
  columns: full          # volle breedte in de sections-weergave
experimental:
  color_threshold: true
header:
  show: true
  title: Stroom (€/kWh)
  show_states: true
  colorize_states: true
update_interval: 5min    # kaart leest attributen; zonder dit hertekent hij pas
                         # als de prijs van het huidige uur verandert
graph_span: 72h          # gisteren + vandaag + morgen
span:
  start: day
  offset: -1d            # begin bij gisteren 00:00, anders valt die dag buiten beeld
now:
  show: true
  label: nu
  color: var(--error-color)
yaxis:
  - min: ~0              # zachte nul: zakt mee als de beurs negatief wordt
    max: ~0.40
    decimals: 2
apex_config:
  chart:
    height: 320px
  tooltip:
    x:
      format: ddd d MMM - HH:mm
  xaxis:
    labels:
      format: ddd HH:mm  # zonder dagnaam leest elk label '00:00'
series:
  - entity: sensor.dyntarnl_e_all_in_now
    name: all-in
    type: column
    extend_to: false     # niet doortrekken tot het eind van het venster
    float_precision: 3
    unit: " €/kWh"
    show:
      extremas: true
      header_color_threshold: true
    color_threshold:
      - value: -1
        color: "#1b5e20"
      - value: 0
        color: "#43a047"
      - value: 0.25
        color: "#fbc02d"
      - value: 0.40
        color: "#e53935"
    data_generator: |
      return entity.attributes.prices;
  - entity: sensor.dyntarnl_e_market_now
    name: beurs
    type: line
    curve: stepline
    stroke_width: 2
    color: var(--primary-color)
    extend_to: false
    float_precision: 3
    unit: " €/kWh"
    data_generator: |
      return entity.attributes.prices;
```

Het verschil tussen de lijn en de kolommen is precies je opslag + energiebelasting.

**Alleen de beurs?** Laat de eerste serie weg en zet de drempels lager — beursprijzen liggen
een stuk dichter bij nul en duiken er regelmatig onder:

```yaml
    color_threshold:
      - value: -0.02
        color: "#1b5e20"
      - value: 0
        color: "#43a047"
      - value: 0.10
        color: "#fbc02d"
      - value: 0.20
        color: "#e53935"
```

Voor gas werkt dezelfde kaart met `sensor.dyntarnl_g_all_in_now` / `..._g_market_now`; die
tekent blokken van de gasdag (06:00–06:00). Zolang de prijzen van morgen nog niet gepubliceerd
zijn blijft het laatste etmaal leeg.

### Voorbeeld-automatisering: ZeroExport bij ongunstige teruglevering

```yaml
automation:
  - alias: ZeroExport aan/uit op teruglevering
    trigger:
      - platform: state
        entity_id: binary_sensor.dyntarnl_e_export_loss_now
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

- **Data ophalen:** bij opstarten, kort na middernacht (nieuwe dag), en vanaf 13:00 elk half
  uur **tot de prijzen van morgen binnen zijn** — daarna stopt het vanzelf tot de volgende dag.
  Stroom komt meestal rond het middaguur binnen, gas vaak pas 's avonds; daarom loopt het
  doorproberen door tot 23:59. Het exacte moment binnen dat halve uur verschilt per
  installatie, zodat niet iedereen tegelijk bij de leverancier aanklopt.
- **Elk heel uur:** de sensoren rollen mee (huidige prijs, en om 06:00 de gasprijs) — **zonder**
  netwerk-call, puur uit de cache.
- **Handmatig:** de knop **"Refresh"** (`button.dyntarnl_refresh`, op de device-pagina,
  onder Configuratie). Die kun je ook vanuit automatiseringen aanroepen via `button.press`.

## Prijsvoorspellingen (optioneel)

Vanaf 2.0 kan DynTarNL de gepubliceerde day-ahead prijzen aanvullen met **voorspellingen tot
7 dagen vooruit**. De optie staat **standaard uit**. Zolang hij uit staat verandert er niets:
geen extra API-calls, geen extra entiteiten en geen andere sensoren of attributen.

> 📖 De volledige uitleg (alle opties, de berekeningen, de grafiek en problemen oplossen) staat
> in [docs/voorspellingen.md](docs/voorspellingen.md).

**Aanzetten:** *Instellingen → Apparaten & Services → DynTarNL → Configureren*. Dat kan op elk
moment, zonder de integratie opnieuw toe te voegen.

### Hoe het werkt

- **Bronnen** (meer kunnen later worden toegevoegd):
  - [EpexPredictor](https://github.com/b3nn0/EpexPredictor), regio NL, ongeveer 7 dagen
    vooruit. De URL is instelbaar, zodat je ook een eigen instantie kunt gebruiken (lokale
    container of add-on).
  - [Energy Price Forecast EU](https://energypriceforecast.eu), markt NL. Zonder API-key
    maximaal 48 uur, met (optionele) key tot 120 uur.
- **Altijd de kale EPEX-prijs.** De opslag- en btw-opties van de bronnen worden niet gebruikt.
  DynTarNL past er de formule van jouw leverancier op toe, met **exact dezelfde functie** als
  voor CUSTOM:

  ```text
  all-in = (EPEX + opslag + energiebelasting) × (1 + btw%)
  ```

  Opslag, energiebelasting en btw komen uit het laatst gepubliceerde uur van je leverancier.
  Negatieve prijzen gaan lineair mee, net als bij de leveranciers zelf.
- **Gepubliceerd wint altijd.** Een voorspelling vult alleen kwartieren in waarvoor (nog) geen
  echte prijs is. Zodra de day-ahead binnenkomt vervangt die de voorspelling. Day-ahead-waarden
  die een bron zelf meelevert worden genegeerd: "gepubliceerd" komt altijd van je leverancier.
- **Per kwartier.** Gepubliceerde uurprijzen en voorspelde uurwaarden worden over vier
  kwartieren verdeeld en als `expanded` gemarkeerd.
- **Alleen stroom.** Geen van de bronnen voorspelt gas.

### Ensemble en nauwkeurigheid

- **Ensemble:** per kwartier een gewogen gemiddelde van de bronnen die voor dat kwartier een
  waarde hebben. Heeft maar één bron een waarde, dan wordt die gebruikt (`n_sources = 1`). De
  losse bronreeksen blijven ook beschikbaar.
- **Weging:** "gelijk", of "automatisch op nauwkeurigheid".
  - Bij automatisch is het gewicht ~ 1/MAE (of 1/MAE²) per looptijd-bucket: 0–1 dag,
    2–3 dagen en 4–7 dagen.
  - Zolang een bron te weinig afgerekende kwartieren heeft (standaard 96, één dag) wegen alle
    bronnen even zwaar.
  - Elke bron houdt een minimumgewicht (standaard 10%), zodat hij na een slechte periode niet
    wegvalt.
  - Optioneel: **biascorrectie**. Dan wordt de gemeten systematische fout per bron eerst
    afgetrokken.
- **Nauwkeurigheidsmeting:**
  - Elke opgehaalde voorspelling wordt bewaard en afgerekend zodra de echte prijs binnenkomt.
  - Dat gebeurt altijd op kale prijzen, want opslag en belasting voegen geen onzekerheid toe.
  - De leverancier publiceert per uur, dus een uur wordt afgerekend als het gemiddelde van
    zijn vier voorspelde kwartieren.
  - **Venster:** de laatste 28 dagen (instelbaar). Het ensemble wordt per bucket én per aantal
    bijdragende bronnen afgerekend. Of meer bronnen echt helpen wordt dus gemeten, niet
    aangenomen.
  - **Uitschieters** zijn begrensd op 0,15 €/kWh per kwartier.
- **Verwachte fout** per voorspeld kwartier: `√(MAE² + (spreiding/2)²)`.
  - MAE is de gemeten ensemble-fout voor deze looptijd en dit aantal bronnen.
  - De spreiding is het verschil tussen de bronnen nu.
  - All-in = kale marge × btw-factor.
- **Opslag:** de gegevens staan in HA-storage (`.storage/dyntarnl.forecast_<entry>`) en
  overleven een herstart. Ruwe voorspellingen blijven alleen bewaard tot ze zijn afgerekend;
  daarna alleen dag-totalen binnen het venster.

### Ophalen en fair use

Elke bron wordt standaard **eens per 6 uur** bevraagd (instelbaar van 2 tot 24 uur). De
voorspellingen hebben een eigen coordinator met eigen time-outs (20 s) en een oplopende
wachttijd na fouten (30 min → 12 uur). **Een storing bij een voorspelbron heeft nooit invloed
op het ophalen of tonen van de gepubliceerde prijzen.**

### Entiteiten (alleen als de optie aan staat)

| Entiteit | Wat |
| --- | --- |
| `sensor.dyntarnl_e_cheapest_block_start` | Start van het goedkoopste aaneengesloten blok van N uur (standaard 3) in de komende 48 uur, inclusief voorspellingen. Attributen: `end`, `avg_price_allin`, `source` (`published`/`forecast`/`mixed`), `expected_error_allin` |
| `sensor.dyntarnl_e_all_in_forecast_avg` | Gemiddelde all-in prijs over de komende 24 uur. Draagt de volledige reeks `prices` (`[epoch-ms, all-in, "p"\|"f"]`) en `error_band` voor grafieken. Die attributen worden **niet** in de recorder opgeslagen |
| `sensor.dyntarnl_e_tomorrow_avg_forecast` | Gemiddelde all-in van morgen: gepubliceerd als dat er is, anders voorspeld (met `source`) |
| `sensor.dyntarnl_forecast_<bron>_mae` / `_bias` / `_settled` / `_weight` / `_last_fetch` | Diagnostiek per bron: gemeten fout, systematische fout, aantal afgerekende kwartieren, huidig gewicht en ophaalstatus |
| `sensor.dyntarnl_forecast_ensemble_mae` / `_settled` | Gemeten fout van het ensemble, per bucket en per aantal bronnen |

Zet je de optie uit (of een bron), dan worden de bijbehorende entiteiten automatisch opgeruimd.

### Voorspelling in de grafiek

De sensor `sensor.dyntarnl_e_all_in_forecast_avg` heeft twee kant-en-klare reeksen met **alleen
de voorspelde kwartieren**:

- `forecast`: de all-in prijs;
- `forecast_market`: de beursprijs incl. btw, net als de reeks van `..._market_now`.

Je kunt ze dus naast de bestaande voorbeeldkaart zetten. Gepubliceerde uren blijven
kolommen. De voorspelling wordt een **gestippelde stepline met een lichte vulling**, in
dezelfde kleurdrempels.

De voorspelling is bewust geen tweede kolomserie: ApexCharts zou de kolombreedte dan over
twee series verdelen, en alle kolommen worden half zo smal.

Neem de [voorbeeldkaart](#voorbeeld-kaart-all-in-én-beurs-in-één-grafiek) en pas twee dingen aan:

1. `graph_span: 120h`: gisteren + vandaag + drie dagen vooruit.
2. Zet de volgende twee series onder `series:`:

```yaml
  - entity: sensor.dyntarnl_e_all_in_forecast_avg
    name: all-in (voorspeld)
    type: area
    curve: stepline
    stroke_width: 2
    stroke_dash: 4         # gestippeld = voorspeld
    opacity: 0.25          # lichte vulling, de kolommen blijven leidend
    extend_to: false
    float_precision: 3
    unit: " €/kWh"
    show:
      in_header: false     # header blijft de gepubliceerde prijs van nu tonen
    color_threshold:       # zelfde drempels als de kolommen
      - value: -1
        color: "#1b5e20"
      - value: 0
        color: "#43a047"
      - value: 0.25
        color: "#fbc02d"
      - value: 0.40
        color: "#e53935"
    data_generator: |
      return entity.attributes.forecast || [];
  - entity: sensor.dyntarnl_e_all_in_forecast_avg
    name: beurs (voorspeld)
    type: line
    curve: stepline
    stroke_width: 2
    stroke_dash: 4
    color: var(--primary-color)
    extend_to: false
    float_precision: 3
    unit: " €/kWh"
    show:
      in_header: false
    data_generator: |
      return entity.attributes.forecast_market || [];
```

Wil je ook de onzekerheid zien? `error_band` geeft per voorspeld kwartier
`[epoch-ms, laag, hoog]` (all-in ± verwachte fout). Twee dunne lijnen maken daar een band van:

```yaml
  - entity: sensor.dyntarnl_e_all_in_forecast_avg
    name: marge
    type: line
    curve: stepline
    stroke_width: 1
    stroke_dash: 2
    color: "#9e9e9e"
    extend_to: false
    show:
      in_header: false
      in_legend: false
    data_generator: |
      return (entity.attributes.error_band || []).map(([t, lo]) => [t, lo]);
  - entity: sensor.dyntarnl_e_all_in_forecast_avg
    name: marge
    type: line
    curve: stepline
    stroke_width: 1
    stroke_dash: 2
    color: "#9e9e9e"
    extend_to: false
    show:
      in_header: false
      in_legend: false
    data_generator: |
      return (entity.attributes.error_band || []).map(([t, , hi]) => [t, hi]);
```

### Service `dyntarnl.get_prices`

De belangrijkste manier om volledige reeksen op te halen. Werkt ook met de optie uit; dan
krijg je alleen gepubliceerde prijzen terug.

```yaml
action: dyntarnl.get_prices
data:
  energy: electricity        # of gas (alleen gepubliceerd, per gasdag)
  include_forecast: true
  provider: ensemble         # of epexpredictor / energypriceforecast / all
  horizon: 72                # uren vanaf nu (optioneel)
response_variable: prijzen
```

Elk record heeft de velden:

- `start`, `end`, `price_raw` (kaal, excl. btw), `price_allin`, `source`, `expanded`
- bij gepubliceerde prijzen: `supplier`
- bij voorspellingen: `providers`, `fetched_at`, `n_sources`, `spread_min`, `spread_max`,
  `expected_error_raw` en `expected_error_allin`

### Beperkingen

- **Alle bronnen gebruiken dezelfde weerdata** (wind, zon, temperatuur). Hun fouten zijn dus
  gecorreleerd: als het weerbericht ernaast zit, zitten ze er vaak allemaal naast. Het
  ensemble middelt modelverschillen uit, geen weersonzekerheid. De gemeten fout per aantal
  bronnen laat zien hoeveel het in de praktijk scheelt.
- Een voorspelling over 1 januari heen rekent nog met de energiebelasting van het oude jaar.
- Een voorspelling blijft een voorspelling. Gebruik hem om te plannen, niet om af te rekenen.
- Energy Price Forecast EU heeft geen gedocumenteerde publieke API-spec. DynTarNL gebruikt
  het endpoint van hun eigen HA-integratie en controleert het antwoordformaat. Verandert dat,
  dan gaat alleen die bron in storing.

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
