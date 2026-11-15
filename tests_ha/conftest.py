"""Tests in een ECHTE Home Assistant (pytest-homeassistant-custom-component).

Los van tests/ (die HA stubben): draai met  pytest -c tests_ha/pytest.ini tests_ha
op Linux/macOS/WSL (HA draait niet native op Windows).
"""

import sys
from pathlib import Path

import custom_components

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# De plugin brengt een eigen custom_components-pakket (testing_config) mee dat al geladen
# is; voeg de map uit deze repo toe, zodat HA custom_components.dyntarnl vindt.
_ours = str(REPO / "custom_components")
if _ours not in custom_components.__path__:
    custom_components.__path__.append(_ours)
