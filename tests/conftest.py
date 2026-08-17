"""Test-opzet voor DynTarNL: stub Home Assistant zodat de logica-tests draaien
zonder een volledige HA-installatie. Injecteert minimale HA-modules in sys.modules
vóór de integratie wordt geïmporteerd, en biedt fixture-helpers.
"""

from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

AMS = timezone(timedelta(hours=2))
FIXTURES = Path(__file__).parent / "fixtures"
# Vast referentiemoment dat past bij de vastgelegde fixtures (2026-08-17).
DEFAULT_NOW = datetime(2026, 8, 17, 14, 0, tzinfo=AMS)


def _mod(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = []  # maak het een package zodat submodules importeerbaar zijn
    sys.modules[name] = module
    return module


def _install_ha_stubs() -> None:
    aiohttp = _mod("aiohttp")
    aiohttp.ClientError = type("ClientError", (Exception,), {})
    aiohttp.ClientTimeout = lambda **k: None
    aiohttp.ClientSession = object

    _mod("homeassistant")
    _mod("homeassistant.config_entries").ConfigEntry = object

    const = _mod("homeassistant.const")
    const.CURRENCY_EURO = "€"
    const.UnitOfTime = type("UnitOfTime", (), {"HOURS": "h"})
    const.Platform = type(
        "Platform",
        (),
        {"SENSOR": "sensor", "BINARY_SENSOR": "binary_sensor", "BUTTON": "button"},
    )
    const.EntityCategory = type("EntityCategory", (), {"CONFIG": "config", "DIAGNOSTIC": "diagnostic"})

    core = _mod("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda f: f

    _mod("homeassistant.helpers")
    _mod("homeassistant.helpers.aiohttp_client").async_get_clientsession = lambda hass: None
    _mod("homeassistant.helpers.event").async_track_time_change = lambda *a, **k: None

    uc = _mod("homeassistant.helpers.update_coordinator")
    sub = type("_Sub", (), {"__class_getitem__": classmethod(lambda cls, item: cls)})
    uc.DataUpdateCoordinator = type("DataUpdateCoordinator", (sub,), {"__init__": lambda s, *a, **k: None})
    uc.UpdateFailed = type("UpdateFailed", (Exception,), {})
    uc.CoordinatorEntity = type(
        "CoordinatorEntity", (sub,), {"__init__": lambda s, coord: setattr(s, "coordinator", coord)}
    )

    dr = _mod("homeassistant.helpers.device_registry")
    dr.DeviceEntryType = type("DeviceEntryType", (), {"SERVICE": "service"})
    dr.DeviceInfo = lambda **k: k
    _mod("homeassistant.helpers.entity_platform").AddEntitiesCallback = object

    util = _mod("homeassistant.util")
    dt = _mod("homeassistant.util.dt")
    dt._now = DEFAULT_NOW
    dt.DEFAULT_TIME_ZONE = AMS
    dt.parse_datetime = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
    dt.now = lambda: dt._now
    dt.as_local = lambda d: d.astimezone(AMS)
    util.dt = dt

    _mod("homeassistant.components")
    sensor = _mod("homeassistant.components.sensor")

    @dataclass(frozen=True, kw_only=True)
    class SensorEntityDescription:
        key: str
        name: str | None = None
        icon: str | None = None
        device_class: object = None
        state_class: object = None
        native_unit_of_measurement: str | None = None
        suggested_display_precision: int | None = None

    sensor.SensorEntityDescription = SensorEntityDescription
    sensor.SensorEntity = object
    sensor.SensorStateClass = type("SensorStateClass", (), {"MEASUREMENT": "measurement"})

    binary = _mod("homeassistant.components.binary_sensor")

    @dataclass(frozen=True, kw_only=True)
    class BinarySensorEntityDescription:
        key: str
        name: str | None = None
        icon: str | None = None
        device_class: object = None

    binary.BinarySensorEntityDescription = BinarySensorEntityDescription
    binary.BinarySensorEntity = object


_install_ha_stubs()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))


@pytest.fixture
def load_fixture():
    def _load(name: str) -> dict:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return _load


@pytest.fixture
def now() -> datetime:
    return DEFAULT_NOW


@pytest.fixture
def at_time():
    """Zet het (gestubde) 'nu' voor een test; herstelt automatisch."""
    dt = sys.modules["homeassistant.util.dt"]
    original = dt._now

    def _set(value: datetime) -> None:
        dt._now = value

    yield _set
    dt._now = original
