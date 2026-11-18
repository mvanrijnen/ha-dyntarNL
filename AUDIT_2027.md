# Audit: DynTarNL en het einde van salderen (1 januari 2027)

Status: **uitgevoerd in 1.3.0** (tariefperiodes, inkoop-/verkooptabel, vast/variabel, repairs;
zie [docs/tarieven.md](docs/tarieven.md)). Hieronder de oorspronkelijke audit op `main` na v1.2.1.

## Samenvatting

DynTarNL is **niet klaar** voor 2027. Hieronder de punten, op impact gesorteerd.

| # | Impact | Wat | Waar |
|---|---|---|---|
| 1 | Hoog | De terugleverprijs is "beurs incl. btw − opslag incl. btw". Vanaf 2027 hoort dat "kale beurs ± teruglever-opslag − terugleverkosten" te zijn, zonder btw. Er is geen plek om terugleverkosten in te vullen. | `prices.py:67-77`, alle teruglever-sensoren |
| 2 | Hoog | Er bestaat geen "saldering aan/uit". In 2026 is de echte waarde van een teruggeleverde kWh (binnen je jaarverbruik) de all-in afnameprijs. DynTarNL toont alleen de vergoeding. | ontwerp: geen model voor saldering |
| 3 | Hoog | Bij Custom staan opslag, energiebelasting en btw er één keer in, zonder ingangsdatum. Per 1-1-2027 rekent Custom door met de belasting van 2026. | `sources.py:231-262`, `config_flow.py:97-98` |
| 4 | Middel | Voorspellingen gebruiken het tarief van het **laatst gepubliceerde uur** voor alle voorspelde uren. Uren na de jaarwisseling krijgen dan de belasting van 2026. | `forecast/engine.py:83-88, 132` |
| 5 | Middel | `export loss now/next`, `export cost now/next` en `export loss today` beslissen op de verkeerde terugleverprijs (gevolg van 1). | `binary_sensor.py:53-60`, `sensor.py:216-237` |
| 6 | Middel | `dyntarnl.get_prices` geeft geen terugleverprijs per kwartier, alleen `price_raw` en `price_allin`. | `forecast/model.py:95-115` |
| 7 | Laag | De standaardwaarden voor energiebelasting 2026 wijken iets af van jouw tabel. | `const.py:67-68` |
| 8 | Laag | EnergyZero als EPEX-bron rekent met de vaste standaardbelasting. Dat heeft geen effect, want alleen de kale beursprijs wordt gebruikt. | `sources.py:346-394` |

Wat **wel goed** gaat:
- **Leveranciers met een API** (Essent, Energiedirect, Frank, easyEnergy) leveren energiebelasting en opslag **per tijdslot**. Die schakelen dus vanzelf over zodra de leverancier vanaf 1-1-2027 de nieuwe belasting meestuurt.
- **Prijzen zoeken gaat per tijdstip:** `slot_at` vergelijkt echte tijdstippen, nooit "nu" of een kloktijd.
- **Vaste bedragen** (netbeheer, vermindering energiebelasting) zitten nergens in de kWh-prijzen, en zo hoort het ook.

**Niet in DynTarNL, wel geraakt:** DynTarNL heeft zelf geen accu-, EV- of
kostenberekeningslogica (EV is bewust buiten scope). Die zit in de gebruikers ervan:
- HomeBatteryAssist (leest `export_price_now.prices` en `forecast_export`, eerder `beurs − purchase_fee`);
- OmniBattery (zou `today`/`tomorrow` lezen);
- de ZeroExport-voorbeeldautomatisering in de README;
- het HA Energy-dashboard.

Die worden vanzelf goed zodra DynTarNL de juiste terugleverprijs publiceert, mits ze de
DynTarNL-reeks gebruiken en niet zelf `beurs − opslag` uitrekenen (zie 1.6).

Rekenvoorbeeld in dit document, tenzij anders vermeld: kale beurs `market_ex` = € 0,10, opslag
`fee_ex` = € 0,02 (excl. btw), btw 21%. De terugleverkosten noem ik `k`; die zijn nog onbekend
en dus niet ingevuld.

