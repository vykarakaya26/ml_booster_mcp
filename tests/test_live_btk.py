"""Live contract tests for BTK: pytest -m live -k btk (keyless; needs network access, downloads 40-45 MB reports)."""

from datetime import date

import pytest

from mlbooster.application.dto import FetchRequest
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.btk import BtkProvider
from mlbooster.infrastructure.storage.raw_store import RawStore

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def raw_store(tmp_path_factory):
    return RawStore(tmp_path_factory.mktemp("bronze"))  # shared so that every report is downloaded once


@pytest.fixture
def btk(catalog, raw_store):
    provider = BtkProvider(catalog.source("btk"), http=SecureHttpClient(rps=1, timeout=120), raw_store=raw_store)
    yield provider
    provider.close()


def test_btk_announcement_page_lists_quarterly_reports(btk):
    reports = btk.reports()
    assert (date.today() - reports[0].period).days < 300
    assert {date(2019, 12, 31), date(2023, 6, 30), date(2026, 6, 30)} <= {r.period for r in reports}


def test_btk_healthcheck(btk):
    result = btk.healthcheck()
    assert result.status is HealthStatus.OK, result.detail


def test_btk_2026q2_matches_the_report(btk, catalog):
    """Values printed in the 2026-Q2 report (published 7 Oct 2026); every catalog series resolves."""
    codes = [s.code for s in catalog.source("btk").series]
    df = btk.fetch(FetchRequest.create("btk", codes, date(2026, 4, 1), date(2026, 6, 30)))
    v = df.set_index(["series_id", "entity_id"])["value"]
    assert v[("BTK.GELIR", "")] == 150_528_370_460 and v[("BTK.GELIR", "TURKCELL")] == 47_507_116_037
    assert v[("BTK.MOBIL.ABONE.PAY", "TURKCELL")] == 39.1 and v[("BTK.MOBIL.ABONE.PAY", "TT_MOBIL")] == 32.0
    assert v[("BTK.SGB.ABONE.PAY", "TTNET")] == 53.07 and v[("BTK.SGB.GELIR.PAY", "SUPERONLINE")] == 18.50
    assert v[("BTK.INTERNET.ABONE", "")] == 100_846_626 and v[("BTK.FIBER.ABONE", "")] == 10_698_289
    for code in codes:
        part = df[(df["series_id"] == code) & (df["entity_id"] != "")]
        if not part.empty and set(part["unit"]) == {"percent"}:
            assert abs(part["value"].sum() - 100) <= 0.5, code


@pytest.mark.parametrize(("year", "quarter", "shares"), [(2019, 4, (40.5, 31.1, 28.4)), (2023, 2, (41.2, 30.8, 28.0))])
def test_btk_older_reports_still_parse(btk, year, quarter, shares):
    end = date(year, 3 * quarter, 30 if quarter in (2, 3) else 31)
    df = btk.fetch(FetchRequest.create("btk", ["BTK.GELIR", "BTK.MOBIL.ABONE.PAY"], date(year, 3 * quarter - 2, 1), end))
    mobile = df[(df["series_id"] == "BTK.MOBIL.ABONE.PAY")].set_index("entity_id")["value"]
    assert (mobile["TURKCELL"], mobile["VODAFONE"], mobile["TT_MOBIL"]) == shares
    revenue = df[(df["series_id"] == "BTK.GELIR")].set_index("entity_id")["value"]
    assert abs(revenue.drop("").sum() - revenue[""]) <= 2
