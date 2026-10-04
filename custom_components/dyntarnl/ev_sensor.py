"""Sensoren voor de laadkosten van de EV-lader (alleen als die optie aan staat)."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import CURRENCY_EURO
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.util import dt as dt_util

from .const import DOMAIN, NAME
from .ev import ALL_KEYS, SESSION, EvCostManager

_NAMES = {
    "session": "cost session",
    "day": "cost today",
    "month": "cost month",
    "quarter": "cost quarter",
    "year": "cost year",
    "total": "cost total",
}


def ev_unique_ids(entry_id: str, enabled: bool) -> set[str]:
    return {f"{entry_id}_ev_cost_{key}" for key in ALL_KEYS} if enabled else set()


def is_ev_unique_id(entry_id: str, unique_id: str) -> bool:
    return unique_id.startswith(f"{entry_id}_ev_")


def _local(value: str | None) -> str | None:
    return dt_util.as_local(dt_util.parse_datetime(value)).isoformat() if value else None


class DynTarNLEvCostSensor(SensorEntity):
    """Laadkosten (all-in, €) voor één periode, met kWh en gemiddelde prijs erbij."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY_EURO
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:ev-station"
    _unrecorded_attributes = frozenset({"sessions"})

    def __init__(self, manager: EvCostManager, key: str) -> None:
        self._manager = manager
        self._key = key
        entry_id = manager.entry.entry_id
        self._attr_name = _NAMES[key]
        self._attr_unique_id = f"{entry_id}_ev_cost_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_ev")},
            name=f"{NAME} EV",
            manufacturer=NAME,
            model="Charging cost",
            entry_type=DeviceEntryType.SERVICE,
        )

    def _value(self) -> dict:
        return self._manager.tracker.value(self._key, dt_util.utcnow())

    @property
    def native_value(self) -> float:
        return round(self._value()["cost"], 4)

    @property
    def last_reset(self) -> datetime | None:
        return self._manager.tracker.last_reset(self._key, dt_util.utcnow())

    @property
    def extra_state_attributes(self) -> dict:
        v = self._value()
        priced = v["kwh"] - v["unpriced_kwh"]
        attrs = {
            "energy_kwh": round(v["kwh"], 3),
            "avg_price": round(v["cost"] / priced, 5) if priced > 0 else None,
            "unpriced_kwh": round(v["unpriced_kwh"], 3),
            "source_sensor": self._manager.entity_id,
            "measurement": self._manager.mode,
        }
        if self._key == SESSION:
            start = v.get("start")
            attrs["started"] = _local(start)
            # Recente sessies, om er een te kunnen verwijderen (delete_ev_session).
            attrs["sessions"] = [
                {
                    "id": s["id"],
                    "start": _local(s["start"]),
                    "end": _local(s["end"]),
                    "kwh": round(s["kwh"], 3),
                    "cost": round(s["cost"], 2),
                }
                for s in self._manager.tracker.sessions()[:20]
            ]
        return attrs

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._manager.add_listener(self.async_write_ha_state))


def ev_entities(manager: EvCostManager) -> list[SensorEntity]:
    return [DynTarNLEvCostSensor(manager, key) for key in ALL_KEYS]
