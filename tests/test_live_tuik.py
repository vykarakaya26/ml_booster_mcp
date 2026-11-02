"""Live contract tests for TÜİK: pytest -m live -k tuik (keyless; needs network access)."""

from datetime import date, timedelta

import pytest

from mlbooster.application.dto import FetchRequest
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.tuik import TuikProvider

pytestmark = pytest.mark.live


@pytest.fixture
def tuik(catalog):
    provider = TuikProvider(catalog.source("tuik"), http=SecureHttpClient(rps=1))
    yield provider
    provider.close()


def test_tuik_healthcheck(tuik):
    result = tuik.healthcheck()
    assert result.status is HealthStatus.OK, result.detail


def test_tuik_seed_series_return_data(tuik, catalog):
    """Every catalog key still matches its dataset structure and returns recent observations."""
    start = date.today() - timedelta(days=500)
    for seed in catalog.source("tuik").series:
        df = tuik.fetch(FetchRequest.create("tuik", [seed.code], start))
        assert len(df) >= 6, seed.code
        assert set(df["freq"]) == {seed.freq.value}, seed.code
        assert (date.today() - df["date"].max()).days < 120, seed.code


def test_tuik_cpi_rates_are_consistent_with_index(tuik):
    """Monthly % change published by TÜİK equals the change in the 2025=100 index (rounded to 2 decimals)."""
    df = tuik.fetch(FetchRequest.create("tuik", ["TUIK.TUFE.ENDEKS", "TUIK.TUFE.AYLIK"], date(2026, 1, 1)))
    wide = df.pivot(index="date", columns="series_id", values="value").dropna()
    implied = (wide["TUIK.TUFE.ENDEKS"].pct_change() * 100).round(2).iloc[1:]
    assert (implied - wide["TUIK.TUFE.AYLIK"].iloc[1:]).abs().max() <= 0.011
    assert 95 < wide["TUIK.TUFE.ENDEKS"].iloc[0] < 130  # base 2025=100
