"""Canlı contract testleri: pytest -m live (EVDS_API_KEY gerekir)."""

import os
from datetime import date, timedelta

import pytest

from mlbooster.application.dto import FetchRequest
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.evds3 import Evds3Provider
from mlbooster.infrastructure.secret_store import EnvSecretProvider

pytestmark = [pytest.mark.live, pytest.mark.skipif(not os.environ.get("EVDS_API_KEY"), reason="EVDS_API_KEY yok")]


@pytest.fixture
def evds(catalog):
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


def test_serielist_schema(evds):
    assert "TP.DK.USD.A.YTL" in set(evds.list_series("bie_dkdovizgn")["series_id"])
