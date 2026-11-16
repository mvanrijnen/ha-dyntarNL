"""Tests in een ECHTE Home Assistant (pytest-homeassistant-custom-component).

Los van tests/ (die HA stubben): draai met  pytest -c tests_ha/pytest.ini tests_ha
op Linux/macOS/WSL (HA draait niet native op Windows).
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# Afhankelijk van de pluginversie is custom_components al geladen (het testing_config-
# pakket van de plugin) of nog niet (dan vinden we die van deze repo). Zorg in beide
# gevallen dat de map uit deze repo erin zit, zodat HA custom_components.dyntarnl vindt.
import custom_components  # noqa: E402

_ours = str(REPO / "custom_components")
if _ours not in list(custom_components.__path__):
    custom_components.__path__.append(_ours)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def no_ha_usage_warnings(caplog):
    """Faal als HA meldt dat DynTarNL iets verouderds/verkeerds doet ('Detected that custom
    integration ...'). Zulke meldingen worden later vaak fouten (bijv. HA 2026.12)."""
    yield
    bad = [
        r.getMessage()
        for r in caplog.records
        if "custom integration 'dyntarnl'" in r.getMessage() or "integration 'dyntarnl'" in r.getMessage()
    ]
    assert not bad, bad
