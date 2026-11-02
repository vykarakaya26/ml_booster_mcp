"""Live contract tests for BDDK: pytest -m live -k bddk (keyless; needs network access)."""

from datetime import date, timedelta

import pytest

from mlbooster.application.dto import FetchRequest
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.bddk import BddkProvider

pytestmark = pytest.mark.live


@pytest.fixture
def bddk(catalog):
    provider = BddkProvider(catalog.source("bddk"), http=SecureHttpClient(rps=1))
    yield provider
    provider.close()


def test_bddk_healthcheck(bddk):
    result = bddk.healthcheck()
    assert result.status is HealthStatus.OK, result.detail


def test_bddk_seed_series_return_recent_groups(bddk, catalog):
    """Every catalog row key and group still resolves, and the groups add up to the series total."""
    codes = [s.code for s in catalog.source("bddk").series]
    df = bddk.fetch(FetchRequest.create("bddk", codes, date.today() - timedelta(days=100)))
    for seed in catalog.source("bddk").series:
        part = df[df["series_id"] == seed.code]
        assert (date.today() - part["date"].max()).days < 100, seed.code
        last = part[part["date"] == part["date"].max()].set_index("entity_id")["value"]
        assert len(last) >= 4, seed.code
        assert abs(last.drop("").sum() - last[""]) <= max(5.0, 1e-6 * last[""]), seed.code


@pytest.mark.parametrize("year", [2002, 2017, 2018])
def test_bddk_old_row_labels_still_resolve(bddk, year):
    """Row labels changed over time (2002 formulas in the label, TFRS 9 in 2018); the keys must still match."""
    df = bddk.fetch(FetchRequest.create("bddk", ["BDDK.AKTIF", "BDDK.KREDI", "BDDK.MEVDUAT"], date(year, 12, 1), date(year, 12, 31)))
    total = df[df["entity_id"] == ""].set_index("series_id")["value"]
    assert set(total.index) == {"BDDK.AKTIF", "BDDK.KREDI", "BDDK.MEVDUAT"}
    assert total["BDDK.AKTIF"] > total["BDDK.MEVDUAT"] > 0 and total["BDDK.AKTIF"] > total["BDDK.KREDI"] > 0


def test_bddk_september_2026_matches_news_bulletin(bddk):
    """Values published in the 30 Oct 2026 news bulletin (million TL)."""
    df = bddk.fetch(FetchRequest.create("bddk", ["BDDK.AKTIF", "BDDK.KREDI", "BDDK.MEVDUAT", "BDDK.OZKAYNAK"], date(2026, 9, 1), date(2026, 9, 30)))
    total = df[df["entity_id"] == ""].set_index("series_id")["value"].to_dict()
    assert total == {"BDDK.AKTIF": 56982049, "BDDK.KREDI": 28765658, "BDDK.MEVDUAT": 32768446, "BDDK.OZKAYNAK": 4835574}
