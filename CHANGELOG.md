# Changelog

Alle noemenswaardige wijzigingen. Versies volgen [semver](https://semver.org/lang/nl/);
oudere versies staan op de [releases-pagina](https://github.com/mvanrijnen/ha-dyntarNL/releases).

## 1.2.1

Alleen documentatie: de grafiekkaart en de Markdown-tabel in `docs/voorspellingen.md` tonen
nu ook de teruglevering (net als de README), en `info.md` noemt de terugleverreeks. Een test
rendert de Markdown-kaarten uit de docs in een echte Home Assistant.

## 1.2.0

### Nieuw: terugleverprijs als reeks

- `sensor.dyntarnl_e_export_price_now` heeft nu dezelfde attributen als `..._market_now`:
  `unit`, `vat_percentage`, `yesterday`, `today`, `tomorrow`, `prices` ([epoch-ms, prijs]),
  `market_price`, `purchase_fee` en `energy_tax`. De prijs in de reeksen is de
  terugleverprijs: beurs − opslag (kan negatief).
- `sensor.dyntarnl_e_all_in_forecast_avg` heeft het attribuut `forecast_export`: de
  voorspelde terugleverprijs per uur, met dezelfde tijdstempels als `forecast_market`.
- Dashboards (ApexCharts, Markdown-tabel) en andere integraties kunnen hier direct mee
  rekenen; de voorbeeldkaarten in de README tonen de teruglevering.

Er breekt niets: entity-ids en bestaande attributen blijven gelijk, en de grote reeksen gaan
niet de recorder in.

## 1.1.1

Bugfix-release: resultaat van een uitgebreide bughunt. Geen gewijzigde entiteiten of
instellingen; gewoon bijwerken.

### Prijzen

- **Storing bij de leverancier:** "nu"/"volgend uur" bleven vanaf de tweede mislukte poging de
  rest van de dag op de prijs van dat moment staan. Ze rollen nu door op de prijzen die al
  binnen waren; tijdens een storing wordt één keer per uur opnieuw geprobeerd (niet drie keer).
- **Zomer-/wintertijd:** slots worden op echte tijd vergeleken; naïeve tijden (Essent,
  Energiedirect, easyEnergy) worden correct omgezet, ook rond het dubbele/ontbrekende uur.
  easyEnergy-gas wordt per uur opgehaald.
- **Om middernacht** schuiven vandaag/morgen direct door uit de cache, vóór de nieuwe fetch.
- **Strengere controle** van de bron-data (ontbrekende of onmogelijke waarden → nette fout
  i.p.v. verkeerde prijzen); sensoren blijven beschikbaar op de laatst bekende prijzen.
- Frank Energie: aangepast aan de gewijzigde API.

### Voorspellingen

- Geen gat meer in de voorspelling voor morgen tussen de EPEX-uitslag (~13:00) en het moment
  dat je leverancier publiceert.
- "Morgen gemiddeld" toont alleen een waarde als de hele dag bekend is.
- Het goedkoopste blok begint op een heel uur en schuift niet op terwijl het loopt.
- Geen halve kolom meer aan het eind van de grafiekreeks.
- `get_prices` meldt welke reeks er in `records` zit (`forecast_series`).
- Opslag: kleiner, alleen schrijven bij wijzigingen, niets verloren bij herladen tijdens het
  inlezen, niets achtergelaten na verwijderen.
- **Bronvermelding** (attribution) op de voorspel-sensoren: EpexPredictor gebruikt
  energy-charts.info / ENTSO-E (CC-BY-4.0) en Open-Meteo.com.

### Overig

- Leverancier wijzigen (*Opnieuw configureren*) terwijl HA nog opstart, wordt direct toegepast.
- Foutmeldingen bevatten geen inloggegevens uit een eigen URL; minder logberichten bij een
  storing van een voorspeller.
- Ruim 200 tests, waaronder tests in een echte Home Assistant.

## 1.1.0

> **Dit is de versie die eerder als "2.0" werd aangekondigd**
> ([#2](https://github.com/mvanrijnen/ha-dyntarNL/issues/2)). Omdat er voor bestaande
> gebruikers niets breekt (geen gewijzigde entiteiten of instellingen, alles nieuws staat
> standaard uit), is het volgens [semver](https://semver.org/lang/nl/) een minor-release:
> **1.1.0** in plaats van 2.0.0.

### In het kort

- **Nieuw: prijsvoorspellingen tot 7 dagen vooruit (optioneel).** DynTarNL voorspelt zelf
  niets: het leest de voorspellingen van twee externe voorspellers uit, middelt ze gewogen
  (ensemble) en corrigeert ze (formule van jouw eigen leverancier, optionele biascorrectie), en
  meet automatisch hoe nauwkeurig elke bron is.
- **Nieuw: instellingen achteraf aanpassen** via *Configureren* op de integratie (options flow).
- **Nieuw: leverancier of eigen tarieven wijzigen** via *Opnieuw configureren*, zonder de
  integratie te verwijderen. Entiteiten en hun historie blijven behouden.
- **Nieuw: service `dyntarnl.get_prices`** voor de volledige prijsreeks per kwartier.
- **Ververs-knop** (en `dyntarnl.refresh`) haalt nu ook de voorspellingen direct opnieuw op.
- **Nieuw: Nederlandse vertaling** van de configuratieschermen.
- **Opgelost:** een eigen leverancier (*Custom supplier*) toevoegen gaf "Unknown error occurred"
  ([#3](https://github.com/mvanrijnen/ha-dyntarNL/issues/3)), en Frank Energie gaf geen prijzen
  meer door een gewijzigde API van Frank.

**Bestaand gedrag blijft ongewijzigd.** De voorspellingen staan standaard uit. Dan zijn er geen
extra API-calls en geen extra entiteiten, en bestaande sensoren, attributen en `entity_id`'s
blijven precies gelijk. Je bestaande installatie wordt automatisch bijgewerkt; je hoeft niets
opnieuw in te stellen.

Minimaal **Home Assistant 2024.12** (vanwege de options flow).

### Prijsvoorspellingen (optioneel)

Aan te zetten via *Instellingen → Apparaten & Services → DynTarNL → Configureren*. Volledige
uitleg: [docs/voorspellingen.md](docs/voorspellingen.md).

- **Bronnen:**
  - [EpexPredictor](https://github.com/b3nn0/EpexPredictor), ongeveer 7 dagen vooruit, met een
    instelbare URL zodat ook een eigen instantie (container of add-on) werkt;
  - [Energy Price Forecast EU](https://energypriceforecast.eu): zonder API-key 48 uur, met een
    [Private Pro](https://energypriceforecast.eu/en/private-pro/)-key 120 uur. Alleen voor
    privé, niet-commercieel gebruik.
- **Jouw eigen formule.** Van elke bron wordt alleen de kale EPEX-prijs gebruikt. Daar past
  DynTarNL de opslag, energiebelasting en btw van jouw leverancier op toe, met exact dezelfde
  berekening als voor de gewone prijzen. Een voorspelde all-in prijs is dus direct vergelijkbaar
  met je echte prijs.
- **Gepubliceerd wint altijd.** Een voorspelling vult alleen tijdvakken zonder echte prijs. Zodra
  rond 13:00 de day-ahead van morgen binnenkomt, vervangt die de voorspelling.
- **Per kwartier.** Uurwaarden worden over vier kwartieren verdeeld en gemarkeerd (`expanded`).
- **Ensemble:** een gewogen gemiddelde van de bronnen per kwartier.
  - Weging gelijk, of automatisch op gemeten nauwkeurigheid (1/MAE of 1/MAE²) per looptijd:
    0–1 dag, 2–3 dagen en 4–7 dagen.
  - Gelijke gewichten zolang er nog te weinig metingen zijn, en een minimumgewicht per bron.
  - Optioneel: biascorrectie (een structureel te hoge of te lage bron wordt gecorrigeerd).
- **Automatische nauwkeurigheidsmeting:**
  - Elke voorspelling wordt afgerekend tegen de prijs die later wordt gepubliceerd.
  - Per bron en per looptijd, en voor het ensemble ook per aantal bijdragende bronnen.
  - Over de laatste 28 dagen, met gedempte uitschieters.
  - Daaruit volgt een **verwachte fout** per voorspeld kwartier.
- **Zuinig en los van de rest.** Elke bron wordt standaard eens per 6 uur bevraagd. De
  voorspellingen hebben hun eigen onderdeel met time-outs en een oplopende wachttijd na fouten;
  een storing bij een voorspelbron raakt de gewone prijzen nooit. Na een herstart wordt niet
  opnieuw opgehaald zolang de bewaarde voorspelling nog vers is.

### Nieuwe entiteiten (alleen als de voorspellingen aan staan)

- `sensor.dyntarnl_e_cheapest_block_start`: start van het goedkoopste aaneengesloten blok van
  N uur (standaard 3) in de komende 48 uur, inclusief voorspellingen.
- `sensor.dyntarnl_e_all_in_forecast_avg`: gemiddelde all-in prijs over de komende 24 uur. Draagt
  de grafiekreeksen `prices`, `forecast`, `forecast_market` en `error_band`; die komen niet in de
  recorder, dus je database groeit er niet van.
- `sensor.dyntarnl_e_tomorrow_avg_forecast`: gemiddelde van morgen, gepubliceerd als dat er is,
  anders voorspeld.
- Diagnostiek per bron op het nieuwe device *DynTarNL Forecast*: `mae`, `bias`, `settled`,
  `weight` en `last_fetch`, plus `ensemble_mae` en `ensemble_settled`.

Zet je de voorspellingen (of een bron) weer uit, dan worden deze entiteiten automatisch
opgeruimd.

### Nieuwe service

- **`dyntarnl.get_prices`** geeft de volledige prijsreeks per kwartier terug als response data:
  kale prijs, all-in prijs, bron en (bij voorspellingen) de verwachte fout.
  - Parameters: `energy`, `include_forecast`, `provider` en `horizon`.
  - Altijd beschikbaar. Met de voorspellingen uit geeft hij alleen gepubliceerde prijzen.

### Nieuwe instellingen (*Configureren*)

| Instelling | Standaard |
| --- | --- |
| Voorspellingen aan/uit | **uit** |
| Bronnen | beide |
| Ensemble aan/uit | aan |
| Weging: gelijk of automatisch, 1/MAE of 1/MAE² | automatisch, 1/MAE |
| Nauwkeurigheidsmeting aan/uit | aan |
| Maximale horizon | 72 uur |
| Ophaalinterval | 6 uur |
| EpexPredictor-URL | publieke instantie |
| Energy Price Forecast EU API-key | geen |
| Minimum aantal afgerekende kwartieren vóór weging | 96 (één dag) |
| Minimumgewicht per bron | 10% |
| Biascorrectie | uit |
| Meetvenster | 28 dagen |
| Lengte goedkoopste blok | 3 uur |

### Grafiek en tabel

- De README en [docs/voorspellingen.md](docs/voorspellingen.md) hebben een uitgewerkte
  ApexCharts-kaart: gepubliceerde prijzen als kolommen, de voorspelling gestippeld ernaast, met
  de prijs van **nu** in de header en de legenda.
- Ook als tabel: een Markdown-kaart met een overzicht per dag en per uur, inclusief de marge
  van de voorspelling.

### Leverancier of tarieven wijzigen

- *Instellingen → Apparaten & Services → DynTarNL → ⋮ → Opnieuw configureren*: kies een
  andere leverancier, of pas bij *Custom supplier* je opslag, energiebelasting, btw en
  EPEX-bron aan. Je huidige tarieven staan al ingevuld.
- Het blijft dezelfde integratie: entity_id's, historie, statistieken en voorspel-instellingen
  blijven behouden. Alleen de bron van de prijzen verandert, vanaf dat moment.

### Opgelost

- Een eigen leverancier (*Custom supplier*) toevoegen gaf "Unknown error occurred". Dat zat ook
  al in 1.0.2 ([#3](https://github.com/mvanrijnen/ha-dyntarNL/issues/3)). Dank aan @hbvdh voor
  de analyse.
- **Zomer-/wintertijd:** bij de klokwissel gaven "nu", "volgend uur" en "vorig uur" soms een
  uur te vroeg of te laat (o.a. het dubbele uur in oktober); easyEnergy schoof op de dag van de
  zomertijd alle prijzen een uur op en miste 23:00.
- **Gas bij easyEnergy** (en Custom met easyEnergy als bron) gebruikte daggemiddelden per
  kalenderdag in plaats van de gasdag (06:00–06:00): op dagen dat de gasprijs veranderde klopte
  de prijs niet.
- **Eén mislukte ophaalactie** (bijv. om middernacht) maakte alle sensoren tot de volgende dag
  onbeschikbaar. Nu blijven de bekende prijzen beschikbaar, wordt de nieuwe dag uit de cache
  ingedeeld, wordt elk uur opnieuw geprobeerd en blijft de Ververs-knop altijd bruikbaar.
- **Gas van morgen:** DynTarNL stopte met opnieuw proberen zodra er één uur van morgen bekend
  was; nu pas als morgen tot 24:00 binnen is.
- **Energy Price Forecast EU** levert per uur nu vier verschillende kwartierwaarden; die werden
  samengevoegd tot één waarde (fouten tot ~2 ct/kWh). De diagnose-sensor toont nu ook je plan,
  horizon en of je API-key werkt.
- Kleinere fixes: een echte beursprijs van precies €0 gaf een extra (soms falende) aanroep; de
  btw-terugrekening van opslag/belasting kon bij heel lage prijzen tot 21% afwijken; EnergyZero
  miste het eerste uur van gisteren; foutmeldingen van Frank werden genegeerd; de dag-reeksen
  van de `now`-sensoren gaan niet meer de recorder in; opnieuw configureren terwijl de
  leverancier onbereikbaar is werkt direct; opties opslaan blokkeert niet meer als een
  voorspelbron even onbereikbaar is; na het verwijderen blijft geen opslagbestand achter.
- **Frank Energie** gaf geen prijzen meer: Frank weigert sinds kort de oude prijsquery
  ("Graphql validation error"). DynTarNL gebruikt nu de nieuwe `marketPrices`-query. Dit
  raakte ook Custom met Frank als bron voor de beursprijs.

### Overig

- Nederlandse vertaling (`nl.json`), ook voor de bestaande schermen om een leverancier te kiezen.
- De opgeslagen configuratie krijgt een kleine automatische migratie (config entry-versie 1.2).
  Die is zo gemaakt dat terug naar 1.0.x kan zonder de integratie opnieuw toe te voegen.
- De Custom-berekening staat nu in één functie (`apply_formula`). De uitkomst is ongewijzigd.
