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
