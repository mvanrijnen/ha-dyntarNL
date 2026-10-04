# Prijsvoorspellingen in DynTarNL

Vanaf versie 2.0 kan DynTarNL de gepubliceerde day-ahead stroomprijzen aanvullen met
**voorspellingen tot 7 dagen vooruit**. Deze pagina legt uit hoe dat werkt, wat je kunt
instellen, welke entiteiten je krijgt, hoe je de voorspelling in een grafiek zet en hoe je
die grafiek leest.

> **Standaard uit.** Zolang je de optie niet aanzet verandert er niets: geen extra API-calls,
> geen extra entiteiten en geen andere sensoren of attributen.

- [Waarom voorspellingen?](#waarom-voorspellingen)
- [Hoe het werkt](#hoe-het-werkt)
- [Aanzetten en instellen](#aanzetten-en-instellen)
- [Ensemble en weging](#ensemble-en-weging)
- [Nauwkeurigheid en verwachte fout](#nauwkeurigheid-en-verwachte-fout)
- [Entiteiten](#entiteiten)
- [Service `dyntarnl.get_prices`](#service-dyntarnlget_prices)
- [De grafiek](#de-grafiek) (en [als tabel](#als-tabel))
- [Ophalen, fair use en storingen](#ophalen-fair-use-en-storingen)
- [Beperkingen](#beperkingen)
- [Problemen oplossen](#problemen-oplossen)

---

## Waarom voorspellingen?

De day-ahead prijzen van morgen worden rond 13:00 gepubliceerd. Daarvoor weet je dus
hooguit tot middernacht wat stroom kost. Voor sommige beslissingen wil je verder vooruit
kijken:

- Laad je de auto vannacht of pas morgenmiddag?
- Moet de thuisaccu vandaag vol, of wordt het morgen goedkoper?
- Kan de wasmachine/warmtepomp beter vandaag of overmorgen?

Voorspellingen geven daar een onderbouwde schatting voor, mét een indicatie hoe zeker die is.

## Hoe het werkt

**Bronnen.** DynTarNL haalt voorspellingen op bij externe diensten. Er zijn er nu twee; het
ontwerp maakt het makkelijk om er later meer toe te voegen.

| Bron | Horizon | Instellingen |
| --- | --- | --- |
| [EpexPredictor](https://github.com/b3nn0/EpexPredictor) | ± 7 dagen, per kwartier | URL instelbaar: de publieke instantie of je eigen (container of add-on) |
| [Energy Price Forecast EU](https://energypriceforecast.eu) | 48 uur zonder key, 120 uur met key | Optionele API-key |

**Altijd de kale prijs, jouw formule.** Van elke bron haalt DynTarNL alleen de kale
EPEX-prijs op. De opslag- en btw-opties van die diensten gebruiken we bewust niet. Op de kale
prijs past DynTarNL de formule van jouw leverancier toe, met **exact dezelfde functie** als
voor de gewone prijzen:

```text
all-in = (EPEX + opslag + energiebelasting) × (1 + btw%)
```

Opslag, energiebelasting en btw komen uit het laatst gepubliceerde uur van je leverancier
(bij *Custom* uit wat je zelf hebt ingevuld). Een voorspelde all-in prijs is dus direct
vergelijkbaar met je echte all-in prijs. Negatieve beursprijzen gaan lineair mee, precies zoals
je leverancier ze afrekent.

**Gepubliceerd wint altijd.** Een voorspelling vult alleen tijdvakken in waarvoor nog geen
echte prijs bekend is. Komt rond 13:00 de day-ahead van morgen binnen, dan vervangt die de
voorspelling direct. Een gepubliceerde prijs wordt nooit door een voorspelling overschreven.
Day-ahead-waarden die een bron zelf meelevert worden genegeerd: "gepubliceerd" komt altijd
van je eigen leverancier.

**Per kwartier.** Intern werkt alles per kwartier. Waar een bron (of je leverancier) een
uurprijs levert, wordt die over de vier kwartieren van dat uur verdeeld en gemarkeerd als
`expanded`.

**Alleen stroom.** Geen van de bronnen voorspelt gasprijzen.

## Aanzetten en instellen

*Instellingen → Apparaten & Services → DynTarNL → **Configureren***. Dit kan op elk moment;
na opslaan herlaadt de integratie zichzelf. Na een **update** van DynTarNL zelf moet je Home
Assistant wel herstarten.

Het formulier heeft vier stappen.

**1. Prijsvoorspellingen.** De hoofdschakelaar. Zet je hem uit, dan wordt direct opgeslagen.
Je overige keuzes blijven bewaard voor als je hem later weer aanzet.

**2. Instellingen voorspellingen.**

| Instelling | Standaard | Uitleg |
| --- | --- | --- |
| Bronnen | beide | Welke bronnen worden bevraagd |
| Bronnen combineren (ensemble) | aan | Uit = alleen de losse bronreeksen; de sensoren nemen dan per kwartier de eerste bron in de lijst met een waarde |
| Weging | automatisch | *Automatisch*: wie nauwkeuriger blijkt weegt zwaarder. *Gelijk*: altijd even zwaar |
| Gewicht naar | 1/MAE | 1/MAE² beloont nauwkeurigheid sterker |
| Nauwkeurigheid meten | aan | Nodig voor automatische weging en voor de verwachte fout |
| Maximale horizon | 72 uur | Hoe ver vooruit (max. 168) |
| Ophaalinterval | 6 uur | Hoe vaak elke bron wordt bevraagd (2–24 uur) |

**3. Instellingen per bron.** De EpexPredictor-URL en de optionele API-key voor Energy Price
Forecast EU. Beide worden met één testcall gecontroleerd. Leeg laten van de key mag; dan geldt
de gratis horizon van 48 uur.

**4. Geavanceerd.**

| Instelling | Standaard | Uitleg |
| --- | --- | --- |
| Minimum afgerekende kwartieren vóór weging | 96 (= 1 dag) | Tot elke bron zoveel metingen heeft, wegen alle bronnen gelijk |
| Minimumgewicht per bron | 0,10 | Een bron houdt altijd minstens dit aandeel |
| Biascorrectie | uit | Trekt de gemeten systematische afwijking per bron af vóór het middelen |
| Meetvenster | 28 dagen | Over zoveel recente dagen wordt de nauwkeurigheid berekend |
| Lengte goedkoopste blok | 3 uur | Voor de sensor *cheapest block start* |

## Ensemble en weging

Het **ensemble** is per kwartier een gewogen gemiddelde van de bronnen die voor dat kwartier
een waarde hebben. De losse bronreeksen blijven daarnaast beschikbaar.

- Hebben beide bronnen een waarde, dan telt het ensemble er twee (`n_sources = 2`).
- Verder dan 48 uur heeft Energy Price Forecast EU (zonder key) geen waarde meer. Dan is het
  ensemble gewoon EpexPredictor (`n_sources = 1`).

**Automatische weging** werkt per *looptijd*: hoe ver vooruit de voorspelling was op het
moment van ophalen. Er zijn drie looptijd-buckets: 0–1 dag, 2–3 dagen en 4–7 dagen. Een bron
kan op korte termijn sterk zijn en op lange termijn zwak; het gewicht volgt dat per bucket.

- Gewicht ~ 1/MAE (of 1/MAE²), genormaliseerd tot samen 100%.
- Zolang een bron in een bucket minder dan het minimum aantal afgerekende kwartieren heeft,
  wegen alle bronnen gelijk.
- Elke bron houdt het minimumgewicht. Zo valt een bron na één slechte week niet volledig weg
  en kan hij zich terugverdienen.

**Biascorrectie** (optioneel): voorspelt een bron structureel te hoog of te laag, dan wordt die
gemeten afwijking eraf gehaald voordat er gemiddeld wordt.

## Nauwkeurigheid en verwachte fout

**Meten.** Bij elke ophaalactie bewaart DynTarNL een momentopname van de voorspelling. Zodra
de echte prijs voor dat tijdvak binnenkomt, wordt afgerekend:

- **altijd op kale prijzen**: opslag en energiebelasting zijn vast en voegen geen onzekerheid toe;
- **per uur**: je leverancier publiceert per uur, dus het gemiddelde van de vier voorspelde
  kwartieren wordt vergeleken met de gepubliceerde uurprijs (dat telt als vier afgerekende
  kwartieren);
- **per bron en per looptijd-bucket**, en voor het ensemble ook **per aantal bronnen**. Zo
  wordt gemeten, en niet aangenomen, of twee bronnen samen beter zijn dan één;
- **uitschieters gedempt**: de fout per kwartier telt maximaal 0,15 €/kWh mee.

Het meetvenster is voortschrijdend (standaard de laatste 28 dagen). Daardoor vallen
seizoenseffecten er na een maand vanzelf uit en betekent "MAE" gewoon "gemiddelde fout over
de afgelopen 28 dagen".

**Verwachte fout.** Elk voorspeld kwartier krijgt een `expected_error`:

```text
verwachte fout (kaal) = √( MAE² + (spreiding / 2)² )
verwachte fout (all-in) = verwachte fout (kaal) × (1 + btw%)
```

- **MAE** is de gemeten fout van het ensemble voor deze looptijd en dit aantal bronnen.
- **Spreiding** is hoe ver de bronnen het nú oneens zijn (hoogste − laagste).
- De vaste opslag verandert de marge niet; alleen de btw schaalt mee.

De eerste dag is er nog geen meting. De verwachte fout is dan alleen de halve spreiding (of
leeg bij één bron).

**Opslag.** De metingen staan in Home Assistant-storage (`.storage/dyntarnl.forecast_<entry>`)
en overleven een herstart. Ruwe momentopnames blijven alleen bewaard tot ze zijn afgerekend;
daarna blijven alleen dagtotalen binnen het meetvenster over. Na het verwijderen van de
integratie wordt dit bestand ook verwijderd.

## Entiteiten

Alleen aanwezig als de optie aan staat. Zet je de optie (of een bron) uit, dan ruimt DynTarNL
de bijbehorende entiteiten en het device zelf op.

**Op het device *DynTarNL E*:**

| Entiteit | State | Belangrijkste attributen |
| --- | --- | --- |
| `sensor.dyntarnl_e_cheapest_block_start` | Start van het goedkoopste aaneengesloten blok van N uur in de komende 48 uur, inclusief voorspellingen | `end`, `hours`, `avg_price_allin`, `avg_price_raw`, `source`, `providers`, `expected_error_allin` |
| `sensor.dyntarnl_e_all_in_forecast_avg` | Gemiddelde all-in prijs komende 24 uur | `source`, `coverage_hours`, `forecast_until`, `prices` (alles per kwartier) en de grafiekreeksen `forecast`, `forecast_market`, `error_band` (alleen voorspeld, per uur) |
| `sensor.dyntarnl_e_tomorrow_avg_forecast` | Gemiddelde all-in van morgen: gepubliceerd als dat er is, anders voorspeld | `date`, `source`, `coverage_hours`, `expected_error_allin` |

`source` is `published`, `forecast` of `mixed` (een deel gepubliceerd, een deel voorspeld).

De grafiekreeksen zijn groot en worden daarom **niet** in de recorder opgeslagen; je database
groeit er niet van.

**Op het device *DynTarNL Forecast* (diagnostisch), per bron:**

| Entiteit | Betekenis |
| --- | --- |
| `sensor.dyntarnl_forecast_<bron>_mae` | Gemiddelde absolute fout (kaal, €/kWh) over het meetvenster, looptijd 0–1 dag. Attribuut `buckets` heeft alle looptijden |
| `sensor.dyntarnl_forecast_<bron>_bias` | Gemiddelde afwijking: positief = voorspelt te hoog |
| `sensor.dyntarnl_forecast_<bron>_settled` | Aantal afgerekende kwartieren in het venster (`settled_total` = sinds het begin) |
| `sensor.dyntarnl_forecast_<bron>_weight` | Huidig gewicht in het ensemble (%), per looptijd in `buckets` |
| `sensor.dyntarnl_forecast_<bron>_last_fetch` | Laatste geslaagde ophaalactie. Attributen: `status` (`ok`/`backoff`/`pending`), `last_error`, `next_attempt`, `points`, `forecast_until` |

`<bron>` is `epexpredictor` of `energypriceforecast`. Voor het ensemble zijn er
`sensor.dyntarnl_forecast_ensemble_mae` (met de fout per looptijd én per aantal bronnen) en
`sensor.dyntarnl_forecast_ensemble_settled`.

## Service `dyntarnl.get_prices`

De belangrijkste manier om de volledige reeks op te halen, bijvoorbeeld in een script of
automatisering. De service bestaat altijd. Met de optie uit krijg je alleen gepubliceerde
prijzen terug (`forecast_enabled: false`).

```yaml
action: dyntarnl.get_prices
data:
  energy: electricity        # of gas (alleen gepubliceerd, per gasdag)
  include_forecast: true
  provider: ensemble         # of epexpredictor / energypriceforecast / all
  horizon: 72                # uren vanaf nu (optioneel)
response_variable: prijzen
```

Met `provider: all` krijg je naast de samengevoegde reeks ook elke bron apart onder
`providers`.

Elk record in `records`:

| Veld | Betekenis |
| --- | --- |
| `start`, `end` | Kwartier, in lokale tijd |
| `price_raw` | Kale EPEX-prijs, €/kWh excl. btw |
| `price_allin` | All-in prijs volgens jouw leveranciersformule |
| `source` | `published` of `forecast` |
| `expanded` | `true` = uit een uurwaarde over vier kwartieren verdeeld |
| `supplier` | Alleen bij `published` |
| `providers`, `fetched_at`, `n_sources` | Alleen bij `forecast`: welke bronnen, wanneer opgehaald, hoeveel |
| `spread_min`, `spread_max` | Alleen bij `forecast`: laagste en hoogste bronwaarde |
| `expected_error_raw`, `expected_error_allin` | Alleen bij `forecast`: verwachte fout |

Voorbeeld: de starttijden van de 8 goedkoopste voorspelde kwartieren, in een template:

```jinja
{% set recs = prijzen.records | selectattr('source', 'eq', 'forecast') | list %}
{{ (recs | sort(attribute='price_allin'))[:8] | map(attribute='start') | list }}
```

## De grafiek

De voorspelling past naast de bestaande [voorbeeldkaart](../README.md#voorbeeld-kaart-all-in-én-beurs-in-één-grafiek)
(ApexCharts). Het resultaat:

- **Massieve kolommen** = gepubliceerde all-in prijzen, zoals je gewend bent.
- **Gestippelde lijn met lichte vulling** = voorspelde all-in prijs, met dezelfde kleuren
  (groen goedkoop, geel normaal, rood duur).
- **Massieve blauwe lijn** = gepubliceerde beursprijs.
- **Gestippelde blauwe lijn** = voorspelde beursprijs.
- Optioneel: **dunne grijze stippellijnen** boven en onder de voorspelling = de verwachte marge.

De **grote bedragen in de header** (links all-in, rechts beurs) zijn de prijzen van het
**huidige uur**, mits de gepubliceerde series `in_header: before_now` hebben (zoals in het
README-voorbeeld). Zonder die regel toont apexcharts-card het laatste punt van de reeks, dus
bijvoorbeeld de prijs van morgen 23:00. De voorspelde series staan bewust niet in de header
(`in_header: false`). De **legenda** toont standaard het laatste punt van elke serie; de
`legend.formatter` uit het README-voorbeeld maakt daar de waarde van nu van. Voorspelde series
hebben nog geen punt vóór nu en tonen daarom alleen hun naam.

Kort gezegd: **massief = zeker, gestippeld = voorspeld**. Het verschil tussen de oranje/gele
vlakken en de blauwe lijn is (net als bij de kolommen) je opslag + energiebelasting.

Rond 13:00 verschijnen de prijzen van morgen. Het gestippelde stuk voor morgen wordt dan
vervangen door kolommen, en de stippellijn begint pas bij overmorgen.

**Waarom geen gekleurde kolommen voor de voorspelling?** ApexCharts zet meerdere
kolomseries naast elkaar. Alle kolommen zouden dan half zo smal en verschoven worden, ook die
van de gepubliceerde uren. Een stepline met vulling heeft dat probleem niet en maakt het
verschil tussen zeker en voorspeld meteen zichtbaar.

### Instellen

Neem de voorbeeldkaart uit de README (met `in_header: before_now` op de twee gepubliceerde
series) en pas twee dingen aan:

1. `graph_span: 120h` (gisteren + vandaag + drie dagen vooruit).
2. Voeg onder `series:` deze twee series toe:

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

**Kolombreedte.** ApexCharts maakt kolommen zo breed als het kleinste tijdsverschil tussen
twee punten in de hele grafiek. Daarom staan `forecast`, `forecast_market` en `error_band` per
**uur**, net als de gepubliceerde kolommen (publiceert je leverancier per kwartier, dan per
kwartier). De kwartierdetails blijven beschikbaar via `prices` en `dyntarnl.get_prices`. Hoe
langer `graph_span`, hoe smaller de kolommen: 120 uur geeft ruim 40% smallere kolommen dan
72 uur. Te smal? Kies `graph_span: 96h`, of maak ze breder met:

```yaml
apex_config:
  plotOptions:
    bar:
      columnWidth: 90%
```

**Marge erbij?** `error_band` geeft per uur `[epoch-ms, laag, hoog]` (all-in ± verwachte
fout). Twee dunne lijnen maken er een band van:

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

De eerste dag (nog geen metingen) is de band smal: dan is hij alleen gebaseerd op de
onenigheid tussen de bronnen.

**Aanpassen.** Te weinig contrast, bijvoorbeeld in dark mode? Verhoog `opacity`, kies een
andere `stroke_dash`, of geef de voorspelling één vaste kleur (`color:`) in plaats van
`color_threshold`.

**Data per bron in plaats van het ensemble?** Gebruik een
[template-sensor](https://www.home-assistant.io/integrations/template/) of een script met
`dyntarnl.get_prices` en `provider: epexpredictor` (of `energypriceforecast`).

### Als tabel

Liever cijfers dan een grafiek? Een standaard **Markdown-kaart** zet de gepubliceerde en
voorspelde uren in twee tabellen:

- **Per dag:** gemiddelde, laagste en hoogste all-in prijs, met de bron (`gepubliceerd`,
  `voorspeld` of `gemengd`). Vandaag telt de hele dag mee.
- **Per uur:** de komende 48 uur, met all-in, beurs en de bron. Bij voorspelde uren staat
  ook de marge (± verwachte fout), zodra die bekend is.

🟢 is onder € 0,25, 🟡 tot € 0,40 en 🔴 daarboven: dezelfde drempels als de grafiek. Pas
`uren` bovenaan aan voor een kortere of langere uurtabel.

```yaml
type: markdown
title: Stroomprijzen
content: |
  {%- set uren = 48 -%}
  {%- set nu = now().timestamp() -%}
  {%- set a_now = 'sensor.dyntarnl_e_all_in_now' -%}
  {%- set m_now = 'sensor.dyntarnl_e_market_now' -%}
  {%- set fc_ent = 'sensor.dyntarnl_e_all_in_forecast_avg' -%}
  {%- set allin = (state_attr(a_now, 'today') or []) + (state_attr(a_now, 'tomorrow') or []) -%}
  {%- set beurs = (state_attr(m_now, 'today') or []) + (state_attr(m_now, 'tomorrow') or []) -%}
  {%- set fc = state_attr(fc_ent, 'forecast') or [] -%}
  {%- set fm = state_attr(fc_ent, 'forecast_market') or [] -%}
  {%- set eb = state_attr(fc_ent, 'error_band') or [] -%}
  {%- set ns = namespace(rows=[], marge={}) -%}
  {%- for e in eb -%}
    {%- set ns.marge = dict(ns.marge, **{e[0] | string: (e[2] - e[1]) / 2}) -%}
  {%- endfor -%}
  {%- for p in allin -%}
    {%- set t = as_timestamp(p.start) -%}
    {%- set b = beurs[loop.index0].price if beurs | length > loop.index0 else none -%}
    {%- set ns.rows = ns.rows + [{'t': t, 'd': t | timestamp_custom('%Y-%m-%d'), 'a': p.price, 'b': b, 'f': false, 'm': none}] -%}
  {%- endfor -%}
  {%- for p in fc -%}
    {%- set t = p[0] / 1000 -%}
    {%- set b = fm[loop.index0][1] if fm | length > loop.index0 else none -%}
    {%- set ns.rows = ns.rows + [{'t': t, 'd': t | timestamp_custom('%Y-%m-%d'), 'a': p[1], 'b': b, 'f': true, 'm': ns.marge.get(p[0] | string)}] -%}
  {%- endfor -%}
  {%- macro eur(x) -%}{{ '%.3f' | format(x) | replace('.', ',') }}{%- endmacro -%}
  {%- macro dag(ts) -%}{{ ['zo', 'ma', 'di', 'wo', 'do', 'vr', 'za'][ts | timestamp_custom('%w') | int] }}{%- endmacro -%}
  {%- macro kleur(x) -%}{{ '🟢' if x < 0.25 else ('🟡' if x < 0.40 else '🔴') }}{%- endmacro -%}
  ### Per dag

  | Dag | Gem. | Min | Max | Bron |
  |:--|--:|--:|--:|:--|
  {% for d, rs in ns.rows | groupby('d') -%}
  {%- set prijzen = rs | map(attribute='a') | list -%}
  {%- set nf = rs | selectattr('f') | list | count -%}
  {%- set ts = as_timestamp(d ~ 'T12:00:00') -%}
  | {{ dag(ts) }} {{ ts | timestamp_custom('%d-%m') }} | {{ kleur(prijzen | average) }} {{ eur(prijzen | average) }} | {{ eur(prijzen | min) }} | {{ eur(prijzen | max) }} | {{ 'gepubliceerd' if nf == 0 else ('voorspeld' if nf == rs | count else 'gemengd') }} |
  {% endfor %}
  ### Per uur (komende {{ uren }} uur)

  | Tijd | All-in | Beurs | Bron |
  |:--|--:|--:|:--|
  {% for r in ns.rows if r.t + 3600 > nu and r.t < nu + uren * 3600 -%}
  | {{ dag(r.t) }} {{ r.t | timestamp_custom('%H:%M') }} | {{ kleur(r.a) }} {{ eur(r.a) }} | {{ eur(r.b) if r.b is not none else '–' }} | {{ ('≈ voorspeld' ~ (' ±' ~ eur(r.m) if r.m is not none else '')) if r.f else 'gepubliceerd' }} |
  {% endfor %}
```

## Ophalen, fair use en storingen

- Elke bron wordt standaard **eens per 6 uur** bevraagd. Het zijn gratis diensten; vaker heeft
  weinig zin, want de modellen werken met weersverwachtingen die ook maar een paar keer per dag
  verversen.
- Het exacte moment verschilt per installatie, zodat niet iedereen tegelijk aanklopt.
- Na een herstart wordt niet opnieuw opgehaald zolang de bewaarde voorspelling nog vers is.
- Elke bron heeft een eigen time-out (20 s). Na een fout wacht DynTarNL steeds langer voor een
  nieuwe poging: 30 minuten, 1 uur, 2 uur, … tot maximaal 12 uur.
- **Een storing bij een voorspelbron heeft nooit invloed op je gewone prijzen.** De
  voorspellingen draaien in een eigen, losgekoppeld onderdeel. Valt één bron uit, dan rekent
  het ensemble verder met de andere.

## Beperkingen

- **Gecorreleerde fouten.** Alle bronnen baseren zich op dezelfde weersverwachtingen (wind,
  zon, temperatuur). Zit het weerbericht ernaast, dan zitten ze er vaak allemaal naast. Het
  ensemble middelt verschillen tussen modellen uit, niet de onzekerheid van het weer. De
  gemeten fout per aantal bronnen (`ensemble_mae`, attribuut `by_sources`) laat zien hoeveel
  combineren in de praktijk oplevert.
- **Energiebelasting rond de jaarwisseling.** Een voorspelling over 1 januari heen rekent nog
  met de energiebelasting van het oude jaar.
- **Plannen, niet afrekenen.** Een voorspelling blijft een schatting. Gebruik hem om vooruit te
  plannen; de gepubliceerde prijs is wat je betaalt.
- **Ongedocumenteerde API.** Energy Price Forecast EU publiceert geen API-specificatie.
  DynTarNL gebruikt het endpoint van hun eigen Home Assistant-integratie en controleert het
  antwoordformaat. Verandert dat, dan gaat alleen die bron in storing.

## Problemen oplossen

**Geen voorspel-entiteiten?**
Controleer bij *Ontwikkelhulpmiddelen → Statussen* of er entiteiten met `dyntarnl_forecast`
zijn. Zo niet: staat 2.0 erop (na de update herstarten) en heb je *Configureren* helemaal
doorlopen tot en met de laatste stap?

**Entiteiten wel, maar geen voorspelling?**
Kijk bij `sensor.dyntarnl_forecast_<bron>_last_fetch`. Staat `status` op `backoff`, dan staat
de reden in `last_error`. Kijk ook in *Instellingen → Systeem → Logboeken* naar regels met
`dyntarnl`.

**Niets in de grafiek?**
Controleer of de attributen `forecast` en `forecast_market` van
`sensor.dyntarnl_e_all_in_forecast_avg` gevuld zijn, en of `graph_span` ver genoeg vooruit
reikt. De voorspelling begint pas na het laatste gepubliceerde uur.

**MAE en gewicht blijven leeg of 50%?**
Normaal de eerste dag(en): er moet eerst genoeg afgerekend zijn (standaard 96 kwartieren per
bron per looptijd). Voorspellingen voor 4–7 dagen vooruit worden pas na 4–7 dagen
afgerekend.

**API-key leegmaken?**
Maak het veld leeg en klik op Verzenden; leeg betekent "geen key".
