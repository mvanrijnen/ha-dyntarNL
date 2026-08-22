# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`dyntarnl` — a Home Assistant custom integration (HACS, single config entry, no auth) that
publishes dynamic Dutch electricity & gas tariffs as sensors. The user picks a *supplier
brand*; the integration derives the *platform* (price API + response shape) from it.

Everything the user sees (README, `strings.json`) plus most code comments/docstrings are in
**Dutch**; identifiers, entity keys and entity names are English. Keep that split when editing.

## Commands

```bash
pytest                 # full suite (~30 tests, no HA install, no network)
pytest tests/test_sources.py::test_frank_parser -q            # single test
pytest -k gasday
```

CI (`.github/workflows/`): `pytest` on Python 3.13, plus hassfest and the HACS action
(`ignore: brands` — dyntarnl is not on the brands CDN and never will be, see below).

There is no linter/formatter configured. Tests are the only gate.

## GitHub

Repo: <https://github.com/mvanrijnen/ha-dyntarNL> (public, `origin`, default branch `main`).
The `gh` CLI is authenticated as `mvanrijnen`; use it for issues, PRs, releases and run logs.

Releases are what HACS installs, so the order matters:

1. Bump `"version"` in `custom_components/dyntarnl/manifest.json` **first**,
   and update the version mentioned in the README banner.
2. Commit, push, then `gh release create vX.Y.Z --generate-notes`.

The tag must point at a commit whose manifest already carries that version — HACS reads the
version out of the tarball's manifest, not out of the tag. (`v0.1.0` was cut one commit early
and ships `"version": "0.0.2"`; don't repeat that.)

## Architecture

Data flows: **supplier registry → platform fetcher → shared `Slot` model → entities.**

- `const.py` — `SUPPLIERS` is the registry: each `Supplier` maps a user-visible brand key to
  one of the `PLATFORM_*` constants (and, for eon-app, a `host`). Adding a brand that reuses
  an existing platform is a one-line change here. All config keys and API URLs live here too.
- `sources.py` — one `fetch_*` coroutine per platform (`fetch_eon_app`, `fetch_easyenergy`,
  `fetch_frank`, `fetch_energyzero`) plus `build_custom`. Each normalises a vendor payload
  into `list[Slot]` and returns `PriceData`. This is the only module that talks to the network.
- `model.py` — `Slot` (one hour: `total`/`market`/`fee`/`tax`, each with an `_ex` excl.-VAT
  twin) and `bucket_by_day()`, which splits a flat slot list into yesterday/today/tomorrow by
  *local* date. `PriceData = dict[str, EnergyData]` keyed by `ELECTRICITY`/`GAS`.
- `coordinator.py` — dispatches on `supplier.platform`, then calls `fetch_epex()` **only** when
  `_needs_epex_fallback()` finds slots without a market price. `update_interval=None`.
- `entity.py` / `sensor.py` / `binary_sensor.py` / `button.py` — entities are generated from
  description tuples (`_METRICS × _BASES`, `_COMPONENTS`, `_FEEDIN_SENSORS`, `_BINARY_SENSORS`)
  rather than written out one by one. Adding a metric = adding a tuple entry + a small pure
  function taking `(EnergyData, datetime, PriceFn)`.
- `__init__.py` — owns *when* data is fetched (see below) and registers the `dyntarnl.refresh`
  service.

### Invariants worth knowing

- **VAT convention differs per source and is the main source of bugs.** eon-app gives separate
  `amount`/`amountEx` per group; easyEnergy and Frank give `purchasePrice`/`sourcingMarkupPrice`
  and tax *already incl. VAT* (only Frank's `marketPrice` has a separate `marketPriceTax` line),
  so the `_ex` fields are back-derived via `factor = market / market_ex`. Custom is the only
  path where the user's inputs are excl. VAT and `× (1 + vat/100)` is applied.
- **Gas follows the Dutch gas day (06:00–06:00)**, so gas slots are not "one price per calendar
  day". `slot_at()` is start-inclusive/end-exclusive and works for both energy types; never
  index price arrays by hour.
- **Fetch cadence is deliberately sparse** (`__init__.py`): startup, 00:00 (re-bucket the new
  day), and 13:30/14:30/15:30/16:30 for tomorrow's day-ahead prices. Every other whole hour only
  calls `async_update_listeners()` — sensors roll forward from cache with **no network call**.
  Do not add an `update_interval`.
- **Feed-in threshold** lives in `prices.py`: `feed_in_value = market − fee`. Negative means
  exporting costs money. Sources without a real markup (`fee = 0`) collapse this to `market < 0`.
- **Unique IDs** are `{entry_id}_{energy}_{...}` and entity names are compact English
  (`e_all_in_now`). Changing either renames user entities — a breaking change.

### Adding a new supplier

1. If it reuses an existing API: add a `Supplier(...)` line to `SUPPLIERS`, done.
2. If it needs a new API: add `PLATFORM_*` + URL to `const.py`, a `fetch_*` to `sources.py`, a
   branch in `DynTarNLCoordinator._async_update_data`, a fixture under `tests/fixtures/`, and a
   parse test.
3. Only list a supplier if its API gives the **complete** breakdown (market + markup + tax).
   Market-price-only sources (EnergyZero brands) and dynamic-markup ones (Nieuwestroom) must
   stay off the dropdown and be handled through CUSTOM — otherwise the all-in price is wrong.
   `fetch_energyzero` exists as an EPEX source for CUSTOM, not as a selectable supplier.

## Tests

`tests/conftest.py` injects hand-written stub modules for `homeassistant.*` and `aiohttp` into
`sys.modules` *before* the integration is imported, and puts `custom_components/` on the path —
so tests import `dyntarnl.x`, and HA is never installed. Consequences:

- If you use a new HA symbol in the integration, add it to `_install_ha_stubs()` or every test
  fails at import.
- "Now" is frozen at `DEFAULT_NOW` (2026-08-17 14:00 +02:00) to match the recorded fixtures. Use
  the `at_time` fixture to move it; it restores itself.
- Network calls are stubbed by `monkeypatch.setattr(src, "_get_json", ...)` against the JSON in
  `tests/fixtures/`. Never let a test hit a live API.

## Brand assets

Since HA 2026.3 custom integrations ship their own images in
`custom_components/dyntarnl/brand/` (`icon.png` 256, `icon@2x.png` 512, `logo.png` 256×71,
`logo@2x.png` 512×142); `home-assistant/brands` no longer accepts custom PRs. The top-level
`brand/` folder holds the SVG/PNG sources used in the README.
