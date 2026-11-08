"""Tests der HA-Integration. Laufen nur in einer Umgebung mit Home Assistant:
pip install pytest-homeassistant-custom-component"""
import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

QUELLE = Path(__file__).parents[2] / "kamin_abbrand" / "integration" / "kamin_abbrand"


@pytest.fixture(scope="session", autouse=True)
def custom_components_bereit(tmp_path_factory):
    wurzel = tmp_path_factory.mktemp("ha")
    ziel = wurzel / "custom_components"
    ziel.mkdir()
    (ziel / "__init__.py").write_text("")
    shutil.copytree(QUELLE, ziel / "kamin_abbrand")
    sys.path.insert(0, str(wurzel))
    sys.modules.pop("custom_components", None)
    import custom_components
    custom_components.__path__ = [str(ziel)]
    yield


@pytest.fixture(autouse=True)
def eigene_integrationen(enable_custom_integrations):
    yield
