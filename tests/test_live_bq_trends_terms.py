"""Live contract test of the search-interest layer: pytest -m live -k bq_trends (needs a Google Cloud project and ADC).

Costs about 10 GiB of the free monthly query tier for the retained lists of both tables."""

import os

import pytest

from mlbooster.application.use_cases.search_interest import SearchInterest
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.bigquery_terms import BigQueryTermLookup
from mlbooster.infrastructure.providers.google_auth import credentials_path
from mlbooster.infrastructure.search_terms_yaml import YamlSearchTerms
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.settings import PACKAGE_DIR

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("BQ_PROJECT_ID") and credentials_path()),
        reason="BQ_PROJECT_ID and Google Application Default Credentials are not set",
    ),
]


@pytest.fixture(scope="module")
def use():
    from mlbooster.infrastructure.catalog_yaml import YamlCatalogRepository

    repo = YamlCatalogRepository(PACKAGE_DIR / "catalog_data")
    lookup = BigQueryTermLookup(repo.load().source("bq_trends"), http=SecureHttpClient(rps=2), secrets=EnvSecretProvider())
    yield SearchInterest(repo, YamlSearchTerms(PACKAGE_DIR / "catalog_data" / "search_terms.yaml"), lookup)
    lookup.close()


def test_bq_trends_terms_report_every_term_and_no_share(use):
    result = use("Togg", "otomotiv")
    assert result["coverage"]["list_days"] >= 20
    assert {t["status"] for t in result["terms"]} <= {"found", "related", "not_found"}
    assert result["scale"]["share_of_search"] is None and "arama_payi" not in result["features"].columns


def test_bq_trends_scores_are_scaled_per_term_and_province(use):
    """The reason the share of search is withheld. If this fails, Google changed the scaling: revisit search_share.py."""
    result = use("Togg", "otomotiv", competitors=["Tesla", "BYD", "Renault"])
    if result["scale"]["series"] == 0:
        pytest.skip("none of the terms is in the retained lists")
    assert result["scale"]["per_series"] is True
