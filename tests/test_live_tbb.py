"""Live contract tests for TBB: pytest -m live -k tbb (keyless; needs network access)."""

from datetime import date, timedelta

import pytest

from mlbooster.application.dto import FetchRequest
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.tbb import TbbProvider

pytestmark = pytest.mark.live


@pytest.fixture
def tbb(catalog):
    provider = TbbProvider(catalog.source("tbb"), http=SecureHttpClient(rps=1))
    yield provider
    provider.close()


def test_tbb_healthcheck(tbb):
    result = tbb.healthcheck()
    assert result.status is HealthStatus.OK, result.detail


def test_tbb_seed_series_return_recent_banks_that_add_up(tbb, catalog):
    """Every catalog item key still resolves, and the banks add up to the system total (closed banks aside)."""
    codes = [s.code for s in catalog.source("tbb").series]
    df = tbb.fetch(FetchRequest.create("tbb", codes, date.today() - timedelta(days=400)))
    for seed in catalog.source("tbb").series:
        part = df[df["series_id"] == seed.code]
        assert (date.today() - part["date"].max()).days < 250, seed.code
        last = part[part["date"] == part["date"].max()].set_index("entity_id")["value"]
        assert len(last) >= 40, seed.code
        assert abs(last.drop("").sum() - last[""]) <= 0.001 * abs(last[""]), seed.code


@pytest.mark.parametrize("year", [2002, 2017, 2018])
def test_tbb_older_statement_keys_still_resolve(tbb, year):
    """SOLO keys (12/2002-12/2017) and TFRS9-SOLO keys (3/2018-..) are chained per series."""
    codes = ["TBB.AKTIF", "TBB.KREDI", "TBB.MEVDUAT", "TBB.OZKAYNAK"]
    df = tbb.fetch(FetchRequest.create("tbb", codes, date(year, 12, 1), date(year, 12, 31)))
    total = df[df["entity_id"] == ""].set_index("series_id")["value"]
    assert set(total.index) == set(codes)
    assert total["TBB.AKTIF"] > total["TBB.MEVDUAT"] > total["TBB.OZKAYNAK"] > 0 and total["TBB.AKTIF"] > total["TBB.KREDI"] > 0
    banks = df[(df["series_id"] == "TBB.AKTIF") & (df["entity_id"] != "")]["value"].sum()
    assert 0.85 * total["TBB.AKTIF"] < banks <= total["TBB.AKTIF"] * 1.001  # closed banks are not listed by name


def test_tbb_june_2026_values(tbb):
    """Values shown by the Mali Tablolar report page for June 2026 (million TL)."""
    df = tbb.fetch(FetchRequest.create("tbb", ["TBB.AKTIF"], date(2026, 6, 1), date(2026, 6, 30)))
    values = df.set_index("entity_id")["value"]
    assert values[""] == 47776632 and values["3"] == 9076507 and values["8"] == 3727680
