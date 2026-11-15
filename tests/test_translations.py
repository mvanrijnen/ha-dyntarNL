"""Vertalingen: dezelfde regels die hassfest in CI controleert."""

import json
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "dyntarnl"
FILES = [BASE / "strings.json", *sorted((BASE / "translations").glob("*.json"))]


def _strings(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(value, f"{path}.{key}" if path else key)
    elif isinstance(node, str):
        yield path, node


@pytest.mark.parametrize("file", FILES, ids=lambda f: f.name)
def test_no_urls_in_translations(file):
    """hassfest: geen URL's in vertalingen, gebruik description_placeholders."""
    data = json.loads(file.read_text(encoding="utf-8"))
    bad = [path for path, text in _strings(data) if "http://" in text or "https://" in text]
    assert bad == []


def test_translations_have_the_same_keys():
    keys = {f.name: {p for p, _ in _strings(json.loads(f.read_text(encoding="utf-8")))} for f in FILES}
    reference = keys["strings.json"]
    for name, found in keys.items():
        assert found == reference, f"{name}: {sorted(found ^ reference)}"
