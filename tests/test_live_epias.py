"""Live contract tests for EPİAŞ: pytest -m live -k epias (requires EPIAS_USERNAME and EPIAS_PASSWORD)."""

import os
from datetime import date, timedelta

import pytest
from dotenv import dotenv_values, find_dotenv

from mlbooster.application.dto import FetchRequest
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.epias import EpiasProvider
from mlbooster.infrastructure.secret_store import EnvSecretProvider

# Read without modifying os.environ: offline tests must not see the credentials.
_DOTENV = dotenv_values(find_dotenv(usecwd=True))
USERNAME = os.environ.get("EPIAS_USERNAME") or _DOTENV.get("EPIAS_USERNAME")
PASSWORD = os.environ.get("EPIAS_PASSWORD") or _DOTENV.get("EPIAS_PASSWORD")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not (USERNAME and PASSWORD), reason="EPIAS_USERNAME / EPIAS_PASSWORD are not set"),
]


@pytest.fixture
def epias(catalog, monkeypatch):
    monkeypatch.setenv("EPIAS_USERNAME", USERNAME)
    monkeypatch.setenv("EPIAS_PASSWORD", PASSWORD)
    provider = EpiasProvider(catalog.source("epias"), http=SecureHttpClient(rps=1), secrets=EnvSecretProvider())
    yield provider
    provider.close()


def test_epias_healthcheck(epias):
    result = epias.healthcheck()
    assert result.status is HealthStatus.OK, result.detail


def test_epias_seed_series_return_recent_days(epias, catalog):
    """Every catalog field still exists; the last requested day is complete (endDate covers the whole day)."""
    codes = [s.code for s in catalog.source("epias").series]
    end = date.today() - timedelta(days=2)
    df = epias.fetch(FetchRequest.create("epias", codes, end - timedelta(days=6), end))
    for code in codes:
        part = df[(df["series_id"] == code) & (df["entity_id"] == "")]
        assert part["date"].max() == end, code
        assert len(part) == 7, code


def test_epias_generation_mix_adds_up_to_total(epias):
    day = date.today() - timedelta(days=3)
    df = epias.fetch(FetchRequest.create("epias", ["EPIAS.URETIM.KAYNAK"], day, day)).set_index("entity_id")["value"]
    assert len(df) >= 8
    assert abs(df.drop("").sum() - df[""]) <= 0.02 * df[""]


def test_epias_ptf_is_plausible(epias):
    """PTF daily averages stay within the market price caps (TL/MWh) of recent years."""
    df = epias.fetch(FetchRequest.create("epias", ["EPIAS.PTF"], date(2026, 1, 1), date(2026, 1, 31)))
    assert len(df) == 31 and df["value"].between(0, 10_000).all()
