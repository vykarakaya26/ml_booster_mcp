"""Live contract tests for TSB: pytest -m live -k tsb (keyless; needs network access)."""

from datetime import date, timedelta

import pytest

from mlbooster.application.dto import FetchRequest
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.tsb import TsbProvider

pytestmark = pytest.mark.live


@pytest.fixture
def tsb(catalog):
    provider = TsbProvider(catalog.source("tsb"), http=SecureHttpClient(rps=2))
    yield provider
    provider.close()


def test_tsb_healthcheck(tsb):
    result = tsb.healthcheck()
    assert result.status is HealthStatus.OK, result.detail


def test_tsb_seed_series_return_recent_companies(tsb, catalog):
    """Every catalog sheet still resolves, and the companies add up to the sector total."""
    codes = [s.code for s in catalog.source("tsb").series]
    df = tsb.fetch(FetchRequest.create("tsb", codes, date.today() - timedelta(days=80)))
    for seed in catalog.source("tsb").series:
        part = df[df["series_id"] == seed.code]
        assert (date.today() - part["date"].max()).days < 80, seed.code
        last = part[part["date"] == part["date"].max()].set_index("entity_id")["value"]
        assert len(last) >= 10, seed.code
        assert abs(last.drop("").sum() - last[""]) <= 0.005 * abs(last[""]) + 1, seed.code


def test_tsb_september_2026_matches_the_published_file(tsb):
    """Cumulative Jan–Sep 2026 premiums (TL) in the file published on 19 Oct 2026; small revisions are tolerated."""
    codes = ["TSB.PRIM.TOPLAM.YTD", "TSB.PRIM.HAYATDISI.YTD", "TSB.PRIM.TRAFIK.YTD"]
    df = tsb.fetch(FetchRequest.create("tsb", codes, date(2026, 9, 1), date(2026, 9, 30)))
    total = df[df["entity_id"] == ""].set_index("series_id")["value"]
    assert total["TSB.PRIM.TOPLAM.YTD"] == pytest.approx(1_097_137_374_182, rel=0.01)
    assert total["TSB.PRIM.HAYATDISI.YTD"] == pytest.approx(917_814_570_773, rel=0.01)
    assert total["TSB.PRIM.TRAFIK.YTD"] == pytest.approx(243_861_700_983, rel=0.01)
    turkiye = df[(df["series_id"] == "TSB.PRIM.TOPLAM.YTD") & (df["entity_id"] == "1020")].iloc[0]
    assert turkiye["entity_name"] == "Türkiye Sigorta AŞ" and turkiye["value"] == pytest.approx(134_557_275_439, rel=0.01)


@pytest.mark.parametrize("year", [2015, 2020])
def test_tsb_old_files_still_parse_and_monthly_premiums_add_up_to_the_year(tsb, year):
    df = tsb.fetch(FetchRequest.create("tsb", ["TSB.PRIM.TOPLAM", "TSB.PRIM.TOPLAM.YTD"], date(year, 1, 1), date(year, 12, 31)))
    total = df[df["entity_id"] == ""]
    monthly = total[total["series_id"] == "TSB.PRIM.TOPLAM"]
    december = total[(total["series_id"] == "TSB.PRIM.TOPLAM.YTD") & (total["date"] == date(year, 12, 31))]["value"].iloc[0]
    assert len(monthly) == 12 and (monthly["value"] > 0).all()
    assert monthly["value"].sum() == pytest.approx(december)


def test_tsb_known_gap_2016_04(tsb):
    """The April 2016 file is listed but missing on the server (404): the month is skipped, not an error."""
    df = tsb.fetch(FetchRequest.create("tsb", ["TSB.PRIM.TOPLAM.YTD"], date(2016, 3, 1), date(2016, 5, 31)))
    assert sorted(d.month for d in df.loc[df["entity_id"] == "", "date"]) == [3, 5]
