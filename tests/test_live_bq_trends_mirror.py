"""Live contract test of the local copy: pytest -m live -k bq_trends. Copies two days into a temporary directory
(about 2.5 GiB of the free monthly query tier, less when BigQuery's query cache answers)."""

import os
from datetime import timedelta

import pytest

from mlbooster.infrastructure.catalog_yaml import YamlCatalogRepository
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.bigquery_mirror import BigQueryMirror
from mlbooster.infrastructure.providers.google_auth import credentials_path
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.settings import PACKAGE_DIR
from mlbooster.infrastructure.storage.trends_mirror import TrendsMirror

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("BQ_PROJECT_ID") and credentials_path()),
        reason="BQ_PROJECT_ID and Google Application Default Credentials are not set",
    ),
]


def test_bq_trends_mirror_copies_a_day_once(tmp_path):
    source = YamlCatalogRepository(PACKAGE_DIR / "catalog_data").load().source("bq_trends")
    mirror = TrendsMirror(tmp_path / "bq_trends")
    copy = BigQueryMirror(source, http=SecureHttpClient(rps=2), secrets=EnvSecretProvider(), mirror=mirror)
    today = copy._now()
    for table in ("top", "rising"):  # everything but the last few days counts as already held
        for i in range(4, 37):
            mirror.write_list(table, today - timedelta(days=i), mirror.lists(table, today, today))
    assert 0 < copy.estimate()["bytes"] < 5 * 1024**3
    report = copy.sync()
    assert report["days_added"] >= 1 and report["queries"] > 0
    listed = mirror.lists("top", today - timedelta(days=3), today)
    assert listed.groupby("refresh_date")["term"].count().between(20, 25).all()
    assert not mirror.terms("top").empty and mirror.size_bytes() > 0
    assert copy.sync()["days_added"] == 0
