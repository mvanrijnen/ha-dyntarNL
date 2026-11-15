# Changelog

Alle noemenswaardige wijzigingen. Versies volgen [semver](https://semver.org/lang/nl/);
oudere versies staan op de [releases-pagina](https://github.com/mvanrijnen/ha-dyntarNL/releases).

## 1.1.0

### In het kort

- **Nieuw: prijsvoorspellingen tot 7 dagen vooruit (optioneel).** Uit twee externe bronnen,
  omgerekend met de formule van jouw eigen leverancier, met een gewogen ensemble en een
  automatische meting van hoe nauwkeurig elke bron is.
- **Nieuw: instellingen achteraf aanpassen** via *Configureren* op de integratie (options flow).
- **Nieuw: service `dyntarnl.get_prices`** voor de volledige prijsreeks per kwartier.
- **Nieuw: Nederlandse vertaling** van de configuratieschermen.
- **Opgelost:** een eigen leverancier (*Custom supplier*) toevoegen gaf "Unknown error occurred"
  ([#3](https://github.com/mvanrijnen/ha-dyntarNL/issues/3)).

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

### Opgelost

- Een eigen leverancier (*Custom supplier*) toevoegen gaf "Unknown error occurred". Dat zat ook
  al in 1.0.2 ([#3](https://github.com/mvanrijnen/ha-dyntarNL/issues/3)). Dank aan @hbvdh voor
  de analyse.

### Overig

- Nederlandse vertaling (`nl.json`), ook voor de bestaande schermen om een leverancier te kiezen.
- De opgeslagen configuratie krijgt een kleine automatische migratie (config entry-versie 1.2).
  Die is zo gemaakt dat terug naar 1.0.x kan zonder de integratie opnieuw toe te voegen.
- De Custom-berekening staat nu in één functie (`apply_formula`). De uitkomst is ongewijzigd.
