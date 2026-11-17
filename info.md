# DynTarNL — Dynamische stroom- & gastarieven (NL), optioneel met prijsvoorspellingen

Publiceert dynamische energietarieven van meerdere Nederlandse leveranciers als sensoren.
Je kiest je leverancier; de integratie bepaalt zelf het platform en de publieke prijs-API.
**Geen account of API-sleutel nodig.**

## Ondersteund
- **Volledige uitsplitsing (niets invullen):** Essent, Energiedirect, Frank Energie, easyEnergy
- **Custom supplier (manual)** (eigen opslag/belasting/btw): voor élke andere leverancier —
  o.a. de EnergyZero-merken (ANWB, Coolblue, …), Nieuwestroom, Vattenfall, Eneco, Tibber,
  Zonneplan, …

## Sensoren
Prijs-sensoren (all-in & beurs) voor stroom en gas, component-sensoren (belasting/opslag),
en teruglever-/negatieve-prijs binary sensors om direct op te schakelen (ZeroExport / accu).
De terugleverprijs (beurs − opslag) staat ook als reeks in de attributen van
`sensor.dyntarnl_e_export_price_now` (gepubliceerd) en, met voorspellingen aan, als
`forecast_export` op `sensor.dyntarnl_e_all_in_forecast_avg`.

## Optioneel: prijsvoorspellingen (vanaf 1.1)
Standaard uit. Aan te zetten via *Configureren*. DynTarNL voorspelt zelf niets: het leest
EPEX-voorspellingen (standaard 72 uur, instelbaar tot 7 dagen) uit bij externe voorspellers (EpexPredictor, Energy
Price Forecast EU), middelt ze gewogen, en corrigeert ze met je eigen leveranciersformule en
hun gemeten nauwkeurigheid. Volledige reeksen via `dyntarnl.get_prices`.

> Onofficieel; geen affiliatie met de leveranciers.
