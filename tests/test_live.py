"""Canlı contract testleri: pytest -m live (EVDS_API_KEY gerekir)."""

import os
from datetime import date, timedelta

import pytest

from mlbooster.application.dto import FetchRequest
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.evds3 import Evds3Provider
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from dotenv import dotenv_values, find_dotenv

# os.environ'u değiştirmeden oku: offline testler anahtarı görmemeli.
API_KEY = os.environ.get("EVDS_API_KEY") or dotenv_values(find_dotenv(usecwd=True)).get("EVDS_API_KEY")

pytestmark = [pytest.mark.live, pytest.mark.skipif(not API_KEY, reason="EVDS_API_KEY yok")]


@pytest.fixture
def evds(catalog, monkeypatch):
    monkeypatch.setenv("EVDS_API_KEY", API_KEY)
    provider = Evds3Provider(catalog.source("evds3"), http=SecureHttpClient(), secrets=EnvSecretProvider())
    yield provider
    provider.close()


def test_healthcheck(evds):
    result = evds.healthcheck()
    assert result.status is HealthStatus.OK, result.detail


def test_seed_series_return_data(evds, catalog):
    for seed in catalog.source("evds3").series:
        df = evds.fetch(FetchRequest.create("evds3", [seed.code], date.today() - timedelta(days=800)))
        assert not df.empty, seed.code
        assert set(df["freq"]) == {seed.freq.value}, seed.code


@pytest.mark.parametrize("seed_index", range(6))
def test_seed_codes_listed_in_their_datagroup(evds, catalog, seed_index):
    """Katalogdaki kod EVDS'nin güncel /serieList'inde mi? (kod değişikliklerini yakalar)"""
    seed = catalog.source("evds3").series[seed_index]
    listed = evds.list_series(seed.datagroup)
    row = listed[listed["series_id"] == seed.code]
    assert not row.empty, f"{seed.code} {seed.datagroup} içinde yok"
    assert row.iloc[0]["freq"] in {seed.freq.value, "D"}  # EVDS kur gruplarını 'GÜNLÜK' etiketler
