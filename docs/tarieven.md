# Tariefperiodes, inkoop- en verkooptabel (vanaf 1.3)

Per 1 januari 2027 stopt het salderen. Afname en teruglevering worden dan los afgerekend: over
teruglevering betaal je geen energiebelasting en geen btw meer, en je leverancier rekent
terugleverkosten. DynTarNL rekent daarom met **tariefperiodes** en publiceert een
**inkooptabel** en een **verkooptabel** in hetzelfde formaat.

## Tariefperiodes

Een periode geldt vanaf 00:00 (Europe/Amsterdam) op haar ingangsdatum tot de volgende
periode begint. **Elk tijdslot krijgt de regels van zijn eigen periode**, ook in voorspellingen
die over de jaargrens heen lopen: 31-12 23:45 rekent met 2026, 1-1 00:00 met 2027. Nooit op
basis van "nu".

Instellen: *Instellingen → Apparaten en diensten → DynTarNL → Configureren → Tariefperiodes*.
Je kunt een periode toevoegen, wijzigen of verwijderen. Opslaan herlaadt DynTarNL meteen
(geen herstart nodig); de nieuwe waarden gelden direct voor de huidige prijzen en de
voorspellingen.

Per periode:

| Veld | Betekenis |
| --- | --- |
| Ingangsdatum | Geldt vanaf 00:00 op deze datum |
| Contracttype | Dynamisch, vast of variabel (variabel = vast, met een nieuwe periode per tariefwijziging) |
| Saldering | Aan: verkoopprijs = inkoopprijs van hetzelfde slot. Uit: los afrekenen |
| Btw | Percentage (21) |
| Energiebelasting stroom / gas | Excl. btw |
| Voorlopig + notitie | Voor waarden die nog niet definitief zijn |
| **Inkoop** (dynamisch) | Opslag stroom/gas excl. btw |
| **Inkoop** (vast/variabel) | Enkel- of dubbeltarief; tarief normaal/dal en gas, ingevoerd als *all-in* (incl. energiebelasting en btw) of *kaal* (excl. btw); daltijden (standaard werkdagen 23:00–07:00 en het hele weekend), feestdagen optioneel; vaste leveringskosten per maand (alleen informatief) |
| **Verkoop** (dynamisch) | Opslag (+) of korting (−) op de beurs; terugleverkosten per kWh; vaste terugleverkosten per jaar (alleen informatief) |
| **Verkoop** (vast/variabel) | Terugleververgoeding normaal/dal (kaal); terugleverkosten per kWh; vaste terugleverkosten per jaar (alleen informatief) |

Controles in de UI: geen dubbele ingangsdatum, de vroegste periode begint vandaag of eerder
(geen gat), en verplichte velden moeten ingevuld zijn, tenzij de periode als *voorlopig*
staat. **Een leeg veld wordt nooit 0**: slots in die periode krijgen dan geen prijs
(onbekend), en je krijgt een melding.

Vaste bedragen (netbeheer, vermindering energiebelasting, vaste leverings- of
terugleverkosten per jaar of maand, staffels) horen niet in een kWh-prijs. DynTarNL rekent ze
niet mee; de vaste kosten die je invult, zie je terug in `sensor.dyntarnl_tariff_period`.

### Bij het bijwerken naar 1.3

DynTarNL maakt automatisch twee periodes aan:

1. **Vanaf 1 januari van dit jaar**: je huidige instellingen, saldering aan. Bij een
   leverancier met een eigen API (Essent, Energiedirect, Frank, easyEnergy) komen opslag en
   belasting voor gepubliceerde uren gewoon van de leverancier.
2. **Vanaf 1-1-2027, voorlopig**: saldering uit, energiebelasting uit het (gecorrigeerde)
   Belastingplan 2027 (stroom € 0,08802 excl. = € 0,1065 incl. btw; gas € 0,61628 excl. =
   € 0,7457 incl.). Opslag teruglevering en terugleverkosten blijven **leeg**: die kent
   DynTarNL niet.

Tot en met 2026 verandert er niets aan je prijzen.

### Meldingen (Instellingen → Systeem → Reparaties)

