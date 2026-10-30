from __future__ import annotations

import pytest

from mlbooster.adapters.base import HttpClient
from mlbooster.adapters.evds3 import Evds3Adapter
from mlbooster.catalog import load_catalog
from mlbooster.store.bronze import BronzeStore

TEST_KEY = "test-key-not-real"


@pytest.fixture
def catalog():
    return load_catalog()


@pytest.fixture
def evds(catalog, monkeypatch, tmp_path):
    monkeypatch.setenv("EVDS_API_KEY", TEST_KEY)
    http = HttpClient(rps=None, max_retries=2, sleep=lambda _: None)
    adapter = Evds3Adapter(catalog.source("evds3"), http=http, bronze=BronzeStore(tmp_path / "bronze"))
    yield adapter
    adapter.close()
