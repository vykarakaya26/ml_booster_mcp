"""Live contract tests for Google Trends on BigQuery: pytest -m live -k bq_trends.

Needs a Google Cloud project (BQ_PROJECT_ID; a BigQuery sandbox project is enough) and Application Default
Credentials (`gcloud auth application-default login` or GOOGLE_APPLICATION_CREDENTIALS). Skips without them. Every
query carries maximumBytesBilled, so a run cannot scan more than the cap per query.
"""

import os
from datetime import date, timedelta

import pytest
from dotenv import dotenv_values, find_dotenv

from mlbooster.application.dto import FetchRequest
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.bigquery_trends import BigQueryTrendsProvider
from mlbooster.infrastructure.providers.google_auth import credentials_path
from mlbooster.infrastructure.secret_store import EnvSecretProvider

# Read without modifying os.environ: offline tests must not see the settings.
_DOTENV = dotenv_values(find_dotenv(usecwd=True))
PROJECT = os.environ.get("BQ_PROJECT_ID") or _DOTENV.get("BQ_PROJECT_ID")
_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or _DOTENV.get("GOOGLE_APPLICATION_CREDENTIALS")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (PROJECT and (_CREDENTIALS or credentials_path())),
        reason="BQ_PROJECT_ID and Google Application Default Credentials are not set",
    ),
]


@pytest.fixture
def trends(catalog, monkeypatch):
    monkeypatch.setenv("BQ_PROJECT_ID", PROJECT)
    if _CREDENTIALS:
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", _CREDENTIALS)
    provider = BigQueryTrendsProvider(catalog.source("bq_trends"), http=SecureHttpClient(rps=2), secrets=EnvSecretProvider())
    yield provider
    provider.close()


def test_bq_trends_healthcheck(trends):
    result = trends.healthcheck()
    assert result.status is HealthStatus.OK, result.detail


def test_bq_trends_turkey_lists_cover_recent_days(trends):
    today = date.today()
    df = trends.fetch(FetchRequest.create("bq_trends", ["BQT.TOP.EKONOMI.SAYI"], today - timedelta(days=40), today))
    assert not df.empty, "no Türkiye list in the retained partitions (is TR covered?)"
    assert (today - max(df["date"])).days <= 4
    assert df["value"].between(0, 25).all()
    assert 20 <= df["date"].nunique() <= 36  # about 30 daily partitions are retained


def test_bq_trends_interest_of_a_listed_term(trends):
    today = date.today()
    listing = trends._list("top", today - timedelta(days=35), today)
    term = listing.sort_values(["refresh_date", "rank"]).iloc[-1]["folded"]
    code = "BQT.ILGI." + term.upper().replace(" ", "_")[:40]
    if not code.removeprefix("BQT.ILGI.").replace("_", "").isalnum() or len(term) < 2:
        pytest.skip(f"latest top term cannot be expressed as a series code: {term!r}")
    df = trends.fetch(FetchRequest.create("bq_trends", [code], today - timedelta(days=5 * 365), today))
    total = df[df["entity_id"] == ""]
    assert len(total) > 200  # about five years of weeks
    assert total["value"].between(0, 100).all()
    assert df[df["entity_id"] != ""]["entity_id"].str.startswith("TR-").all()
