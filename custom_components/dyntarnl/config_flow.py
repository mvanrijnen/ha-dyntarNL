"""Config flow voor DynTarNL: kies je leverancier (of CUSTOM)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
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
    FC_MAX_HORIZON,
    FORECAST_DEFAULTS,
    WEIGHTING_ACCURACY,
    WEIGHTING_EQUAL,
    CONF_MARKUP_ELEC,
    CONF_MARKUP_GAS,
    CONF_SUPPLIER,
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


def _euro(maximum: float) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=0, max=maximum, step=0.00001, mode=NumberSelectorMode.BOX, unit_of_measurement="€"
        )
    )


class DynTarNLConfigFlow(ConfigFlow, domain=DOMAIN):
    """Leverancier kiezen; bij CUSTOM extra velden."""

    VERSION = 1
    # 1.2: voorspel-opties (standaard UIT). Bewust een minor-versie: terug naar
    # 1.x blijft mogelijk zonder de integratie opnieuw toe te voegen.
    MINOR_VERSION = 2

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
            return self.async_create_entry(
                title=supplier.name if supplier else "DynTarNL",
                data={CONF_SUPPLIER: self._supplier},
                options=dict(FORECAST_DEFAULTS),
            )

        options = [
            SelectOptionDict(value=s.key, label=s.name)
            for s in SUPPLIERS
            if s.implemented
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_SUPPLIER): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_custom(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="Custom supplier",
                data={CONF_SUPPLIER: self._supplier, **user_input},
                options=dict(FORECAST_DEFAULTS),
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_VAT, default=DEFAULT_VAT): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=100, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="%"
                    )
                ),
                vol.Required(CONF_MARKUP_ELEC, default=0.0): _euro(1),
                vol.Required(CONF_MARKUP_GAS, default=0.0): _euro(2),
                vol.Required(CONF_TAX_ELEC, default=DEFAULT_TAX_ELEC): _euro(1),
                vol.Required(CONF_TAX_GAS, default=DEFAULT_TAX_GAS): _euro(2),
                vol.Required(CONF_EPEX_SOURCE, default=DEFAULT_EPEX_SOURCE): SelectSelector(
                    SelectSelectorConfig(
                        options=list(EPEX_SOURCES),
                        translation_key="epex_source",
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="custom", data_schema=schema)


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


class DynTarNLOptionsFlow(OptionsFlow):
    """Voorspellingen aan/uit en instellen; aanpasbaar zonder opnieuw toevoegen.

    init (hoofdschakelaar) → forecast (algemeen) → provider_settings (per gekozen
    provider) → advanced. Uit = direct opslaan; de overige waarden blijven bewaard
    zodat ze terugkomen als je het later weer aanzet.
    """

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}

    def _current(self) -> dict[str, Any]:
        return {**FORECAST_DEFAULTS, **self.config_entry.options, **self._options}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._options = {**self._current(), **user_input}
            if not user_input[CONF_FORECAST]:
                return self.async_create_entry(data=self._options)
            return await self.async_step_forecast()

        current = self._current()
        schema = vol.Schema(
            {vol.Required(CONF_FORECAST, default=current[CONF_FORECAST]): BooleanSelector()}
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_forecast(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_FC_PROVIDERS):
                errors[CONF_FC_PROVIDERS] = "no_providers"
            elif user_input[CONF_FC_WEIGHTING] == WEIGHTING_ACCURACY and not user_input[CONF_FC_ACCURACY]:
                errors[CONF_FC_WEIGHTING] = "weighting_needs_accuracy"
            else:
                for key in (CONF_FC_WEIGHT_EXPONENT, CONF_FC_HORIZON, CONF_FC_INTERVAL):
                    user_input[key] = int(user_input[key])
                self._options.update(user_input)
                return await self.async_step_provider_settings()

        current = {**self._current(), **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Required(CONF_FC_PROVIDERS, default=list(current[CONF_FC_PROVIDERS])): _select(
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
        current = {**self._current(), **(user_input or {})}
        providers = [PROVIDERS[k](current) for k in current[CONF_FC_PROVIDERS] if k in PROVIDERS]
        fields: dict = {}
        for provider in providers:
            fields.update(provider.option_fields())
        if not fields:
            return await self.async_step_advanced()

        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            for provider in providers:
                if provider.option_keys and (error := await provider.async_validate(session)):
                    errors[provider.option_keys[0]] = error
            if not errors:
                self._options.update(user_input)
                return await self.async_step_advanced()

        return self.async_show_form(
            step_id="provider_settings", data_schema=vol.Schema(fields), errors=errors
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
