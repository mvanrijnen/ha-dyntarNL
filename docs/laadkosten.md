# Laadkosten EV

DynTarNL kan bijhouden wat het laden van je elektrische auto kost. Elke geladen kWh wordt
afgerekend tegen de **all-in prijs van het moment waarop hij geladen werd**, niet tegen een
gemiddelde. Zo zie je precies wat slim laden oplevert.

> **Standaard uit.** Zonder gekozen lader-sensor verandert er niets.

- [Aanzetten](#aanzetten)
- [Hoe er gerekend wordt](#hoe-er-gerekend-wordt)
- [Sessies](#sessies)
- [Entiteiten](#entiteiten)
- [Resetten en sessies verwijderen](#resetten-en-sessies-verwijderen)
- [De kaart](#de-kaart)
- [Beperkingen](#beperkingen)

## Aanzetten

*Instellingen → Apparaten & Services → DynTarNL → **Configureren** → **Laadkosten EV***.
Kies de sensor van je lader. Wil je het weer uitzetten, maak het veld dan leeg.

Je kunt drie soorten sensoren kiezen. DynTarNL herkent het type aan de eenheid.

| Sensor | Eenheid | Voorbeeld (SMA EV Charger) | Advies |
| --- | --- | --- | --- |
| Energie per sessie | Wh / kWh | `sensor.smaev_..._charging_session_energy` | **Beste keuze**: nauwkeurig én zuivere sessies |
| Energie totaal (meterstand) | Wh / kWh | `sensor.<lader>_meter_reading` | Nauwkeurig; sessies op basis van stilstand niet beschikbaar |
| Vermogen | W / kW | `sensor.smaev_..._charging_station_power` | Werkt, maar minder nauwkeurig (zie hieronder) |

## Hoe er gerekend wordt

- **kWh-teller:** de toename tussen twee standen is het geladen verbruik.
  - Valt de teller terug (een nieuwe sessie), dan telt de nieuwe stand als verbruik sinds die
    reset.
  - Een kWh-stand loopt ook over een herstart van HA door: wat er laadde terwijl HA uit stond,
    telt mee.
- **Vermogen:** DynTarNL rekent vermogen × tijd. De laatste waarde geldt tot de volgende, en
  elke 5 minuten wordt bijgewerkt. Over een herstart heen kan dit niet: wat er gebeurde
  terwijl HA uit stond, is onbekend.
- **Prijs per kwartier.** Het verbruik in een interval wordt gelijkmatig over de kwartieren
  verdeeld, en elk kwartier krijgt de all-in prijs van zijn eigen uur. Laden over een
  uurgrens heen wordt dus correct afgerekend. Negatieve prijzen geven negatieve kosten
  (je verdient eraan).
- **Geen prijs bekend** (bijvoorbeeld als het ophalen van de tarieven mislukte)? Dan telt het
  verbruik wel mee in de kWh, maar niet in de kosten. Het staat apart in het attribuut
  `unpriced_kwh`.

## Sessies

- **Sessie-teller:** een nieuwe sessie begint zodra de teller terugvalt naar (bijna) 0. Dat
  is precies het moment waarop de lader een nieuwe sessie begint.
- **Vermogen-sensor:** een nieuwe sessie begint als er na minstens 30 minuten stilstand weer
  geladen wordt. Pauzeert slim laden langer dan 30 minuten, dan worden dat twee sessies.
- **Totaal-teller (meterstand):** die valt nooit terug, dus de sessie loopt door tot je hem
  reset.

Elke sessie krijgt als id zijn lokale starttijd, bijvoorbeeld `2026-10-04 18:05`. De laatste
100 sessies blijven bewaard.

## Entiteiten

Op het device **DynTarNL EV**:

| Entiteit | Wat |
| --- | --- |
| `sensor.dyntarnl_ev_cost_session` | Kosten van de lopende (of laatste) sessie |
| `sensor.dyntarnl_ev_cost_today` | Kosten vandaag |
| `sensor.dyntarnl_ev_cost_month` | Kosten deze maand |
| `sensor.dyntarnl_ev_cost_quarter` | Kosten dit kwartaal |
| `sensor.dyntarnl_ev_cost_year` | Kosten dit jaar |
| `sensor.dyntarnl_ev_cost_total` | Kosten totaal (sinds aanzetten of de laatste reset) |

Alle bedragen zijn all-in in euro's. Elke sensor heeft deze attributen:

- `energy_kwh`: geladen kWh;
- `avg_price`: de gemiddelde prijs per kWh die je effectief betaalde;
- `unpriced_kwh`, `source_sensor` en `measurement` (`energy` of `power`).

De sessie-sensor heeft daarnaast:

- `started`: de start van de sessie (het moment dat de teller van de lader terugviel);
- `charger_reading_kwh`: de stand van de sessie-teller van de lader zelf. Die hoort gelijk
  te zijn aan `energy_kwh`, zolang HA de hele sessie heeft gezien;
- `sessions`: de laatste 20 sessies met `id`, `start`, `end`, `kwh` en `cost`. Dit attribuut
  wordt niet in de recorder opgeslagen.

De sensoren hebben `state_class: total` met een `last_reset` per periode. Daardoor werken ze
in de langetermijnstatistieken en in een `statistics-graph`.

## Resetten en sessies verwijderen

**Resetten** zet een teller op 0:

```yaml
action: dyntarnl.reset_ev_cost
data:
  period: month    # session, day, month, quarter, year, total of all
```

**Een sessie verwijderen**, bijvoorbeeld omdat er een andere auto laadde, haalt die sessie
van **alle** tellers af: dag, maand, kwartaal, jaar en totaal.

```yaml
action: dyntarnl.delete_ev_session
data:
  session_id: "2026-10-04 18:05"   # het id uit de sessielijst, of: last
```

- `last` is de lopende sessie, of anders de laatst afgesloten sessie.
- Verwijder je de **lopende** sessie (de andere auto laadt nog), dan telt de rest van die
  sessie ook niet meer mee. De volgende sessie telt weer gewoon.
- Heb je een periode al gereset ná de start van die sessie, dan wordt de sessie daar niet
  nog eens afgetrokken. De periode blijft dan op 0 staan in plaats van negatief te worden.

## De kaart

Eén kaart met alles bij elkaar:

- een overzicht van alle perioden (kosten, kWh en gemiddelde prijs);
- de huidige sessie en de laatste sessies, met hun id;
- een staafgrafiek met de laadkosten per dag;
- knoppen om de laatste sessie te verwijderen of het totaal te resetten, allebei met een
  bevestigingsvraag.

```yaml
type: vertical-stack
cards:
  - type: markdown
    title: Laadkosten EV
    content: |
        {%- set p = 'sensor.dyntarnl_ev_cost_' -%}
        {%- macro eur(x) -%}€ {{ '%.2f' | format(x | float(0)) | replace('.', ',') }}{%- endmacro -%}
        {%- macro kwh(x) -%}{{ '%.1f' | format(x | float(0)) | replace('.', ',') }} kWh{%- endmacro -%}
        {%- macro per_kwh(x) -%}{{ ('€ ' ~ ('%.3f' | format(x)) | replace('.', ',')) if x is not none else '–' }}{%- endmacro -%}
        {%- macro dag(ts) -%}{{ ['zo', 'ma', 'di', 'wo', 'do', 'vr', 'za'][ts | timestamp_custom('%w') | int] }}{%- endmacro -%}
        {%- set started = state_attr(p ~ 'session', 'started') -%}
        {%- if started -%}
        **Huidige sessie** sinds {{ dag(as_timestamp(started)) }} {{ as_timestamp(started) | timestamp_custom('%d-%m %H:%M') }}
        {%- endif %}

        | | Kosten | Geladen | Gem. prijs |
        |:--|--:|--:|--:|
        {% for key, label in [('session', 'Sessie'), ('today', 'Vandaag'), ('month', 'Deze maand'), ('quarter', 'Dit kwartaal'), ('year', 'Dit jaar'), ('total', 'Totaal')] -%}
        {%- set e = p ~ key -%}
        | **{{ label }}** | {{ eur(states(e)) }} | {{ kwh(state_attr(e, 'energy_kwh')) }} | {{ per_kwh(state_attr(e, 'avg_price')) }} |
        {% endfor %}
        {%- set zonder = state_attr(p ~ 'total', 'unpriced_kwh') | float(0) -%}
        {%- if zonder > 0 %}
        ⚠️ {{ kwh(zonder) }} geladen zonder bekende prijs (telt niet mee in de kosten).
        {% endif %}
        ### Laatste sessies

        | Start | Eind | Geladen | Kosten | Id |
        |:--|:--|--:|--:|:--|
        {% for s in (state_attr(p ~ 'session', 'sessions') or [])[:10] -%}
        {%- set b = as_timestamp(s.start) -%}
        | {{ dag(b) }} {{ b | timestamp_custom('%d-%m %H:%M') }} | {{ as_timestamp(s.end) | timestamp_custom('%H:%M') if s.end else '–' }} | {{ kwh(s.kwh) }} | {{ eur(s.cost) }} | `{{ s.id }}` |
        {% else -%}
        | – | | | | |
        {% endfor %}
  - type: statistics-graph
    title: Laadkosten per dag
    entities:
      - entity: sensor.dyntarnl_ev_cost_total
        name: Laadkosten
    stat_types:
      - change
    chart_type: bar
    period: day
    days_to_show: 30
  - type: horizontal-stack
    cards:
      - type: button
        name: Laatste sessie verwijderen
        icon: mdi:car-off
        tap_action:
          action: perform-action
          perform_action: dyntarnl.delete_ev_session
          data:
            session_id: last
          confirmation:
            text: Laatste laadsessie verwijderen (bijv. een andere auto)?
      - type: button
        name: Totaal resetten
        icon: mdi:restart
        tap_action:
          action: perform-action
          perform_action: dyntarnl.reset_ev_cost
          data:
            period: total
          confirmation:
            text: Totale laadkosten op 0 zetten?
```

Zo werkt de kaart:

- **Een andere sessie verwijderen dan de laatste?** Kopieer het id uit de tabel en gebruik
  `dyntarnl.delete_ev_session` via *Ontwikkelhulpmiddelen → Acties*.
- **De grafiek** gebruikt de langetermijnstatistieken van HA. Die worden per uur bijgewerkt,
  dus de eerste staaf verschijnt pas na een uur.
- **Een ander aantal sessies tonen?** Verander `[:10]` in het template.

## Beperkingen

- **Statistieken achteraf:** wat al in de langetermijnstatistieken staat, kan HA niet
  herschrijven. Verwijder of reset je iets, dan zie je dat in de grafiek als een negatieve
  staaf op de dag van verwijderen. De sensoren zelf kloppen wel meteen.
- **Vermogen-sensor:** minder nauwkeurig dan een kWh-teller, en verbruik terwijl HA uit
  stond ontbreekt.
- **Sessies bij vermogen:** die zijn gebaseerd op stilstand (30 minuten). Een lange pauze
  tijdens slim laden splitst een sessie in tweeën.
- **Alleen het laadverbruik:** de prijs is je all-in prijs per kWh van je leverancier.
  Vaste kosten (vastrecht, netbeheer) en de teruglevering van zonnepanelen tellen niet mee.
