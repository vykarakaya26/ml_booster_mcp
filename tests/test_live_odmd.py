"""Live contract tests for ODMD: pytest -m live -k odmd (keyless; needs network access)."""

from datetime import date, timedelta

import pytest

from mlbooster.application.dto import FetchRequest
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.odmd import OdmdProvider

pytestmark = pytest.mark.live


@pytest.fixture
def odmd(catalog):
    provider = OdmdProvider(catalog.source("odmd"), http=SecureHttpClient(rps=1))
    yield provider
    provider.close()


def test_odmd_healthcheck(odmd):
    result = odmd.healthcheck()
    assert result.status is HealthStatus.OK, result.detail


def test_odmd_seed_series_return_recent_brands(odmd, catalog):
    """Every catalog series resolves for the latest months, and the brands add up to the market total."""
    codes = [s.code for s in catalog.source("odmd").series]
    df = odmd.fetch(FetchRequest.create("odmd", codes, date.today() - timedelta(days=75)))
    for code in codes:
        part = df[df["series_id"] == code]
        assert (date.today() - part["date"].max()).days < 75, code
        last = part[part["date"] == part["date"].max()].set_index("entity_id")["value"]
        assert len(last) >= 30, code
        assert last.drop("").sum() == last[""], code


def test_odmd_september_2026_matches_press_bulletin(odmd):
    """Totals published in the 2 Oct 2026 press bulletin (units)."""
    codes = ["ODMD.TOPLAM", "ODMD.OTOMOBIL", "ODMD.HAFIF_TICARI", "ODMD.OTOMOBIL.YERLI"]
    df = odmd.fetch(FetchRequest.create("odmd", codes, date(2026, 9, 1), date(2026, 9, 30)))
    v = df.set_index(["series_id", "entity_id"])["value"]
    assert {c: v[(c, "")] for c in codes} == {
        "ODMD.TOPLAM": 83469, "ODMD.OTOMOBIL": 63736, "ODMD.HAFIF_TICARI": 19733, "ODMD.OTOMOBIL.YERLI": 24325,
    }  # fmt: skip
    assert v[("ODMD.TOPLAM", "RENAULT")] == 11430 and v[("ODMD.OTOMOBIL", "TOGG")] == 6692


@pytest.mark.parametrize(
    ("year", "month", "total"),
    [(2015, 1, 34615), (2018, 12, 77706), (2020, 12, 104293), (2025, 1, 68654)],
)
def test_odmd_old_layouts_still_parse(odmd, year, month, total):
    """2015 workbooks, 2020-12 with its extra 'TOPLAM/TOTAL' row; totals match the bulletins' yearly tables."""
    end = date(year, month, 28)
    df = odmd.fetch(FetchRequest.create("odmd", ["ODMD.TOPLAM"], date(year, month, 1), end))
    assert df[df["entity_id"] == ""]["value"].tolist() == [total]


def test_odmd_legacy_xls_month_is_skipped_and_renamed_brand_is_one_entity(odmd):
    df = odmd.fetch(FetchRequest.create("odmd", ["ODMD.TOPLAM"], date(2017, 1, 1), date(2017, 3, 31)))
    assert sorted(set(df["date"])) == [date(2017, 1, 31), date(2017, 3, 31)]  # 2017-02 is an xls file
    df = odmd.fetch(FetchRequest.create("odmd", ["ODMD.TOPLAM"], date(2024, 1, 1), date(2024, 2, 29)))
    ssangyong = df[df["entity_id"] == "KG_MOBILITY_SSANGYONG"]
    assert len(ssangyong) == 2 and set(ssangyong["entity_name"]) == {"KG MOBILITY – SSANGYONG"}
