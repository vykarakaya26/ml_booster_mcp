"""Live contract tests for BKM: pytest -m live -k bkm (keyless; needs network access)."""

from datetime import date, timedelta

import pytest

from mlbooster.application.dto import FetchRequest
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.bkm import BkmProvider

pytestmark = pytest.mark.live


@pytest.fixture
def bkm(catalog):
    provider = BkmProvider(catalog.source("bkm"), http=SecureHttpClient(rps=1))
    yield provider
    provider.close()


def test_bkm_healthcheck(bkm):
    result = bkm.healthcheck()
    assert result.status is HealthStatus.OK, result.detail


def test_bkm_seed_series_return_full_recent_history(bkm, catalog):
    """Every catalog column still resolves, the history starts in January 2017 and has no gaps."""
    codes = [s.code for s in catalog.source("bkm").series]
    df = bkm.fetch(FetchRequest.create("bkm", codes, date(2017, 1, 1)))
    for code in codes:
        dates = df[df["series_id"] == code]["date"]
        assert dates.min() == date(2017, 1, 31), code
        assert (date.today() - dates.max()).days < 100, code
        assert len(dates) == (dates.max().year - 2017) * 12 + dates.max().month, code
        assert (df[df["series_id"] == code]["value"] > 0).all(), code


def test_bkm_august_2026_matches_general_report(bkm):
    """Values of the 'Seçilen Aya Ait Genel İstatistik' report for August 2026 (cards; million TL)."""
    codes = ["BKM.KART.KREDI", "BKM.KART.BANKA", "BKM.KK.YURTICI.TUTAR", "BKM.BK.YABANCI.TUTAR", "BKM.INTERNET.TUTAR"]
    df = bkm.fetch(FetchRequest.create("bkm", codes, date(2026, 8, 1), date(2026, 8, 31)))
    values = df.set_index("series_id")["value"].to_dict()
    assert values == pytest.approx(
        {
            "BKM.KART.KREDI": 153426877, "BKM.KART.BANKA": 224453001, "BKM.KK.YURTICI.TUTAR": 2531037.68,
            "BKM.BK.YABANCI.TUTAR": 73678.52, "BKM.INTERNET.TUTAR": 923865.73,
        }
    )  # fmt: skip


def test_bkm_sector_series_are_parts_of_domestic_shopping(bkm):
    """Sector figures are domestic and foreign card shopping in Türkiye: each is a small share of the total."""
    start = date.today() - timedelta(days=150)
    df = bkm.fetch(FetchRequest.create("bkm", ["BKM.SEKTOR.MARKET.TUTAR", "BKM.KK.YURTICI.ALISVERIS.TUTAR"], start))
    last = df[df["date"] == df["date"].max()].set_index("series_id")["value"]
    assert 0.1 < last["BKM.SEKTOR.MARKET.TUTAR"] / last["BKM.KK.YURTICI.ALISVERIS.TUTAR"] < 0.5
