"""Nep-hass en nep-config-entry voor setup-, service- en options-tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

from dyntarnl.const import FORECAST_DEFAULTS


@dataclass
class RegEntry:
    entity_id: str
    unique_id: str
    config_entry_id: str


class EntityRegistry:
    def __init__(self, entries=None):
        self.entries = list(entries or [])
        self.removed = []

    def async_remove(self, entity_id):
        self.removed.append(entity_id)
        self.entries = [e for e in self.entries if e.entity_id != entity_id]


class DeviceRegistry:
    def __init__(self, devices=None):
        self.devices = dict(devices or {})  # identifier -> id
        self.removed = []

    def async_get_device(self, identifiers):
        for ident in identifiers:
            if ident in self.devices:
                return SimpleNamespace(id=self.devices[ident])
        return None

    def async_remove_device(self, device_id):
        self.removed.append(device_id)


class Services:
    def __init__(self):
        self.registered = {}

    def has_service(self, domain, name):
        return (domain, name) in self.registered

    def async_register(self, domain, name, handler, schema=None, supports_response=None):
        self.registered[(domain, name)] = SimpleNamespace(handler=handler, schema=schema, supports_response=supports_response)

    def async_remove(self, domain, name):
        self.registered.pop((domain, name), None)


class ConfigEntries:
    def __init__(self, hass):
        self.hass = hass
        self.entries = []
        self.forwarded = []

    def async_entries(self, domain):
        return self.entries

    def async_loaded_entries(self, domain):
        return []

    async def async_forward_entry_setups(self, entry, platforms):
        self.forwarded.append(list(platforms))

    async def async_unload_platforms(self, entry, platforms):
        return True

    def async_update_entry(self, entry, *, options=None, minor_version=None, **kw):
        if options is not None:
            entry.options = options
        if minor_version is not None:
            entry.minor_version = minor_version
        return True


class Hass:
    def __init__(self, registry_entries=None, devices=None):
        self.config_entries = ConfigEntries(self)
        self.services = Services()
        self.entity_registry = EntityRegistry(registry_entries)
        self.device_registry = DeviceRegistry(devices)


@dataclass
class Entry:
    entry_id: str = "entry1"
    data: dict = field(default_factory=lambda: {"supplier": "essent"})
    options: dict = field(default_factory=lambda: dict(FORECAST_DEFAULTS))
    version: int = 1
    minor_version: int = 2
    runtime_data: object = None
    unload: list = field(default_factory=list)
    tasks: list = field(default_factory=list)
    update_listeners: list = field(default_factory=list)

    def async_on_unload(self, func):
        self.unload.append(func)

    def add_update_listener(self, listener):
        self.update_listeners.append(listener)
        return lambda: None

    def async_create_background_task(self, hass, coro, name):
        self.tasks.append(name)
        if asyncio.iscoroutine(coro):
            coro.close()
