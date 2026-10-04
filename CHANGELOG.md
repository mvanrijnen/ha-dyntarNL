# Changelog

Alle noemenswaardige wijzigingen. Versies volgen [semver](https://semver.org/lang/nl/);
oudere versies staan op de [releases-pagina](https://github.com/mvanrijnen/ha-dyntarNL/releases).

## 2.0.0

**Bestaand gedrag blijft ongewijzigd zolang de nieuwe voorspel-optie uit staat**, en die staat
standaard uit. Dan zijn er geen extra API-calls, geen extra entiteiten en geen wijzigingen in
bestaande sensoren, attributen of `entity_id`'s.

### Nieuw: prijsvoorspellingen (optioneel)

- EPEX-prijsvoorspellingen voorbij de day-ahead, tot 7 dagen vooruit, uit externe bronnen:
  - **EpexPredictor**, met een instelbare URL voor een eigen instantie;
  - **Energy Price Forecast EU**, met een optionele API-key.
- Een provider-abstractie, zodat later een bron kan worden toegevoegd zonder de rest te wijzigen.
- Altijd de **kale** EPEX-prijs. De leveranciersformule wordt toegepast met exact dezelfde
  functie als voor CUSTOM (`apply_formula`).
- **Gepubliceerde prijzen winnen altijd.** Een voorspelling vult alleen kwartieren zonder
  gepubliceerde prijs.
- Het datamodel werkt per kwartier. Uurwaarden worden over vier kwartieren verdeeld en
  gemarkeerd (`expanded`).
- **Ensemble:** een gewogen gemiddelde per kwartier.
  - Gewichten: gelijk, of automatisch ~ 1/MAE (of 1/MAE²) per looptijd-bucket.
  - Een minimum aantal metingen voordat er gewogen wordt, en een ondergrens per gewicht.
  - Optionele biascorrectie.
- **Automatische nauwkeurigheidsmeting:**
  - Elke voorspelling wordt afgerekend tegen de later gepubliceerde prijs, op kale prijzen.
  - Per bron en per looptijd-bucket; het ensemble ook per aantal bijdragende bronnen.
  - Venster van 28 dagen, met begrensde uitschieters.
  - Een verwachte fout per voorspeld kwartier.
- De gegevens worden opgeslagen in HA-storage en overleven een herstart.
- Een **aparte coordinator** met eigen time-outs en backoff. Een storing bij een voorspelbron
  raakt de gepubliceerde prijzen nooit.

### Nieuwe options (*Configureren* op de integratie)

| Instelling | Standaard |
| --- | --- |
| Voorspellingen aan/uit | **uit** |
| Bronnen | beide |
| Ensemble aan/uit | aan |
| Weging: gelijk of automatisch op nauwkeurigheid, 1/MAE of 1/MAE² | automatisch, 1/MAE |
| Nauwkeurigheidsmeting aan/uit | aan |
| Maximale horizon | 72 uur |
| Ophaalinterval | 6 uur |
| EpexPredictor-URL | publieke instantie |
| Energy Price Forecast EU API-key | geen |
| Minimum aantal afgerekende kwartieren vóór weging | 96 |
| Minimumgewicht per bron | 10% |
| Biascorrectie | uit |
| Meetvenster | 28 dagen |
| Lengte goedkoopste blok | 3 uur |

### Nieuwe service

- **`dyntarnl.get_prices`** geeft response data terug: de volledige prijsreeks per kwartier,
  optioneel met voorspellingen.
  - Parameters: `energy`, `include_forecast`, `provider` en `horizon`.
  - De service is altijd beschikbaar. Met de optie uit geeft hij alleen gepubliceerde prijzen.

### Nieuwe entiteiten (alleen als de optie aan staat)

- `sensor.dyntarnl_e_cheapest_block_start` — het goedkoopste blok van N uur in de komende
  48 uur, inclusief voorspellingen.
- `sensor.dyntarnl_e_all_in_forecast_avg` — het gemiddelde over de komende 24 uur. De reeksen
  `prices`, `forecast`, `forecast_market` en `error_band` staan in attributen die niet in de
  recorder komen. De README heeft een ApexCharts-voorbeeld dat de voorspelling gestippeld naast
  de gepubliceerde kolommen tekent.
- `sensor.dyntarnl_e_tomorrow_avg_forecast` — het gemiddelde van morgen, gepubliceerd of voorspeld.
- Diagnostiek per bron op het nieuwe device *DynTarNL Forecast*: `mae`, `bias`, `settled`,
  `weight` en `last_fetch`.
- Diagnostiek voor het ensemble: `ensemble_mae` en `ensemble_settled`.

### Overig

- Een nieuwe documentatiepagina [docs/voorspellingen.md](docs/voorspellingen.md) legt de
  voorspellingen, alle opties, de berekeningen en de grafiek uit.
- Een config entry gaat van versie 1.1 naar 1.2 en migreert automatisch: de nieuwe opties
  krijgen hun standaardwaarde (uit). Het is een minor-versie, dus terug naar 1.x kan zonder de
  integratie opnieuw toe te voegen.
- Er is nu een Nederlandse vertaling (`nl.json`), ook voor de bestaande config flow.
- De CUSTOM-berekening staat nu in één functie, `apply_formula`. De uitkomst is ongewijzigd.
- Minimaal Home Assistant 2024.12, vanwege de options flow.
