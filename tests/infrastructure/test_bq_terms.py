"""BigQuery term lookup and the search term catalog against respx mocks; values are synthetic."""

import json
from datetime import date

import httpx
import pytest
import respx

from mlbooster.application.errors import UnexpectedResponse
from mlbooster.domain.errors import InconsistentCatalog, InvalidInput
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers import bigquery_trends
from mlbooster.infrastructure.providers.bigquery_terms import ROW_COLUMNS, BigQueryTermLookup
from mlbooster.infrastructure.providers.google_auth import TOKEN_URL, TokenCache
from mlbooster.infrastructure.search_terms_yaml import YamlSearchTerms
from mlbooster.infrastructure.secret_store import EnvSecretProvider

PROJECT = "benim-projem-123"
QUERY_URL = f"https://bigquery.googleapis.com/bigquery/v2/projects/{PROJECT}/queries"
TODAY = date(2026, 11, 4)


def answer(columns, rows, billed="1000"):
    return httpx.Response(200, json={
        "jobComplete": True, "jobReference": {"jobId": "job_1", "location": "US"}, "totalBytesBilled": billed,
        "schema": {"fields": [{"name": c, "type": "STRING"} for c in columns]},
        "rows": [{"f": [{"v": v} for v in r]} for r in rows],
    })  # fmt: skip


@pytest.fixture(autouse=True)
def _fresh_cache():
    bigquery_trends._SHARED_CHUNKS.clear()
    yield
    bigquery_trends._SHARED_CHUNKS.clear()


@pytest.fixture
def lookup(catalog, tmp_path, monkeypatch):
    path = tmp_path / "adc.json"
    path.write_text(json.dumps({"type": "authorized_user", "client_id": "c", "client_secret": "s", "refresh_token": "r"}))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(path))
    monkeypatch.setenv("BQ_PROJECT_ID", PROJECT)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "ya29.token-0123456789abcdef", "expires_in": 3600}))
        provider = BigQueryTermLookup(
            catalog.source("bq_trends"), http=SecureHttpClient(rps=None, max_retries=0), secrets=EnvSecretProvider(),
            tokens=TokenCache(), today=TODAY,
        )  # fmt: skip
        provider.mock = mock
        yield provider


def test_listed_terms_cover_both_tables_and_are_metered(lookup):
    route = lookup.mock.post(QUERY_URL).mock(return_value=answer(["refresh_date", "term", "rank"], [["2026-11-01", "togg", "3"]]))
    frame = lookup.listed_terms()
    assert set(frame["table"]) == {"top", "rising"} and list(frame.columns) == ["table", "refresh_date", "term", "rank"]
    assert route.call_count == 12 and lookup.queries == 12 and lookup.bytes_billed == 12000  # 6 chunks × 2 tables


def test_interest_batches_terms_and_days(lookup):
    route = lookup.mock.post(QUERY_URL).mock(return_value=answer(ROW_COLUMNS, [
        ["togg", "2026-11-01", "2026-10-25", None, None, "26.5"],
        ["togg", "2026-11-01", "2026-10-25", "TR-34", "İstanbul", "100"],
    ]))  # fmt: skip
    frame = lookup.interest("top", {"togg": date(2026, 11, 1), "tesla": date(2026, 11, 1), "byd": date(2026, 10, 20)})
    assert route.call_count == 1
    sent = json.loads(route.calls[0].request.content)
    params = {p["name"]: p["parameterValue"] for p in sent["queryParameters"]}
    assert [v["value"] for v in params["days"]["arrayValues"]] == ["2026-10-20", "2026-11-01"]
    assert sorted(v["value"] for v in params["terms"]["arrayValues"]) == ["byd", "tesla", "togg"]
    assert params["country"] == {"value": "TR"} and "refresh_date IN UNNEST(@days)" in sent["query"]
    assert "togg" not in sent["query"] and sent["maximumBytesBilled"] == str(bigquery_trends.DEFAULT_MAX_BYTES)
    assert frame["region_code"].tolist() == ["", "TR-34"] and frame["score"].tolist() == [26.5, 100.0]


def test_interest_splits_more_than_seven_days(lookup):
    route = lookup.mock.post(QUERY_URL).mock(return_value=answer(ROW_COLUMNS, []))
    assert lookup.interest("top", {f"t{i}": date(2026, 10, i + 1) for i in range(9)}).empty
    assert route.call_count == 2


def test_interest_without_terms_sends_nothing(lookup):
    route = lookup.mock.post(QUERY_URL)
    assert lookup.interest("top", {}).empty and route.call_count == 0


@pytest.mark.parametrize(
    ("columns", "rows", "match"),
    [
        (["x"], [], "kolon"),
        (ROW_COLUMNS, [["togg", "bad", "2026-10-25", None, None, "1"]], "tanınmayan"),
        (ROW_COLUMNS, [["togg", "2026-11-01", "2026-10-25", None, None, "120"]], "0–100"),
    ],
)
def test_interest_rejects_bad_responses(lookup, columns, rows, match):
    lookup.mock.post(QUERY_URL).mock(return_value=answer(columns, rows))
    with pytest.raises(UnexpectedResponse, match=match):
        lookup.interest("top", {"togg": date(2026, 11, 1)})


@pytest.mark.parametrize(
    "text",
    ["sectors: {otomotiv: {generic: [x]}}", "sectors: {otomotiv: {bilinmeyen: [abc]}}", "sectors: {'A B': {generic: [abc]}}", "diger: 1"],
)
def test_search_terms_are_validated(tmp_path, text):
    path = tmp_path / "search_terms.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises((InconsistentCatalog, InvalidInput)):
        YamlSearchTerms(path).load()


def test_search_terms_load_once(tmp_path):
    path = tmp_path / "search_terms.yaml"
    path.write_text("sectors: {otomotiv: {generic: [sıfır araba], brands: [Togg]}}", encoding="utf-8")
    terms = YamlSearchTerms(path)
    assert terms.load()["otomotiv"].brands == ("Togg",)
    path.unlink()
    assert terms.load()["otomotiv"].generic == ("sıfır araba",)
