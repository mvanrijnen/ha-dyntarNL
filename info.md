# DynTarNL — Dynamische stroom- & gastarieven (NL)

Publiceert dynamische energietarieven van meerdere Nederlandse leveranciers als sensoren.
Je kiest je leverancier; de integratie bepaalt zelf het platform en de publieke prijs-API.
**Geen account of API-sleutel nodig.**

## Ondersteund
- **Volledige breakdown:** Essent, Energiedirect, Frank Energie
- **Marktprijs + belasting:** EnergyZero, ANWB, Coolblue, Energie VanOns, GroeneStroomLokaal, SamSam, Hegg
- **EPEX + belasting:** Nieuwestroom, EasyEnergie
- **CUSTOM** (eigen opslag/belasting/btw): voor élke andere leverancier — Vattenfall, Eneco, Tibber, Zonneplan, …

## Sensoren
Prijs-sensoren (all-in & beurs) voor stroom en gas, component-sensoren (belasting/opslag),
en teruglever-/negatieve-prijs binary sensors om direct op te schakelen (ZeroExport / accu).

## Optioneel: prijsvoorspellingen (2.0)
Standaard uit. Aan te zetten via *Configureren*: EPEX-voorspellingen tot 7 dagen vooruit
(EpexPredictor, Energy Price Forecast EU), met je eigen leveranciersformule, een gewogen
ensemble en automatische nauwkeurigheidsmeting. Volledige reeksen via `dyntarnl.get_prices`.

> Onofficieel; geen affiliatie met de leveranciers.