---

## 1. Waar de all-in prijs wordt opgebouwd

### 1.1 De formule: `prices.py:24-45` `apply_formula`

`all-in = (market_ex + fee_ex + tax_ex) × (1 + vat/100)`. Dit is de enige implementatie. Hij
wordt gebruikt voor Custom en voor voorspellingen. `Tariff` (`prices.py:11-21`) heeft drie
velden: `fee_ex`, `tax_ex` en `vat`. Er is geen ingangsdatum, geen terugleverdeel en geen
saldering.

- **Vanaf 2027:** de formule zelf klopt nog voor afname. `(0,10 + 0,02 + 0,08802) × 1,21 =
  0,251704`, afgerond 0,25170. Jouw voorbeeld komt op 0,2517, omdat daarin 0,1065 incl. btw
  bij elkaar wordt opgeteld. 0,08802 × 1,21 = 0,1065042, dus het verschil is 0,0000042: een
  afrondingsverschil, geen fout.
- **Voorstel:** formule voor afname laten zoals hij is. `Tariff` per periode uitbreiden (zie
  fase 2). Belasting excl. btw blijft de bron en "incl. btw" wordt daaruit berekend, net als
  nu.

### 1.2 Leveranciers met een API (`sources.py`)

| Bron | Beurs | Opslag | Energiebelasting | Btw | Aparte terugleverprijs? |
|---|---|---|---|---|---|
| Essent / Energiedirect (`fetch_eon_app`, `:92-134`) | groep MARKET (`amount`/`amountEx`) | groep FEE | groep TAX | `vatPercentage` per blok (standaard 21) | **Nee** |
| easyEnergy (`fetch_easyenergy`, `:154-201`) | `price` / `priceIncVat` | `purchasePrice` (incl.) | `energyTax` (incl.) | vast `_VAT_FACTOR = 1.21` (`:38`) | **Nee** (de prijs-API heeft geen veld voor teruglevering) |
| Frank (`fetch_frank`, `:287-340`) | `marketPrice` + `marketPriceTax` | `sourcingMarkupPrice` (incl.) | `energyTaxPrice` (incl.) | vast 1,21 | **Nee** |

- **Nu:** belasting en opslag komen per tijdslot uit de API, dus de datumafhankelijkheid zit
  al goed.
- **Vanaf 2027:** de afnameprijs gaat vanzelf goed zodra de leverancier 2027-tarieven
  publiceert. **Risico:** de btw staat bij easyEnergy en Frank vast op 1,21. Dat is correct
  zolang de btw 21% blijft, wat jij als uitgangspunt geeft.
- **Als een veld ontbreekt:** `_num` (`:50-64`) geeft een `SourceError` bij een ontbrekende,
  NaN- of onmogelijke waarde (> € 10/kWh). Dat wordt een `UpdateFailed` en de sensoren
  blijven op de laatste prijzen staan. Dat is geen stille 0. Hernoemt een leverancier een
  veld, dan faalt de fetch zichtbaar, en `tools/live_check.py` ziet dat ook.
- **Terugleverkosten** levert geen enkele bron. Die moeten dus uit configuratie komen.

### 1.3 Custom (`sources.py:231-262` `build_custom`)

- **Nu:** opslag stroom/gas, energiebelasting stroom/gas en btw komen uit `entry.data`. Je
  vult ze één keer in, via de config flow of reconfigure. `Tariff` is voor **alle** slots
  hetzelfde: gisteren, vandaag en morgen.
- **Vanaf 2027:** zonder handmatige wijziging precies om 00:00 blijft de belasting van 2026
  staan.
  - Voorbeeld: `(0,10 + 0,02 + 0,09161) × 1,21 = 0,25605` in plaats van 0,25170. Dat is
    **€ 0,0043/kWh te hoog**, ongeveer 1,7%.
  - Wijzig je het op 31-12 om 14:00, dan krijgen alle uren van 31-12 (gisteren en vandaag)
    de 2027-belasting. Eén waarde kan niet per tijdslot verschillen.
- **Voorstel:** tarieventabel met ingangsdatum en een tarief per tijdslot (fase 2).

