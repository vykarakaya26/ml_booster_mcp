"""Live contract tests for EPİAŞ: pytest -m live -k epias (requires EPIAS_USERNAME and EPIAS_PASSWORD)."""

import os
from dataclasses import replace
from datetime import date, timedelta

import pytest
from dotenv import dotenv_values, find_dotenv

from mlbooster.application.dto import FetchRequest
from mlbooster.domain.model import Frequency, HealthStatus, SeedSeries, SeriesKind
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


# Raw generation `total` (domestic generation + importExport), only for checking the provider's arithmetic.
RAW_TOTAL = SeedSeries("EPIAS.TEST.TOTAL", "test", Frequency.DAILY, SeriesKind.FLOW, remote="realtime-generation/total")


@pytest.fixture
def epias(catalog, monkeypatch):
    monkeypatch.setenv("EPIAS_USERNAME", USERNAME)
    monkeypatch.setenv("EPIAS_PASSWORD", PASSWORD)
    source = catalog.source("epias")
    source = replace(source, series=(*source.series, RAW_TOTAL))
    provider = EpiasProvider(source, http=SecureHttpClient(rps=1), secrets=EnvSecretProvider())
    yield provider
    provider.close()


def _days(df, code: str) -> list[date]:
    return sorted(df[(df["series_id"] == code) & (df["entity_id"] == "")]["date"])


def _calendar(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


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
    """The mix total is the sum of the sources; the API's `total` also contains the net exchange (importExport)."""
    day = date.today() - timedelta(days=3)
    codes = ["EPIAS.URETIM.KAYNAK", "EPIAS.URETIM", "EPIAS.ITHALAT_IHRACAT", "EPIAS.TEST.TOTAL"]
    df = epias.fetch(FetchRequest.create("epias", codes, day, day))
    mix = df[df["series_id"] == "EPIAS.URETIM.KAYNAK"].set_index("entity_id")["value"]
    value = df[df["entity_id"] == ""].set_index("series_id")["value"]
    assert len(mix) >= 8 and abs(mix.drop("").sum() - mix[""]) <= 1e-6 * mix[""]
    assert mix[""] == pytest.approx(value["EPIAS.URETIM"])
    assert value["EPIAS.URETIM"] + value["EPIAS.ITHALAT_IHRACAT"] == pytest.approx(value["EPIAS.TEST.TOTAL"], abs=1.0)


@pytest.mark.parametrize(
    ("code", "start", "end"),
    [
        ("EPIAS.PTF", date(2024, 6, 15), date(2026, 1, 31)),  # beyond the 1-year limit: two calls
        ("EPIAS.SMF", date(2025, 1, 1), date(2025, 12, 31)),
        ("EPIAS.TUKETIM", date(2024, 11, 1), date(2025, 12, 31)),
        ("EPIAS.URETIM.KAYNAK", date(2025, 1, 1), date(2025, 12, 31)),  # four 3-month calls
        ("EPIAS.URETIM", date(2025, 11, 30), date(2026, 3, 2)),  # month-end start, crosses February
    ],
)
def test_epias_ranges_beyond_the_per_call_limit_are_complete(epias, code, start, end):
    """Every day of a range longer than one call's limit (SEF1117: 1 year, generation 3 months) comes back once."""
    df = epias.fetch(FetchRequest.create("epias", [code], start, end))
    assert _days(df, code) == _calendar(start, end)


def test_epias_dates_before_2016_use_the_winter_offset(epias):
    """Winter days before September 2016 are +02:00; the 23-hour spring-forward day (2016-03-27) is kept."""
    df = epias.fetch(FetchRequest.create("epias", ["EPIAS.PTF", "EPIAS.URETIM"], date(2015, 1, 1), date(2015, 1, 2)))
    assert _days(df, "EPIAS.PTF") == _days(df, "EPIAS.URETIM") == [date(2015, 1, 1), date(2015, 1, 2)]
    df = epias.fetch(FetchRequest.create("epias", ["EPIAS.PTF", "EPIAS.URETIM"], date(2016, 3, 26), date(2016, 3, 28)))
    assert _days(df, "EPIAS.PTF") == _days(df, "EPIAS.URETIM") == _calendar(date(2016, 3, 26), date(2016, 3, 28))


def test_epias_ptf_is_plausible(epias):
    """PTF daily averages stay within the market price caps (TL/MWh) of recent years."""
    df = epias.fetch(FetchRequest.create("epias", ["EPIAS.PTF"], date(2026, 1, 1), date(2026, 1, 31)))
    assert len(df) == 31 and df["value"].between(0, 10_000).all()