- Een periode die al geldt of binnen 14 dagen ingaat, staat nog als voorlopig of mist waarden.
- Een vaste/variabele terugleververgoeding onder het wettelijk minimum (50% van het kale
  leveringstarief) in 2027–2029.
- Een oude teruglever-entiteit (`export_*`) wordt nog in een automatisering of script
  gebruikt; die worden in 2.0 vervangen door de verkoop-entiteiten.

### Na het definitieve Belastingplan (december 2026) en de 2027-voorwaarden van je leverancier

- [ ] Energiebelasting stroom en gas 2027 controleren.
- [ ] Opslag inkoop 2027 (bij Custom verplicht; bij een API-leverancier alleen voor voorspelde uren).
- [ ] Opslag/korting teruglevering en terugleverkosten per kWh invullen.
- [ ] "Voorlopig" uitzetten.

## De tabellen

| Entiteit | Inhoud |
| --- | --- |
| `sensor.dyntarnl_e_buy_table` | Inkoop stroom per slot; state = prijs nu |
| `sensor.dyntarnl_e_sell_table` | Verkoop (teruglevering) stroom per slot; state = prijs nu |
| `sensor.dyntarnl_g_buy_table` | Inkoop gas per slot |

De tabel loopt van gisteren 00:00 tot het eind van wat bekend is: gepubliceerd, en met
voorspellingen aan tot de voorspelhorizon. Bij een vast/variabel contract staat de prijs van
morgen er ook al in vóór de leverancier publiceert. Gepubliceerde prijzen vervangen een
voorspelling in **beide** tabellen tegelijk. Inkoop- en verkooptabel hebben altijd exact
dezelfde slots (ook op de 23- en 25-uursdag van de klokwissel), dus een consumer hoeft alleen
de entiteit te wisselen.

Attributen (compact kolomformaat):

| Attribuut | Betekenis |
| --- | --- |
| `unit` | `€/kWh` of `€/m³` |
| `resolution_min` | 15 met voorspellingen aan (of als de bron kwartierprijzen levert), anders 60 |
| `start` / `start_local` | Begin van de eerste rij: epoch-seconden en lokale ISO-tijd |
| `fields` | Kolomnamen (zie hieronder) |
| `rows` | Eén lijst per slot. Rij *i* begint op `start + i × resolution_min × 60` (echte seconden, dus DST-veilig) |
| `sources` | Uitleg per bron-code in `src` |

Kolommen, voor inkoop en verkoop hetzelfde:

| Kolom | Betekenis |
| --- | --- |
| `price` | Eindprijs (inkoop: wat je betaalt; verkoop: wat je krijgt) |
| `market_ex` | Kale beursprijs (EPEX) excl. btw; bij een vast contract alleen informatief |
| `energy_ex` | Leveringsdeel excl. btw: beurs + opslag (dynamisch) of het contracttarief (vast) |
| `fee_ex` | Opslag (inkoop) of opslag/korting teruglevering (verkoop) |
| `costs_ex` | Terugleverkosten (alleen verkoop, anders 0) |
| `tax_ex` | Energiebelasting (verkoop zonder saldering: 0) |
| `vat` | Btw-bedrag (verkoop zonder saldering: 0) |
| `spread` | Inkoop − verkoop van dit slot (0 zolang saldering geldt) |
| `src` | `p:<leverancier>` gepubliceerd, `f:<bronnen>` voorspeld, `c` contracttarief |

Altijd geldt: `price = energy_ex − costs_ex + tax_ex + vat`. Een onbekende waarde is `null`.

Voorbeeld (Essent, 18-11-2026, voorspellingen aan, saldering aan):

```json
{
  "unit": "€/kWh",
  "resolution_min": 15,
  "start": 1794870000,
  "start_local": "2026-11-17T00:00:00+01:00",
  "fields": ["price", "market_ex", "energy_ex", "fee_ex", "costs_ex", "tax_ex", "vat", "spread", "src"],
  "rows": [
    [0.36772, 0.1914, 0.21229, 0.02089, 0.0, 0.09161, 0.06382, 0.0, "p:essent"],
    [0.47413, 0.27934, 0.30023, 0.02089, 0.0, 0.09161, 0.08229, 0.0, "f:epexpredictor"]
  ],
  "sources": {
    "p:essent": "gepubliceerd door Essent",
    "f:epexpredictor+energypriceforecast": "voorspeld (epexpredictor, energypriceforecast)",
    "f:epexpredictor": "voorspeld (epexpredictor)"
  }
}
```

