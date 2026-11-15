"""Live contract tests for EPDK tariff tables: pytest -m live -k tarife (keyless; downloads about 40 small files)."""

from datetime import date

import pytest

from mlbooster.application.dto import FetchRequest
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.epdk_tariffs import EpdkTariffProvider

pytestmark = pytest.mark.live


@pytest.fixture
def tariffs(catalog):
    provider = EpdkTariffProvider(catalog.source("epdk_tarife"), http=SecureHttpClient(rps=1))
    yield provider
    provider.close()


def test_epdk_tarife_healthcheck(tariffs):
    result = tariffs.healthcheck()
    assert result.status is HealthStatus.OK, result.detail


def test_epdk_tarife_every_catalog_series_has_full_history(tariffs, catalog):
    """Every decision since 2016 parses; every series has one value per month from 2016-01."""
    codes = [s.code for s in catalog.source("epdk_tarife").series]
    df = tariffs.fetch(FetchRequest.create("epdk_tarife", codes, date(2016, 1, 1), date(2026, 10, 31)))
    months = (2026 - 2016) * 12 + 10
    assert df.groupby("series_id").size().to_dict() == dict.fromkeys(codes, months)
    assert (df["value"] > 0).all()


@pytest.mark.parametrize(
    ("code", "month", "value"),
    [
        ("EPDK.TARIFE.SANAYI.AG.ENERJI", date(2016, 1, 31), 0.205219),  # 6051 sayılı karar, 1/1/2016
        ("EPDK.TARIFE.SANAYI.OG.GUC", date(2016, 1, 31), 1.9189846),  # Nihai Tarife Tablosu, kr/Ay/kW
        ("EPDK.TARIFE.MESKEN.AG.ENERJI", date(2021, 12, 31), 0.474253),  # single rate before the tiers
        ("EPDK.TARIFE.MESKEN.AG.ENERJI", date(2022, 1, 31), 0.794622),  # low tier (5 kWh/gün) from 1/1/2022
        ("EPDK.TARIFE.SANAYI.AG.ENERJI", date(2020, 12, 31), 0.534358),  # 1/1/2021 decided on 30.01.2021
        ("EPDK.TARIFE.SANAYI.AG.ENERJI", date(2021, 1, 31), 0.552276),
        ("EPDK.TARIFE.SANAYI.AG.ENERJI", date(2025, 3, 31), 2.675627),  # 5/4/2025 tariff not yet in force
        ("EPDK.TARIFE.SANAYI.AG.ENERJI", date(2025, 4, 30), 2.693343),
        ("EPDK.TARIFE.SANAYI.OG.GUC", date(2026, 4, 30), 35.575915),  # 2026 one-sheet layout, kr/kW/ay
        ("EPDK.TARIFE.SANAYI.OG.ENERJI.MWH", date(2026, 4, 30), 2909.687),
    ],
)
def test_epdk_tarife_published_values(tariffs, code, month, value):
    df = tariffs.fetch(FetchRequest.create("epdk_tarife", [code], month.replace(day=1), month))
    assert df["value"].item() == pytest.approx(value, rel=1e-6)