### 1.4 Standaardwaarden (`const.py:65-68`)

`DEFAULT_TAX_ELEC = 0.09161` en `DEFAULT_TAX_GAS = 0.60066` worden alleen gebruikt om het
Custom-formulier in te vullen (`config_flow.py:97-98`) en als belasting bij EnergyZero (zie 1.5).

**Afwijking van jouw tabel:**
- Stroom: 0,1108 / 1,21 = **0,09157**. In de code staat 0,09161 (× 1,21 = 0,11085).
- Gas: 0,7267 / 1,21 = **0,60058**. In de code staat 0,60066 (× 1,21 = 0,72680).

De verschillen zijn klein (< € 0,0001/kWh), maar ik verander ze niet zonder jouw
bevestiging. Laat ze naast de officiële tabel van de Belastingdienst leggen.

### 1.5 EnergyZero (`sources.py:346-394`)

Hier wordt de belasting `DEFAULT_TAX_ELEC` vast ingevuld. EnergyZero staat niet als leverancier
in de lijst. Het is alleen een EPEX-bron voor Custom, en `build_custom` gebruikt daarvan alleen
`market_ex`. Er is dus **geen effect**. Na de wijziging wel de tabel gebruiken of het veld
weglaten, zodat er nergens een verouderde belasting rondzwerft.

### 1.6 Attributen voor andere integraties (`sensor.py:73-101` `_attributes`)

`market_price`, `purchase_fee` en `energy_tax` van het huidige uur. HomeBatteryAssist
rekende eerder zelf `beurs − purchase_fee`.

- **Vanaf 2027** is die eigen berekening fout: zie 2.1 voor de getallen.
- **Voorstel:** in de HBA-documentatie noemen dat HBA `export_price_now.prices` moet gebruiken
  (dat gebeurt al sinds 1.2.0). DynTarNL kan teruglever-componenten als extra attributen
  toevoegen (`export_fee`, `export_costs`).

---

## 2. Waar de terugleverprijs wordt bepaald

### 2.1 De formule: `prices.py:67-77` `feed_in_value` / `feed_in_value_ex`

```python
feed_in_value(slot)    = slot.market - slot.fee          # incl. btw
feed_in_value_ex(slot) = slot.market_ex - slot.fee_ex    # (ongebruikt, alleen geïmporteerd)
```

**Nu (2026):** terugleververgoeding = beurs incl. btw − afname-opslag incl. btw.
`0,121 − 0,0242 = 0,0968`. Er zijn twee aannames:
- de **afname-opslag** wordt ook van teruglevering afgetrokken;
- er gaat btw over teruglevering.

**Vanaf 2027** (volgens jouw uitgangspunt): `market_ex ± teruglever-opslag − k`, zonder btw en
zonder energiebelasting.

| Kale beurs | Nu (`feed_in_value`) | 2027 met `k` = 0,02 | 2027 met `k` = 0 | Fout nu t.o.v. 2027 (`k` = 0,02) |
|---|---|---|---|---|
| 0,10 | 0,0968 | 0,0800 | 0,1000 | +0,0168 (te hoog) |
| 0,02 | 0,0000 | 0,0000 | 0,0200 | 0 |
| −0,05 | −0,0847 | −0,0700 | −0,0500 | −0,0147 (te laag) |

De fout verandert dus van teken met de prijs, en hangt af van `k`, dat nog onbekend is.

### 2.2 Saldering ontbreekt als begrip

- **Nu:** `export_price_now` is "wat de leverancier per kWh vergoedt". Onder saldering
  (t/m 2026) is de **economische** waarde van een teruggeleverde kWh, tot je jaarverbruik, de
  all-in afnameprijs: `(0,10 + 0,02 + 0,09161) × 1,21 = 0,2561`, niet 0,0968.
- **Gevolg:** een accu-, EV- of ZeroExport-beslissing die in 2026 op `export_price_now` leunt,
  onderschat terugleveren. Die rekent dan alsof saldering al weg is. Vanaf 2027 klopt die
  aanname wél, maar de formule niet (2.1).
