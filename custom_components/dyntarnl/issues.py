"""Repair-meldingen over de tariefperiodes (Instellingen → Systeem → Reparaties).

- `period_incomplete`: een periode die al geldt of binnen 14 dagen ingaat, staat nog als
  "voorlopig" of mist verplichte waarden (die slots krijgen dan geen prijs, geen 0).
- `export_below_minimum`: vaste/variabele terugleververgoeding onder het wettelijk minimum
  (50% van het kale leveringstarief) in 2027–2029.

Wordt bij het opzetten en elke nacht opnieuw bepaald; opgeloste meldingen verdwijnen vanzelf.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import DOMAIN, PERIOD_WARN_DAYS
from .tariffs import Period, below_export_minimum, missing_fields

_PREFIXES = ("period_incomplete_", "export_below_minimum_")


def period_issues(periods: tuple[Period, ...], custom: bool, now: datetime) -> dict[str, tuple[str, dict]]:
    """issue_id → (translation_key, placeholders). Puur, zodat het los te testen is."""
    today = dt_util.as_local(now).date()
    horizon = today + timedelta(days=PERIOD_WARN_DAYS)
    out: dict[str, tuple[str, dict]] = {}
    for i, period in enumerate(periods):
        until = periods[i + 1].start if i + 1 < len(periods) else None
        if until is not None and until <= today:
            continue  # periode is al voorbij
        start = period.start.isoformat()
        missing = missing_fields(period, custom)
        if period.start <= horizon and (period.provisional or missing):
            out[f"period_incomplete_{start}"] = (
                "period_incomplete",
                {
                    "start": start,
                    "provisional": "ja" if period.provisional else "nee",
                    "missing": ", ".join(missing) or "-",
                },
            )
        if below_export_minimum(period, until):
            out[f"export_below_minimum_{start}"] = ("export_below_minimum", {"start": start})
    return out


def async_update_issues(hass: HomeAssistant, periods: tuple[Period, ...], custom: bool) -> None:
    wanted = period_issues(periods, custom, dt_util.now())
    registry = ir.async_get(hass)
    for domain, issue_id in list(registry.issues):
        if domain == DOMAIN and issue_id.startswith(_PREFIXES) and issue_id not in wanted:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
    for issue_id, (key, placeholders) in wanted.items():
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=key,
            translation_placeholders=placeholders,
        )


# Teruglever-entiteiten met de oude betekenis ("vergoeding" = beurs − opslag, incl. btw).
# Ze blijven in 1.x exact werken; in 2.0 vervangen door de verkoop-entiteiten (sell_*).
LEGACY_EXPORT = {
    "sensor": {
        "electricity_feedin_compensation_now": "sensor.dyntarnl_e_sell_now",
        "electricity_feedin_cost_now": "sensor.dyntarnl_e_sell_now",
        "electricity_feedin_cost_next_hour": "sensor.dyntarnl_e_sell_next",
        "electricity_feedin_cost_hours_today": "sensor.dyntarnl_e_sell_table",
    },
    "binary_sensor": {
        "electricity_feedin_costs_money_now": "binary_sensor.dyntarnl_e_sell_negative_now",
        "electricity_feedin_costs_money_next_hour": "binary_sensor.dyntarnl_e_sell_negative_now",
    },
}


def async_check_legacy_usage(hass: HomeAssistant, entry_id: str) -> None:
    """Repair-melding alleen als een oude teruglever-entiteit in een automatisering of
    script wordt gebruikt (zoals HA het zelf doet bij vervallende entiteiten)."""
    try:
        from homeassistant.components.automation import automations_with_entity
        from homeassistant.components.script import scripts_with_entity
        from homeassistant.helpers import entity_registry as er
    except ImportError:  # pragma: no cover - alleen zonder volledige HA (tests)
        return
    registry = er.async_get(hass)
    used: list[str] = []
    for platform, mapping in LEGACY_EXPORT.items():
        for suffix, replacement in mapping.items():
            entity_id = registry.async_get_entity_id(platform, DOMAIN, f"{entry_id}_{suffix}")
            if entity_id and (automations_with_entity(hass, entity_id) or scripts_with_entity(hass, entity_id)):
                used.append(f"{entity_id} → {replacement}")
    if used:
        ir.async_create_issue(
            hass,
            DOMAIN,
            "legacy_export_entities",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="legacy_export_entities",
            translation_placeholders={"entities": "\n".join(f"- {u}" for u in used)},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, "legacy_export_entities")
