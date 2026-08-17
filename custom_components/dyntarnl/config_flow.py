"""Config flow voor DynTarNL: kies je leverancier (of CUSTOM)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import (
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


def _euro(maximum: float) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=0, max=maximum, step=0.00001, mode=NumberSelectorMode.BOX, unit_of_measurement="€"
        )
    )


class DynTarNLConfigFlow(ConfigFlow, domain=DOMAIN):
    """Leverancier kiezen; bij CUSTOM extra velden."""

    VERSION = 1

    def __init__(self) -> None:
        self._supplier: str | None = None

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