- **Voorstel:** een **nieuwe** sensor en reeks voor de effectieve waarde van een teruggeleverde
  kWh: saldering aan → afnameprijs, saldering uit → terugleververgoeding. De bestaande
  `export_price_now` houdt zijn betekenis ("vergoeding") en blijft in 2026 exact gelijk.
  **Beslissing voor jou**, zie de open vragen.

### 2.3 Plekken die `feed_in_value` gebruiken

| Plek | Entiteit / veld |
|---|---|
| `sensor.py:216-218` | `sensor.dyntarnl_e_export_price_now` (state) |
| `sensor.py:241-246, 248` | attributen `today`/`tomorrow`/`yesterday`/`prices` van die sensor (sinds 1.2.0) |
| `sensor.py:221-228` | `export_cost_now` / `export_cost_next` = `max(0, −feed_in_value)` |
| `sensor.py:235-236` | `export_loss_today` = aantal uren met `feed_in_value < 0` |
| `binary_sensor.py:53-60, 72-73` | `export_loss_now` / `export_loss_next` |
| `forecast/views.py:151` | attribuut `forecast_export` op `all_in_forecast_avg` |

Al deze plekken veranderen vanzelf mee zodra `feed_in_value` per slot de juiste periode
gebruikt. Er is **één centrale plek** om aan te passen, en dat is goed nieuws.

### 2.4 Plekken die op de beurs zelf beslissen (niet op teruglevering)

`binary_sensor.py:38-50` (`neg price now/prev/next`) en `sensor.py:231-232` (`neg hours today`)
kijken naar `market < 0`, dus beurs incl. btw. Dat is een marktsignaal en zegt niets over je
eigen vergoeding. In 2027 blijft het zinvol (afname bij negatieve beurs). **Geen wijziging**,
maar dat moet in de docs staan: "export loss" is de teruglever-beslissing, "neg price" niet.

---

## 3. Beslislogica die op het prijsverschil leunt

| Logica | Waar | Nu | Vanaf 2027 |
|---|---|---|---|
| ZeroExport / omvormer begrenzen | `export loss now/next` (binary), README-automatisering (`README.md:280-306`) | aan als `beurs < opslag` (incl. btw) | Juiste drempel: `market_ex + teruglever-opslag < k`. Met `k` = 0,02 en geen teruglever-opslag toevallig gelijk; met `k` = 0 meldt de sensor ten onrechte verlies tussen beurs 0 en 0,02 |
| Kosten van terugleveren | `export cost now/next`, `export loss today` | zie 2.1 | zie 2.1 |
| Goedkoopste blok (laden) | `forecast/views.py:49-75`, `price_allin` | afname-all-in | Juist (afname), mits de afname-all-in per periode klopt (4) |
| Accu, arbitrage, EV | niet in DynTarNL; HBA / OmniBattery | lezen `export_price_now`/`forecast_export` of rekenen zelf | Vanzelf goed als DynTarNL de juiste reeks publiceert en zij die gebruiken |
| HA Energy-dashboard | `export_price_now` als "entiteit met huidige prijs" voor teruglevering | HA vermenigvuldigt per kWh en kent geen saldering, dus onderschat in 2026 | Klopt zodra de formule klopt; HA rekent per kWh, wat in 2027 de werkelijkheid is |

---

## 4. Datumafhankelijkheid: welke regels gelden per tijdslot?

| Plek | Gebaseerd op | Fout over de jaargrens? |
|---|---|---|
| API-leveranciers (`sources.py`) | per slot uit de API | Nee |
| Custom (`sources.py:251`) | één `Tariff` voor álles | **Ja** |
| Voorspellingen (`forecast/engine.py:83-88`) | tarief van het **laatst gepubliceerde slot**, voor elk voorspeld kwartier | **Ja** |
| `forecast_chart` (`forecast/views.py:119-155`) | `data.tariff` = hetzelfde ene tarief | **Ja** |
| Gas (gasdag 06:00–06:00) | per slot | Afhankelijk van de bron; bij Custom **ja**. Let op: de gasdag 31-12 06:00 tot 1-1 06:00 valt onder de belasting die op 31-12 geldt; dat moet nog bevestigd worden |

