"""Tests voor de leverancier-registry."""

from dyntarnl.const import (
    PLATFORM_CUSTOM,
    PLATFORM_EON_APP,
    SUPPLIERS,
    supplier_by_key,
)


def test_lookup_known_and_unknown():
    assert supplier_by_key("essent").platform == PLATFORM_EON_APP
    assert supplier_by_key("custom").platform == PLATFORM_CUSTOM
    assert supplier_by_key("bestaat-niet") is None


def test_all_suppliers_valid():
    for s in SUPPLIERS:
        assert s.key and s.name and s.platform


def test_supplier_keys_unique():
    keys = [s.key for s in SUPPLIERS]
    assert len(keys) == len(set(keys))


def test_eon_app_suppliers_have_host():
    for s in SUPPLIERS:
        if s.platform == PLATFORM_EON_APP:
            assert s.host, f"{s.key} mist host"