Hetzelfde slot in 2027 (dynamisch, saldering uit, terugleverkosten € 0,01, geen
teruglever-opslag), inkoop- en verkooptabel:

```json
"buy":  [0.25170, 0.10, 0.12, 0.02, 0.0, 0.08802, 0.04368, 0.1617, "p:essent"]
"sell": [0.09,    0.10, 0.10, 0.0,  0.01, 0.0,    0.0,     0.1617, "p:essent"]
```

Uitlezen in een template:

```jinja
{%- set a = states.sensor.dyntarnl_e_sell_table.attributes -%}
{%- for row in a.rows -%}
  {{ (a.start + loop.index0 * a.resolution_min * 60) | timestamp_local }}  {{ row[0] }}  {{ row[8] }}
{% endfor %}
```

(`row[0]` = `price`, `row[8]` = `src`; de volgorde staat in `fields`.)

Dezelfde tabellen krijg je ook via de service:

```yaml
action: dyntarnl.get_prices
data:
  energy: electricity
  format: table        # geeft {"buy": {...}, "sell": {...}}
response_variable: prijzen
```

## Afgeleide sensoren (verkoop)

| Entiteit | Waarde |
| --- | --- |
| `sensor.dyntarnl_e_sell_now` / `_sell_prev` / `_sell_next` | Verkoopprijs nu, vorig en volgend slot |
| `sensor.dyntarnl_e_sell_today_min` / `_avg` / `_max`, `_sell_tomorrow_min` / `_max` | Zoals bij inkoop |
| `sensor.dyntarnl_e_spread_now` | Inkoop − verkoop nu |
| `binary_sensor.dyntarnl_e_sell_negative_now` | Aan als terugleveren nu geld kost (verkoop < 0); attributen `next_start`/`next_end` = de eerstvolgende periode waarin dat geldt (ook uit contract of voorspelling) |
| `sensor.dyntarnl_e_sell_best_block_start` | Begin van het blok van N uur met de hoogste verkoopprijs (alleen met voorspellingen aan) |
| `sensor.dyntarnl_tariff_period` | Diagnostiek: huidige periode (state = ingangsdatum), `current`, `next`, `next_start`, `provisional_active`, `provisional_planned`, `missing_current`, `missing_next` |

## Waar komt de prijs vandaan (per leverancier)

Geen van de ondersteunde leveranciers publiceert een eigen terugleverprijs via de API's die
DynTarNL gebruikt. De verkoopprijs wordt dus altijd door DynTarNL berekend:

| Leverancier | Inkoop (dynamisch) | Verkoop |
| --- | --- | --- |
| Essent, Energiedirect | API per slot (beurs, opslag, belasting, btw) | Saldering: = inkoop; anders beurs ± opslag − kosten uit de periode |
| Frank Energie | API per slot | idem |
| easyEnergy | API per slot | idem |
| Custom | Kale beurs van de gekozen EPEX-bron + opslag, belasting en btw uit de periode | idem |
| Elk, vast/variabel contract | Contracttarief uit de periode (beurs alleen informatief) | Saldering: = inkoop; anders vergoeding − kosten |

## Oude teruglever-entiteiten

`sensor.dyntarnl_e_export_price_now`, `_export_cost_now/next`, `_export_loss_today` en
`binary_sensor.dyntarnl_e_export_loss_now/next` blijven in 1.x exact hetzelfde werken: de
dynamische *vergoeding* (beurs − opslag, incl. btw), zonder saldering, terugleverkosten of
contracttype. Gebruik voor nieuwe automatiseringen de verkoop-entiteiten; in 2.0 vervallen de
oude.

## Nettarief per tijdslot (vanaf 2029)

Nog niet gebouwd. Omdat elk slot zijn eigen periode opzoekt, is een tijdsafhankelijk
nettarief later één extra veld (met dagschema) per periode.