**Voorbeeld voorspelling.** Op 28-12-2026, horizon 168 uur, lopen de voorspelde uren tot
4-1-2027. Alle uren vanaf 1-1 krijgen de opslag en belasting van 28-12:
- afname: `(0,10 + 0,02 + 0,09161) × 1,21 = 0,25605` in plaats van 0,25170, dus € 0,0043 te hoog;
- teruglevering: zie 2.1.

**Voorstel:** `tariff_at(moment)` voor elk kwartier en uur. De keuze gebeurt op de
**start van het tijdslot**: 1-1-2027 00:00 Europe/Amsterdam is 31-12 23:00 UTC. Er is geen
DST-risico, want de jaargrens valt in de wintertijd, maar de code moet wel lokale datums
gebruiken en geen UTC-datums.

---

## 5. Externe bronnen

| Bron | Gebruikte velden | Terugleverprijs? | Bij ontbreken of wijziging |
|---|---|---|---|
| Essent / Energiedirect | `tariffs[].groups[MARKET/FEE/TAX].amount/amountEx`, `vatPercentage`, `startDateTime/endDateTime` | Nee | `SourceError` → `UpdateFailed`, cache blijft |
| easyEnergy | `price`, `priceIncVat`, `energyTax`, `purchasePrice`, `invoicePrice`, `from/until` | Nee | Idem; `invoicePrice` mag ontbreken (dan som) |
| Frank | `marketPrice`, `marketPriceTax`, `sourcingMarkupPrice`, `energyTaxPrice`, `from/till` | Nee | Idem; de API veranderde eind 2026 al eens |
| EnergyZero (EPEX voor Custom) | `Prices[].price`, `readingDate` | Nee | Idem |
| Voorspellers (EpexPredictor, EPF) | kale EPEX per kwartier | n.v.t. | backoff, voorspelling valt weg, prijzen niet |

Geen enkele bron levert terugleverkosten of een aparte teruglever-opslag. Die moeten uit
de tarieventabel komen. Gaat een leverancier in 2027 wél een terugleverprijs publiceren,
dan kan die later als bron voor die periode dienen. In het ontwerp krijgt het tabelveld
dan "API wint, tabel is terugval".

---

## 6. Bestaande tests rond prijzen

| Bestand | Tests | Wat |
|---|---|---|
| `tests/test_formula.py` | 2 | `apply_formula` |
| `tests/test_prices.py` | 4 | `feed_in_value`, `slot_at` |
| `tests/test_sensors.py` | 9 | teruglever-tellingen, attributen, export price now-reeks |
| `tests/test_forecast_engine.py` | 10 | samenvoegen, `current_tariff`, all-in van voorspellingen |
| `tests/test_forecast_views.py` | ± 20 | grafiek, `forecast_export`, goedkoopste blok |
| `tests/test_custom.py`, `tests/test_sources.py` | — | Custom-formule en parsers per bron (fixtures) |
| `tests/test_dst.py`, `tests/test_gasday.py` | 9 | tijdzone en gasdag |
| `tests_ha/` | 12 | echte HA: Custom-setup, opties, reconfigure, Markdown-kaart |

**Geen** test raakt een jaargrens, een tariefwissel binnen een reeks, terugleverkosten of
saldering. Er is ook geen test die controleert dat er nergens stil een 0 wordt ingevuld voor
een ontbrekend tarief.

---

## Ontwerpvoorstel voor fase 2 (ter beslissing)

1. **Tarieventabel in de opties** (`entry.options["tariff_periods"]`), een lijst periodes:
   - velden: `start` (datum), `netting` (saldering), `tax_elec_ex`, `tax_gas_ex`, `vat`,
     `markup_elec_ex`, `markup_gas_ex`, `export_costs_ex`, `export_markup_ex` (mag negatief),
     `provisional`, `note`;
   - lege velden zijn **`None`**, nooit 0.
2. **`tariff_at(moment)`** in `prices.py` als enige plek. De keuze gaat op de slotstart in
   lokale tijd.
