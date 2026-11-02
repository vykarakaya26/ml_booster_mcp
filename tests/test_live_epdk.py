"""Live contract tests for EPDK: pytest -m live -k epdk (keyless; needs network access, downloads ~10 report files)."""

from datetime import date, timedelta

import pytest

from mlbooster.application.dto import FetchRequest
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.epdk import EpdkProvider

pytestmark = pytest.mark.live


@pytest.fixture
def epdk(catalog):
    provider = EpdkProvider(catalog.source("epdk"), http=SecureHttpClient(rps=1))
    yield provider
    provider.close()


def _totals(df) -> dict:
    return df[df["entity_id"] == ""].set_index(["series_id", "date"])["value"].to_dict()


def test_epdk_healthcheck(epdk):
    result = epdk.healthcheck()
    assert result.status is HealthStatus.OK, result.detail


def test_epdk_seed_series_return_recent_holders(epdk, catalog):
    """Every catalog series resolves in the latest reports, with the large holders present."""
    codes = [s.code for s in catalog.source("epdk").series]
    df = epdk.fetch(FetchRequest.create("epdk", codes, date.today() - timedelta(days=120)))
    for code in codes:
        part = df[df["series_id"] == code]
        last = part[part["date"] == part["date"].max()].set_index("entity_id")["value"]
        assert (date.today() - part["date"].max()).days < 100, code
        assert len(last) >= 5 and last[""] > 0, code
    petrol = set(df[df["series_id"] == "EPDK.PETROL.MOTORIN"]["entity_id"])
    lpg = set(df[df["series_id"] == "EPDK.LPG.TOPLAM"]["entity_id"])
    assert {"PETROL_OFISI", "OPET_PETROLCULUK", "SHELL_TURCAS"} <= petrol
    assert {"AYGAZ", "PETROL_OFISI", "IPRAGAZ"} <= lpg


def test_epdk_august_2026_excel_annex(epdk):
    """Excel annex of August 2026: petroleum table 12 total and Petrol Ofisi (25,31 %); LPG product tables 8-10."""
    codes = ["EPDK.PETROL.TOPLAM", "EPDK.LPG.OTOGAZ", "EPDK.LPG.TUPLU", "EPDK.LPG.DOKME"]
    df = epdk.fetch(FetchRequest.create("epdk", codes, date(2026, 8, 1), date(2026, 8, 31)))
    totals = {code: value for (code, _), value in _totals(df).items()}
    assert totals == pytest.approx({"EPDK.PETROL.TOPLAM": 3375473.64, "EPDK.LPG.OTOGAZ": 298480.48,
                                    "EPDK.LPG.TUPLU": 47217.55, "EPDK.LPG.DOKME": 8841.09}, rel=1e-3)  # fmt: skip
    po = df[(df["series_id"] == "EPDK.PETROL.TOPLAM") & (df["entity_id"] == "PETROL_OFISI")]["value"].item()
    assert po / totals["EPDK.PETROL.TOPLAM"] * 100 == pytest.approx(25.31, abs=0.05)


@pytest.mark.parametrize(
    ("code", "month", "total"),
    [
        ("EPDK.PETROL.BENZIN", date(2025, 12, 1), 501114.47),  # Word report table 3.2 (and 1.1)
        ("EPDK.PETROL.TOPLAM", date(2016, 2, 1), None),  # 2016 headers "Şirket Ünvanı | Lisans Tipi"
        ("EPDK.LPG.TOPLAM", date(2016, 1, 1), 295306.0),  # 2016 LPG table, first column "DAĞITICI"
        ("EPDK.LPG.TOPLAM", date(2017, 1, 1), 291891.948),  # total row "Genel Toplam"
        ("EPDK.LPG.TOPLAM", date(2018, 1, 1), 315029.257),  # "Tüplügaz" / "Dökmegaz" headers
    ],
)
def test_epdk_older_report_formats(epdk, code, month, total):
    end = (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    df = epdk.fetch(FetchRequest.create("epdk", [code], month, end))
    value = _totals(df)[(code, end)]
    assert value == pytest.approx(total, rel=1e-4) if total else value > 0
    if code.startswith("EPDK.PETROL") and month.year == 2016:
        assert df.set_index("entity_id").loc["PETROL_OFISI", "entity_name"] == "PETROL OFİSİ"  # "OMV PETROL OFİSİ"
