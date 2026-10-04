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


def _install_voluptuous_stub() -> None:
    """Minimale voluptuous: genoeg voor schema's bouwen en defaults invullen."""
    vol = _mod("voluptuous")

    class _Marker(str):
        def __new__(cls, key, default=None, **kw):
            obj = super().__new__(cls, key)
            obj.default = default
            return obj

    vol.Required = type("Required", (_Marker,), {})
    vol.Optional = type("Optional", (_Marker,), {})
    vol.Invalid = type("Invalid", (Exception,), {})

    class Schema:
        def __init__(self, schema):
            self.schema = schema

        def __call__(self, data):
            out = dict(data)
            for key in self.schema:
                if key not in out and getattr(key, "default", None) is not None:
                    out[str(key)] = key.default
            return out

    vol.Schema = Schema
    vol.In = lambda options: options
    vol.Coerce = lambda typ: typ
    vol.Range = lambda **k: k
    vol.All = lambda *validators: validators


def _install_ha_stubs() -> None:
    aiohttp = _mod("aiohttp")
    aiohttp.ClientError = type("ClientError", (Exception,), {})
    aiohttp.ClientTimeout = lambda **k: None
    aiohttp.ClientSession = object

    _install_voluptuous_stub()

    _mod("homeassistant")
    ce = _mod("homeassistant.config_entries")
    ce.ConfigEntry = object
    ce.ConfigFlowResult = dict

    class _FlowBase:
        def async_show_form(self, **kw):
            return {"type": "form", "errors": None, **kw}

        def async_create_entry(self, **kw):
            return {"type": "create_entry", **kw}

        def async_show_menu(self, **kw):
            return {"type": "menu", **kw}

    class ConfigFlow(_FlowBase):
        def __init_subclass__(cls, domain=None, **kw):
            super().__init_subclass__(**kw)

    ce.ConfigFlow = ConfigFlow
    ce.OptionsFlow = type("OptionsFlow", (_FlowBase,), {})

    exc = _mod("homeassistant.exceptions")
    exc.HomeAssistantError = type("HomeAssistantError", (Exception,), {})
    exc.ServiceValidationError = type("ServiceValidationError", (exc.HomeAssistantError,), {})

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
    core.ServiceCall = object
    core.callback = lambda f: f
    core.Event = object
    core.ServiceResponse = dict
    core.SupportsResponse = type("SupportsResponse", (), {"ONLY": "only", "OPTIONAL": "optional"})

    helpers = _mod("homeassistant.helpers")
    _mod("homeassistant.helpers.aiohttp_client").async_get_clientsession = lambda hass: None
    event = _mod("homeassistant.helpers.event")
    event.async_track_time_change = lambda *a, **k: None
    event.async_track_state_change_event = lambda *a, **k: None

    cv = _mod("homeassistant.helpers.config_validation")
    cv.boolean = bool
    cv.string = str
    helpers.config_validation = cv

    storage = _mod("homeassistant.helpers.storage")

    class Store:
        """In-memory Store: per sleutel één dict, gedeeld over instanties."""

        data: dict = {}

        def __init__(self, hass, version, key, **kw):
            self.key = key

        async def async_load(self):
            return json.loads(json.dumps(Store.data[self.key])) if self.key in Store.data else None

        def async_delay_save(self, data_func, delay=0):
            Store.data[self.key] = json.loads(json.dumps(data_func()))

        async def async_save(self, data):
            Store.data[self.key] = data

        async def async_remove(self):
            Store.data.pop(self.key, None)

    storage.Store = Store

    selector = _mod("homeassistant.helpers.selector")
    for name in (
        "BooleanSelector", "NumberSelector", "NumberSelectorConfig", "SelectSelector",
        "SelectSelectorConfig", "TextSelector", "TextSelectorConfig", "EntitySelector",
    ):
        setattr(selector, name, lambda *a, _n=name, **k: (_n, a, k))
    selector.SelectOptionDict = dict

    def _strict_config(**k):
        # Zoals HA: een selector-config met None-waarden is ongeldig.
        bad = [key for key, value in k.items() if value is None]
        if bad:
            raise ValueError(f"selector-config met None: {bad}")
        return dict(k)

    selector.NumberSelectorConfig = _strict_config
    selector.SelectSelectorConfig = _strict_config
    selector.TextSelectorConfig = _strict_config
    selector.EntitySelectorConfig = _strict_config
    selector.NumberSelectorMode = type("NumberSelectorMode", (), {"BOX": "box", "SLIDER": "slider"})
    selector.SelectSelectorMode = type("SelectSelectorMode", (), {"DROPDOWN": "dropdown", "LIST": "list"})
    selector.TextSelectorType = type("TextSelectorType", (), {"URL": "url", "PASSWORD": "password", "TEXT": "text"})

    uc = _mod("homeassistant.helpers.update_coordinator")
    sub = type("_Sub", (), {"__class_getitem__": classmethod(lambda cls, item: cls)})

    class DataUpdateCoordinator(sub):
        def __init__(self, hass=None, logger=None, *, config_entry=None, name=None, update_interval=None, **kw):
            self.hass = hass
            self.config_entry = config_entry
            self.data = None
            self._listeners = []

        def async_add_listener(self, update_callback, context=None):
            self._listeners.append(update_callback)
            return lambda: self._listeners.remove(update_callback)

        def async_update_listeners(self):
            for listener in list(self._listeners):
                listener()

        def async_set_updated_data(self, data):
            self.data = data
            self.async_update_listeners()

        async def async_config_entry_first_refresh(self):
            self.data = await self._async_update_data()

        async def async_request_refresh(self):
            self.async_set_updated_data(await self._async_update_data())

    uc.DataUpdateCoordinator = DataUpdateCoordinator
    uc.UpdateFailed = type("UpdateFailed", (Exception,), {})
    uc.CoordinatorEntity = type(
        "CoordinatorEntity", (sub,), {"__init__": lambda s, coord: setattr(s, "coordinator", coord)}
    )

    dr = _mod("homeassistant.helpers.device_registry")
    dr.DeviceEntryType = type("DeviceEntryType", (), {"SERVICE": "service"})
    dr.DeviceInfo = lambda **k: k
    dr.async_get = lambda hass: hass.device_registry
    helpers.device_registry = dr
    er = _mod("homeassistant.helpers.entity_registry")
    er.async_get = lambda hass: hass.entity_registry
    er.async_entries_for_config_entry = lambda reg, entry_id: [
        e for e in reg.entries if e.config_entry_id == entry_id
    ]
    helpers.entity_registry = er
    _mod("homeassistant.helpers.entity_platform").AddEntitiesCallback = object

    util = _mod("homeassistant.util")
    dt = _mod("homeassistant.util.dt")
    dt._now = DEFAULT_NOW
    dt.UTC = timezone.utc
    dt.DEFAULT_TIME_ZONE = AMS
    dt.parse_datetime = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
    dt.now = lambda: dt._now
    dt.utcnow = lambda: dt._now.astimezone(timezone.utc)
    dt.as_local = lambda d: d.astimezone(AMS)
    dt.as_utc = lambda d: d.astimezone(timezone.utc)
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
        entity_category: object = None

    sensor.SensorEntityDescription = SensorEntityDescription
    sensor.SensorEntity = object
    sensor.SensorStateClass = type("SensorStateClass", (), {"MEASUREMENT": "measurement", "TOTAL": "total"})
    sensor.SensorDeviceClass = type("SensorDeviceClass", (), {"TIMESTAMP": "timestamp", "MONETARY": "monetary"})

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


@pytest.fixture(autouse=True)
def _clean_store():
    """Elke test begint met een lege (gestubde) HA Store."""
    sys.modules["homeassistant.helpers.storage"].Store.data.clear()
    yield


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