3. **Teruglevering per slot:** `export_value(slot, period)`:
   - saldering aan → het huidige `feed_in_value`, ongewijzigd, zodat 2026 bit-voor-bit gelijk blijft;
   - saldering uit → `market_ex + export_markup_ex − export_costs_ex`, zonder btw.

   Een ontbrekende `export_costs_ex` levert `None` op (sensor `unknown`) plus een repair,
   geen 0.
4. **API-leveranciers:** de afnameprijs blijft uit de API komen. Uit de tabel komen
   alleen de teruglevervelden, saldering, en voor voorspelde uren voorbij de gepubliceerde
   data opslag en belasting.
5. **Nieuwe entiteiten, alleen toevoegen:**
   - `sensor.dyntarnl_e_export_value_now` met reeks: de effectieve waarde (saldering aan →
     all-in);
   - `sensor.dyntarnl_tariff_period`, diagnostisch: huidige en volgende periode, ingangsdatum,
     voorlopig ja/nee;
   - `price_export` in `get_prices`.
6. **Repair** als een periode binnen 14 dagen ingaat en nog voorlopig is of lege velden
   heeft.
7. **Migratie** (config entry minor 1.2 → 1.3):
   - periode 2026 uit de huidige Custom-waarden, met saldering aan;
   - periode 2027-01-01 als voorlopig: belasting 0,08802 / gas 0,61628 (= 0,7457 / 1,21),
     saldering uit, opslag en terugleverkosten **leeg**.
8. **Nettarief 2029:** het tarief per tijdslot uit stap 2 maakt dat eenvoudig. Later volstaat
   een veld met een dagschema per periode. Netbeheer per kWh hoort dan in afname én
   teruglevering.

## Open vragen (blokkerend voor fase 2)

1. **Betekenis van `export_price_now` in 2026.** Kies:
   - **(a)** ongewijzigd laten (vergoeding: beurs − opslag) en een nieuwe sensor
     `export_value_now` toevoegen voor de effectieve waarde onder saldering; dit is mijn
     voorstel, en zo blijft 2026 bit-voor-bit gelijk;
   - **(b)** `export_price_now` in 2026 laten meebewegen met saldering. Dat breekt de huidige
     waarden en dashboards.
2. **API-leveranciers.** Mag de tabel bij Essent, Frank en easyEnergy de afname-opslag en
   -belasting **overschrijven**? Mijn voorstel is nee: de API wint, de tabel vult aan.
3. **Leeg laten in de 2027-voorstelperiode.** Jouw eis "verplichte velden ingevuld" botst met
   "geen waarden verzinnen". Voorstel: opslag en terugleverkosten mogen leeg zolang de periode
   "voorlopig" is. Dan geeft de repair een waarschuwing en is de terugleverprijs na de
   ingangsdatum `unknown`, geen stille 0.
4. **Gas** ook in de tabel? Voorstel: ja, de belasting en opslag voor gas. Saldering geldt
   niet voor gas.
5. **Energiebelasting 2026:** in de code 0,09161 / 0,60066, in jouw tabel 0,09157 / 0,60058
   (afgeleid van incl. btw). Welke is officieel?

## Na het definitieve Belastingplan en de 2027-voorwaarden invullen of controleren

- [ ] Energiebelasting stroom 2027, eerste schijf, excl. btw (voorlopig 0,08802).
- [ ] Energiebelasting gas 2027, excl. btw (voorlopig 0,61628 = 0,7457 / 1,21).
- [ ] Belasting 2026 (code 0,09161 / 0,60066 tegenover jouw tabel 0,09157 / 0,60058).
- [ ] Leveranciersopslag afname 2027 (stroom en gas), excl. btw.
- [ ] Terugleverkosten per kWh 2027, excl. btw.
- [ ] Opslag of korting op teruglevering 2027, indien van toepassing.
- [ ] Wettelijk minimum 50%: geldt dat ook voor dynamische contracten? Jouw uitgangspunt zegt
      dat de vergoeding de marktprijs volgt en negatief kan zijn. Laat het in het contract
      bevestigen; DynTarNL past geen minimum toe tenzij je dat wilt.
- [ ] Vlag "voorlopig" van de periode 2027 uitzetten.
