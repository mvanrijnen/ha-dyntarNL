"""Config flow voor DynTarNL: kies je leverancier (of CUSTOM)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.util import dt as dt_util
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    BooleanSelector,
    DateSelector,
    TextSelector,
    TimeSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_EPEX_SOURCE,
    CONF_FC_ACCURACY,
    CONF_FC_BIAS,
    CONF_FC_CHEAPEST_HOURS,
    CONF_FC_ENSEMBLE,
    CONF_FC_HORIZON,
    CONF_FC_INTERVAL,
    CONF_FC_MIN_SAMPLES,
    CONF_FC_PROVIDERS,
    CONF_FC_WEIGHT_EXPONENT,
    CONF_FC_WEIGHT_FLOOR,
    CONF_FC_WEIGHTING,
    CONF_FC_WINDOW,
    CONF_FORECAST,
    EPF_PRIVATE_PRO_URL,
    FC_MAX_HORIZON,
    FORECAST_DEFAULTS,
    WEIGHTING_ACCURACY,
    WEIGHTING_EQUAL,
    CONF_MARKUP_ELEC,
    CONF_MARKUP_GAS,
    CONF_PERIODS,
    CONF_SUPPLIER,
    CONTRACT_DYNAMIC,
    CONTRACTS,
    RATE_DOUBLE,
    RATE_INPUT_ALL_IN,
    RATE_INPUT_BARE,
    RATE_SINGLE,
    CONF_TAX_ELEC,
    CONF_TAX_GAS,
    CONF_VAT,
    DEFAULT_EPEX_SOURCE,
    DEFAULT_TAX_ELEC,
    DEFAULT_TAX_GAS,
    DEFAULT_VAT,
    DOMAIN,
    EPEX_SOURCES,
    PLATFORM_CUSTOM,
    SUPPLIERS,
    supplier_by_key,
)
from .forecast.providers import PROVIDERS
from .tariffs import Period, initial_periods, missing_fields, period_at, periods_from_options


def _euro(maximum: float) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            # HA accepteert step >= 0.001 of "any"; tarieven hebben 5 decimalen nodig.
            min=0, max=maximum, step="any", mode=NumberSelectorMode.BOX, unit_of_measurement="€"
        )
    )


def _supplier_schema(default: str | None = None) -> vol.Schema:
    options = [SelectOptionDict(value=s.key, label=s.name) for s in SUPPLIERS if s.implemented]
    key = vol.Required(CONF_SUPPLIER, default=default) if default else vol.Required(CONF_SUPPLIER)
    return vol.Schema(
        {key: SelectSelector(SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN))}
    )


def _custom_schema(current: dict[str, Any] | None = None) -> vol.Schema:
    """CUSTOM-tarieven; bij opnieuw configureren met de huidige waarden ingevuld."""
    c = current or {}
    return vol.Schema(
        {
            vol.Required(CONF_VAT, default=c.get(CONF_VAT, DEFAULT_VAT)): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=100, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="%"
                )
            ),
            vol.Required(CONF_MARKUP_ELEC, default=c.get(CONF_MARKUP_ELEC, 0.0)): _euro(1),
            vol.Required(CONF_MARKUP_GAS, default=c.get(CONF_MARKUP_GAS, 0.0)): _euro(2),
            vol.Required(CONF_TAX_ELEC, default=c.get(CONF_TAX_ELEC, DEFAULT_TAX_ELEC)): _euro(1),
            vol.Required(CONF_TAX_GAS, default=c.get(CONF_TAX_GAS, DEFAULT_TAX_GAS)): _euro(2),
            vol.Required(
                CONF_EPEX_SOURCE, default=c.get(CONF_EPEX_SOURCE, DEFAULT_EPEX_SOURCE)
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(EPEX_SOURCES),
                    translation_key="epex_source",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


_CUSTOM_PERIOD_FIELDS = {
    CONF_VAT: "vat",
    CONF_MARKUP_ELEC: "markup_elec_ex",
    CONF_MARKUP_GAS: "markup_gas_ex",
    CONF_TAX_ELEC: "tax_elec_ex",
    CONF_TAX_GAS: "tax_gas_ex",
}


def _with_custom_tariffs(options: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """Custom-tarieven uit het formulier in de tariefperiode van vandaag zetten."""
    raw = [dict(p) for p in options.get(CONF_PERIODS) or []]
    if not raw:
        raw = initial_periods(data, True, dt_util.now().date())
    today = dt_util.now().date().isoformat()
    current = max((p for p in raw if p["start"] <= today), key=lambda p: p["start"], default=None)
    if current is None:
        current = min(raw, key=lambda p: p["start"])
    for key, attr in _CUSTOM_PERIOD_FIELDS.items():
        if key in data:
            current[attr] = float(data[key])
    return {**options, CONF_PERIODS: raw}


def _initial_options(data: dict[str, Any], custom: bool) -> dict[str, Any]:
    """Opties van een nieuwe entry: voorspellingen uit, de huidige tariefperiode uit de
    ingevulde waarden plus een voorlopige periode vanaf 2027-01-01."""
    return {
        **FORECAST_DEFAULTS,
        CONF_PERIODS: initial_periods(data, custom, dt_util.now().date()),
    }


class DynTarNLConfigFlow(ConfigFlow, domain=DOMAIN):
    """Leverancier kiezen; bij CUSTOM extra velden. Later te wijzigen via
    'Opnieuw configureren' (reconfigure): entiteiten, historie en opties blijven."""

    VERSION = 1
    # 1.2: voorspel-opties (standaard UIT). 1.3: tariefperiodes. Bewust minor-versies:
    # terug naar 1.x blijft mogelijk zonder de integratie opnieuw toe te voegen.
    MINOR_VERSION = 3

    def __init__(self) -> None:
        self._supplier: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return DynTarNLOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._supplier = user_input[CONF_SUPPLIER]
            supplier = supplier_by_key(self._supplier)
            if supplier and supplier.platform == PLATFORM_CUSTOM:
                return await self.async_step_custom()
            data = {CONF_SUPPLIER: self._supplier}
            return self.async_create_entry(
                title=supplier.name if supplier else "DynTarNL",
                data=data,
                options=_initial_options(data, custom=False),
            )

        return self.async_show_form(step_id="user", data_schema=_supplier_schema())

    async def async_step_custom(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            data = {CONF_SUPPLIER: self._supplier, **user_input}
            return self.async_create_entry(
                title="Custom supplier",
                data=data,
                options=_initial_options(data, custom=True),
            )

        return self.async_show_form(step_id="custom", data_schema=_custom_schema())

    # --- Opnieuw configureren: andere leverancier of andere CUSTOM-tarieven ------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Leverancier wijzigen zonder de integratie te verwijderen.

        Dezelfde config entry blijft bestaan, dus entity_id's, historie en de
        voorspel-opties blijven behouden; de entry wordt alleen herladen.
        """
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            self._supplier = user_input[CONF_SUPPLIER]
            supplier = supplier_by_key(self._supplier)
            if supplier and supplier.platform == PLATFORM_CUSTOM:
                return await self.async_step_reconfigure_custom()
            return self._update_and_abort(
                entry, supplier.name if supplier else "DynTarNL", {CONF_SUPPLIER: self._supplier}
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_supplier_schema(entry.data.get(CONF_SUPPLIER)),
        )

    def _update_and_abort(
        self, entry: ConfigEntry, title: str, data: dict[str, Any], options: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Entry bijwerken; het herladen doet de update-listener uit __init__.py.

        Niet async_update_reload_and_abort: die herlaadt zelf ook, en HA meldt dat dubbele
        herladen (stopt met werken in HA 2026.12).
        """
        if options is None:
            self.hass.config_entries.async_update_entry(entry, title=title, data=data)
        else:
            self.hass.config_entries.async_update_entry(entry, title=title, data=data, options=options)
        if entry.state in (ConfigEntryState.SETUP_RETRY, ConfigEntryState.SETUP_ERROR):
            # Opzetten mislukte (bijv. bron onbereikbaar): dan is er geen update-listener;
            # meteen opnieuw proberen met de nieuwe leverancier i.p.v. HA's backoff af te wachten.
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_abort(reason="reconfigure_successful")

    async def async_step_reconfigure_custom(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            data = {CONF_SUPPLIER: self._supplier, **user_input}
            return self._update_and_abort(
                entry, "Custom supplier", data, _with_custom_tariffs(dict(entry.options), data)
            )
        # Huidige tarieven voorinvullen: uit de geldende tariefperiode, anders uit data.
        current = dict(entry.data)
        period = period_at(periods_from_options(entry.options), dt_util.now())
        if period is not None:
            for key, attr in _CUSTOM_PERIOD_FIELDS.items():
                if getattr(period, attr) is not None:
                    current[key] = getattr(period, attr)
        return self.async_show_form(
            step_id="reconfigure_custom", data_schema=_custom_schema(current)
        )


def _number(minimum: float, maximum: float, step: float, unit: str | None = None) -> NumberSelector:
    config = NumberSelectorConfig(min=minimum, max=maximum, step=step, mode=NumberSelectorMode.BOX)
    if unit:
        # HA valideert de config: unit_of_measurement=None is ongeldig, dus weglaten.
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


def _select(options: list[str], key: str, multiple: bool = False) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            translation_key=key,
            multiple=multiple,
            mode=SelectSelectorMode.LIST if multiple else SelectSelectorMode.DROPDOWN,
        )
    )


# --- Tariefperiodes ---------------------------------------------------------------------

_GENERAL_KEYS = ("start", "contract", "netting", "vat", "tax_elec_ex", "tax_gas_ex", "provisional", "note")
_DYNAMIC_BUY = ("markup_elec_ex", "markup_gas_ex")
_FIXED_BUY = (
    "rate_mode", "rate_input", "rate_elec", "rate_elec_low", "rate_gas",
    "low_start", "low_end", "low_weekend", "low_holidays", "fixed_costs_month",
)
_DYNAMIC_SELL = ("export_markup_ex", "export_costs_ex", "export_costs_year")
_FIXED_SELL = ("export_rate_ex", "export_rate_low_ex", "export_costs_ex", "export_costs_year")


def _amount(minimum: float, maximum: float, unit: str = "€/kWh") -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(min=minimum, max=maximum, step="any", mode=NumberSelectorMode.BOX,
                             unit_of_measurement=unit)
    )


def _opt(key: str, current: dict[str, Any]) -> vol.Optional:
    """Optioneel veld dat leeg mag blijven (None, nooit een stille 0)."""
    value = current.get(key)
    return vol.Optional(key, description={"suggested_value": value} if value is not None else None)


class _PeriodSteps:
    """Mixin voor de options flow: tariefperiodes toevoegen, wijzigen en verwijderen."""

    _editing: str | None
    _draft: dict[str, Any]

    def _periods(self) -> list[dict[str, Any]]:
        return [dict(p) for p in self._current().get(CONF_PERIODS) or []]

    def _custom(self) -> bool:
        supplier = supplier_by_key(self.config_entry.data.get(CONF_SUPPLIER, ""))
        return supplier is not None and supplier.platform == PLATFORM_CUSTOM

    def _save_periods(self, periods: list[dict[str, Any]]) -> ConfigFlowResult:
        periods.sort(key=lambda p: p["start"])
        return self.async_create_entry(data={**self._current(), CONF_PERIODS: periods})

    async def async_step_tariffs(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        options = ["period_add"]
        if self._periods():
            options += ["period_edit", "period_delete"]
        return self.async_show_menu(step_id="tariffs", menu_options=options)

    def _period_choice(self) -> vol.Schema:
        periods = self._periods()
        choices = [
            SelectOptionDict(
                value=p["start"],
                label=f"{p['start']} · {p.get('contract', 'dynamic')}" + (" · voorlopig" if p.get("provisional") else ""),
            )
            for p in periods
        ]
        return vol.Schema(
            {vol.Required("start"): SelectSelector(SelectSelectorConfig(options=choices, mode=SelectSelectorMode.LIST))}
        )

    async def async_step_period_add(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._editing = None
        self._draft = {}
        return await self.async_step_period_general()

    async def async_step_period_edit(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._editing = user_input["start"]
            self._draft = next((p for p in self._periods() if p["start"] == self._editing), {})
            return await self.async_step_period_general()
        return self.async_show_form(step_id="period_edit", data_schema=self._period_choice())

    async def async_step_period_delete(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            remaining = [p for p in self._periods() if p["start"] != user_input["start"]]
            if not remaining:
                errors["base"] = "last_period"
            elif min(p["start"] for p in remaining) > dt_util.now().date().isoformat():
                errors["base"] = "gap_before_first"
            else:
                return self._save_periods(remaining)
        return self.async_show_form(step_id="period_delete", data_schema=self._period_choice(), errors=errors)

    async def async_step_period_general(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            start = str(user_input["start"])[:10]
            others = [p for p in self._periods() if p["start"] != self._editing]
            if any(p["start"] == start for p in others):
                errors["start"] = "duplicate_start"
            elif min([start] + [p["start"] for p in others]) > dt_util.now().date().isoformat():
                errors["start"] = "gap_before_first"
            else:
                for key in ("tax_elec_ex", "tax_gas_ex", "note"):
                    user_input.setdefault(key, None if key != "note" else "")
                self._draft = {**self._draft, **user_input, "start": start}
                return await self.async_step_period_details()

        d = {**self._draft, **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Required("start", default=d.get("start", dt_util.now().date().isoformat())): DateSelector(),
                vol.Required("contract", default=d.get("contract", CONTRACT_DYNAMIC)): _select(
                    list(CONTRACTS), "contract"
                ),
                vol.Required("netting", default=d.get("netting", True)): BooleanSelector(),
                vol.Required("vat", default=d.get("vat", DEFAULT_VAT)): _amount(0, 100, "%"),
                _opt("tax_elec_ex", d): _amount(0, 1),
                _opt("tax_gas_ex", d): _amount(0, 3, "€/m³"),
                vol.Required("provisional", default=d.get("provisional", False)): BooleanSelector(),
                vol.Optional("note", default=d.get("note") or ""): TextSelector(),
            }
        )
        return self.async_show_form(step_id="period_general", data_schema=schema, errors=errors)

    async def async_step_period_details(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        dynamic = self._draft.get("contract", CONTRACT_DYNAMIC) == CONTRACT_DYNAMIC
        buy_keys = _DYNAMIC_BUY if dynamic else _FIXED_BUY
        sell_keys = _DYNAMIC_SELL if dynamic else _FIXED_SELL
        errors: dict[str, str] = {}
        placeholders = {"missing": ""}
        if user_input is not None:
            values = {**user_input.get("buy", {}), **user_input.get("sell", {})}
            for key in (*buy_keys, *sell_keys):
                values.setdefault(key, None)  # leeggemaakt veld = leeg, niet de oude waarde
            # Velden van het andere contracttype vervallen (geen verborgen oude waarden).
            stale = set(_DYNAMIC_BUY + _FIXED_BUY + _DYNAMIC_SELL + _FIXED_SELL) - set(buy_keys + sell_keys)
            draft = {k: v for k, v in {**self._draft, **values}.items() if k not in stale}
            try:
                period = Period.from_dict(draft)
            except (TypeError, ValueError):
                errors["base"] = "invalid_period"
            else:
                missing = missing_fields(period, self._custom())
                if missing and not period.provisional:
                    errors["base"] = "missing_fields"
                    placeholders["missing"] = ", ".join(missing)
                else:
                    periods = [p for p in self._periods() if p["start"] != self._editing]
                    periods.append(period.to_dict())
                    return self._save_periods(periods)

        d = {**self._draft, **{k: v for part in (user_input or {}).values() if isinstance(part, dict) for k, v in part.items()}}
        if dynamic:
            buy = {
                _opt("markup_elec_ex", d): _amount(0, 1),
                _opt("markup_gas_ex", d): _amount(0, 3, "€/m³"),
            }
            sell = {
                _opt("export_markup_ex", d): _amount(-1, 1),
                _opt("export_costs_ex", d): _amount(0, 1),
                _opt("export_costs_year", d): _amount(0, 5000, "€"),
            }
        else:
            buy = {
                vol.Required("rate_mode", default=d.get("rate_mode", RATE_SINGLE)): _select(
                    [RATE_SINGLE, RATE_DOUBLE], "rate_mode"
                ),
                vol.Required("rate_input", default=d.get("rate_input", RATE_INPUT_ALL_IN)): _select(
                    [RATE_INPUT_ALL_IN, RATE_INPUT_BARE], "rate_input"
                ),
                _opt("rate_elec", d): _amount(0, 2),
                _opt("rate_elec_low", d): _amount(0, 2),
                _opt("rate_gas", d): _amount(0, 5, "€/m³"),
                vol.Required("low_start", default=d.get("low_start", "23:00")): TimeSelector(),
                vol.Required("low_end", default=d.get("low_end", "07:00")): TimeSelector(),
                vol.Required("low_weekend", default=d.get("low_weekend", True)): BooleanSelector(),
                vol.Required("low_holidays", default=d.get("low_holidays", False)): BooleanSelector(),
                _opt("fixed_costs_month", d): _amount(0, 1000, "€"),
            }
            sell = {
                _opt("export_rate_ex", d): _amount(-1, 2),
                _opt("export_rate_low_ex", d): _amount(-1, 2),
                _opt("export_costs_ex", d): _amount(0, 1),
                _opt("export_costs_year", d): _amount(0, 5000, "€"),
            }
        schema = vol.Schema(
            {
                vol.Required("buy"): section(vol.Schema(buy), {"collapsed": False}),
                vol.Required("sell"): section(vol.Schema(sell), {"collapsed": False}),
            }
        )
        return self.async_show_form(
            step_id="period_details_dynamic" if dynamic else "period_details_fixed",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    # Twee step-id's (andere teksten per contracttype), één implementatie.
    async_step_period_details_dynamic = async_step_period_details
    async_step_period_details_fixed = async_step_period_details


class DynTarNLOptionsFlow(_PeriodSteps, OptionsFlow):
    """Instellingen achteraf: menu met voorspellingen en tariefperiodes.

    Voorspellingen: forecast_switch (hoofdschakelaar) → forecast (algemeen) →
    provider_settings (per gekozen provider) → advanced. Uit = direct opslaan; de overige
    waarden blijven bewaard zodat ze terugkomen als je het later weer aanzet.
    Tariefperiodes: zie _PeriodSteps. Opslaan herlaadt de integratie (geen herstart).
    """

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._editing: str | None = None
        self._draft: dict[str, Any] = {}

    def _current(self) -> dict[str, Any]:
        return {**FORECAST_DEFAULTS, **self.config_entry.options, **self._options}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["forecast_switch", "tariffs"])

    async def async_step_forecast_switch(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._options = {**self._current(), **user_input}
            if not user_input[CONF_FORECAST]:
                return self.async_create_entry(data=self._options)
            return await self.async_step_forecast()

        current = self._current()
        schema = vol.Schema(
            {vol.Required(CONF_FORECAST, default=current[CONF_FORECAST]): BooleanSelector()}
        )
        return self.async_show_form(step_id="forecast_switch", data_schema=schema)

    async def async_step_forecast(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_FC_PROVIDERS):
                errors[CONF_FC_PROVIDERS] = "no_providers"
            elif (
                user_input[CONF_FC_ENSEMBLE]  # weging speelt alleen bij het ensemble
                and user_input[CONF_FC_WEIGHTING] == WEIGHTING_ACCURACY
                and not user_input[CONF_FC_ACCURACY]
            ):
                errors[CONF_FC_WEIGHTING] = "weighting_needs_accuracy"
            else:
                for key in (CONF_FC_WEIGHT_EXPONENT, CONF_FC_HORIZON, CONF_FC_INTERVAL):
                    user_input[key] = int(user_input[key])
                self._options.update(user_input)
                return await self.async_step_provider_settings()

        current = {**self._current(), **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_FC_PROVIDERS, default=[k for k in current[CONF_FC_PROVIDERS] if k in PROVIDERS]
                ): _select(
                    list(PROVIDERS), "forecast_provider", multiple=True
                ),
                vol.Required(CONF_FC_ENSEMBLE, default=current[CONF_FC_ENSEMBLE]): BooleanSelector(),
                vol.Required(CONF_FC_WEIGHTING, default=current[CONF_FC_WEIGHTING]): _select(
                    [WEIGHTING_ACCURACY, WEIGHTING_EQUAL], "forecast_weighting"
                ),
                vol.Required(
                    CONF_FC_WEIGHT_EXPONENT, default=str(current[CONF_FC_WEIGHT_EXPONENT])
                ): _select(["1", "2"], "forecast_weight_exponent"),
                vol.Required(CONF_FC_ACCURACY, default=current[CONF_FC_ACCURACY]): BooleanSelector(),
                vol.Required(CONF_FC_HORIZON, default=current[CONF_FC_HORIZON]): _number(24, FC_MAX_HORIZON, 1, "h"),
                vol.Required(CONF_FC_INTERVAL, default=current[CONF_FC_INTERVAL]): _number(2, 24, 1, "h"),
            }
        )
        return self.async_show_form(step_id="forecast", data_schema=schema, errors=errors)

    async def async_step_provider_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Provider-specifieke velden (URL, API-key) van alle gekozen providers.

        Generiek: elke provider levert zijn eigen velden via `option_fields()`; een
        nieuwe provider hoeft hier niets aan te passen.
        """
        selected = [PROVIDERS[k] for k in self._current()[CONF_FC_PROVIDERS] if k in PROVIDERS]
        submitted: dict[str, Any] = {}
        if user_input is not None:
            # Een leeggemaakt optioneel veld stuurt HA niet mee: dat betekent 'leeg',
            # niet 'houd de oude waarde'.
            for cls in selected:
                for key in cls.option_keys:
                    submitted[key] = user_input.get(key, FORECAST_DEFAULTS.get(key))
        current = {**self._current(), **submitted}
        providers = [cls(current) for cls in selected]
        fields: dict = {}
        for provider in providers:
            fields.update(provider.option_fields())
        if not fields:
            return await self.async_step_advanced()

        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            stored = self._current()
            for provider in providers:
                # Alleen een testcall als je de instellingen van deze bron wijzigt (en niet
                # voor een lege API-key): anders kun je niets opslaan zolang een bron even
                # onbereikbaar is, en duurt opslaan tot 20 s per bron.
                changed = any(submitted.get(k) != stored.get(k) for k in provider.option_keys)
                filled = any(submitted.get(k) for k in provider.option_keys)
                if changed and filled and (error := await provider.async_validate(session)):
                    errors[provider.option_keys[0]] = error
            if not errors:
                self._options.update(submitted)
                return await self.async_step_advanced()

        return self.async_show_form(
            step_id="provider_settings",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"private_pro_url": EPF_PRIVATE_PRO_URL},
        )

    async def async_step_advanced(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            for key in (CONF_FC_MIN_SAMPLES, CONF_FC_WINDOW, CONF_FC_CHEAPEST_HOURS):
                user_input[key] = int(user_input[key])
            self._options.update(user_input)
            return self.async_create_entry(data=self._options)

        current = self._current()
        schema = vol.Schema(
            {
                vol.Required(CONF_FC_MIN_SAMPLES, default=current[CONF_FC_MIN_SAMPLES]): _number(0, 2000, 1),
                vol.Required(CONF_FC_WEIGHT_FLOOR, default=current[CONF_FC_WEIGHT_FLOOR]): _number(0, 0.5, 0.01),
                vol.Required(CONF_FC_BIAS, default=current[CONF_FC_BIAS]): BooleanSelector(),
                vol.Required(CONF_FC_WINDOW, default=current[CONF_FC_WINDOW]): _number(7, 90, 1, "d"),
                vol.Required(CONF_FC_CHEAPEST_HOURS, default=current[CONF_FC_CHEAPEST_HOURS]): _number(1, 12, 1, "h"),
            }
        )
        return self.async_show_form(step_id="advanced", data_schema=schema)
