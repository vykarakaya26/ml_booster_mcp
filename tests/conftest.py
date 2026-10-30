from __future__ import annotations

from pathlib import Path

import pytest

from mlbooster.infrastructure.catalog_yaml import YamlCatalogRepository
from mlbooster.infrastructure.settings import PACKAGE_DIR

TEST_KEY = "test-key-not-real-0123456789"


@pytest.fixture
def catalog_repo() -> YamlCatalogRepository:
    return YamlCatalogRepository(PACKAGE_DIR / "catalog_data")


@pytest.fixture
def catalog(catalog_repo):
    return catalog_repo.load()


@pytest.fixture
def api_key(monkeypatch) -> str:
    monkeypatch.setenv("EVDS_API_KEY", TEST_KEY)
    return TEST_KEY


@pytest.fixture
def data_dir(tmp_path) -> Path:
    return tmp_path / "data"


@pytest.fixture(autouse=True)
def _no_dotenv(request, monkeypatch):
    """Offline testler geliştiricinin .env'indeki gerçek anahtarı görmesin (canlı istek atılmasın)."""
    if request.node.get_closest_marker("live"):
        return
    monkeypatch.setattr("mlbooster.infrastructure.settings.load_dotenv", lambda *a, **k: False)
    home = request.getfixturevalue("tmp_path") / "mlbooster-home"
    monkeypatch.setenv("MLBOOSTER_HOME", str(home))  # override'lar gerçek ~/.mlbooster'a yazılmasın
    monkeypatch.delenv("MLBOOSTER_OVERRIDES", raising=False)
    monkeypatch.delenv("EVDS_API_KEY", raising=False)
