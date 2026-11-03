"""Google Trends (BigQuery public dataset) provider against respx mocks built from the documented jobs.query /
getQueryResults formats and the dataset schema of the Marketplace listing (2026-11-03). Values are synthetic."""

import base64
import json
from dataclasses import replace
from datetime import date, timedelta
from urllib.parse import parse_qs, parse_qsl, urlsplit

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from mlbooster.application.dto import FetchRequest
from mlbooster.application.errors import AuthenticationFailed, CredentialsMissing, ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers import bigquery_trends, google_auth
from mlbooster.infrastructure.providers.bigquery_rest import BigQueryClient, Param, project_id
from mlbooster.infrastructure.providers.bigquery_trends import (
    DEFAULT_MAX_BYTES,
    BigQueryTrendsProvider,
    fold,
    max_bytes_billed,
    parse_remote,
)
from mlbooster.infrastructure.providers.google_auth import (
    TOKEN_URL,
    GoogleTokenSource,
    TokenCache,
    load_credentials,
    parse_credentials,
)
from mlbooster.infrastructure.providers.registry import PROVIDERS, ProviderRegistry
from mlbooster.infrastructure.secret_store import EnvSecretProvider

PROJECT = "benim-projem-123"
QUERY_URL = f"https://bigquery.googleapis.com/bigquery/v2/projects/{PROJECT}/queries"
TODAY = date(2026, 11, 3)
REFRESH_TOKEN = "1//refresh-token-abcdef0123456789"
CLIENT_SECRET = "client-secret-0123456789"
ACCESS = "ya29.access-token-ABCDEF0123456789"
ACCESS2 = "ya29.second-token-ZYXWVU9876543210"


def adc(**extra) -> dict:
    return {"type": "authorized_user", "client_id": "123.apps.googleusercontent.com", "client_secret": CLIENT_SECRET,
            "refresh_token": REFRESH_TOKEN, **extra}  # fmt: skip


@pytest.fixture(autouse=True)
def _fresh_shared_chunks():
    bigquery_trends._SHARED_CHUNKS.clear()
    yield
    bigquery_trends._SHARED_CHUNKS.clear()


@pytest.fixture
def creds(tmp_path, monkeypatch):
    path = tmp_path / "adc.json"
    path.write_text(json.dumps(adc()))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(path))
    monkeypatch.setenv("BQ_PROJECT_ID", PROJECT)
    monkeypatch.delenv("BQ_MAX_BYTES_BILLED", raising=False)
    return path


@pytest.fixture
def no_creds(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("BQ_PROJECT_ID", raising=False)
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path / "no-gcloud"))  # no default ADC file either


@pytest.fixture
def make(catalog):
    def factory(source=None, today=TODAY):
        return BigQueryTrendsProvider(
            source or catalog.source("bq_trends"),
            http=SecureHttpClient(rps=None, max_retries=0, sleep=lambda _: None),
            secrets=EnvSecretProvider(),
            tokens=TokenCache(),
            today=today,
        )

    return factory


@pytest.fixture
def provider(make):
    p = make()
    yield p
    p.close()


def token_ok(token=ACCESS, expires_in=3599):
    return httpx.Response(200, json={"access_token": token, "expires_in": expires_in, "token_type": "Bearer"})


def bq(columns, rows, *, complete=True, page_token=None, job="job_abc123"):
    doc = {
        "kind": "bigquery#queryResponse",
        "jobReference": {"projectId": PROJECT, "jobId": job, "location": "US"},
        "jobComplete": complete,
        "totalBytesProcessed": "73400320",
        "totalBytesBilled": "73400320",
    }
    if complete:
        doc |= {"schema": {"fields": [{"name": c, "type": "STRING"} for c in columns]},
                "totalRows": str(len(rows)), "rows": [{"f": [{"v": v} for v in r]} for r in rows]}  # fmt: skip
    if page_token:
        doc["pageToken"] = page_token
    return httpx.Response(200, json=doc)


LIST_COLUMNS = ["refresh_date", "term", "rank"]
INTEREST_COLUMNS = ["week", "region_code", "region_name", "score"]


def listing(days=(date(2026, 11, 1), date(2026, 11, 2))):
    rows = []
    for d in days:
        terms = ["Galatasaray", "Dolar kuru", "Asgari ücret 2027", "Togg T10F", "Türk Telekom arıza", "deprem"]
        if d == days[-1]:
            terms.append("togg")
        rows += [[d.isoformat(), t, str(i + 1)] for i, t in enumerate(terms)]
    return rows


def body(call) -> dict:
    return json.loads(call.request.content)


def params(call) -> dict:
    return {p["name"]: p["parameterValue"] for p in body(call)["queryParameters"]}


# --- Catalog, registry, hosts ----------------------------------------------------------


def test_registered_and_catalog_points_to_adapter(catalog):
    source = catalog.source("bq_trends")
    assert PROVIDERS[source.provider] is BigQueryTrendsProvider
    assert source.canaries and all(parse_remote(s.remote) for s in source.series)
    assert not source.storage.raw and source.storage.observations
    assert source.auth.kind.value == "oauth"


def test_registry_creates_provider(catalog, no_creds):
    p = ProviderRegistry(EnvSecretProvider()).create(catalog.source("bq_trends"))
    assert isinstance(p, BigQueryTrendsProvider)
    p.close()


def test_refuses_untrusted_host(catalog):
    hijacked = replace(catalog.source("bq_trends"), base_url="https://evil.example/bigquery/v2/")
    with pytest.raises(ProviderError, match="yalnızca"):
        BigQueryTrendsProvider(hijacked, http=SecureHttpClient(), secrets=EnvSecretProvider())


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("top,count/dolar+asgari_ucret", ("top", "count", ("dolar", "asgari ucret"))),
        ("rising,interest/togg", ("rising", "interest", ("togg",))),
    ],
)
def test_parse_remote(remote, expected):
    assert parse_remote(remote) == expected


@pytest.mark.parametrize("remote", ["top/dolar", "top,share/dolar", "top,count/Dolar", "top,count/a", "x,count/dolar",
                                    "top,count/" + "+".join(f"k{i:02d}" for i in range(21))])  # fmt: skip
def test_parse_remote_rejects(remote):
    with pytest.raises(InvalidInput):
        parse_remote(remote)


def test_fold_turkish():
    assert fold("Türk Telekom'da ARIZA!") == "turk telekom da ariza"
    assert fold("İŞ Bankası") == "is bankasi"


def test_max_bytes_billed(monkeypatch):
    assert max_bytes_billed(None) == DEFAULT_MAX_BYTES
    assert max_bytes_billed("20971520") == 20971520
    for bad in ("abc", "1000", str(10**15)):
        with pytest.raises(InvalidInput):
            max_bytes_billed(bad)


def test_project_id_validation():
    assert project_id(PROJECT) == PROJECT
    for bad in (None, "", "Upper-Case", "a", "x" * 40, "proj/../other"):
        with pytest.raises(CredentialsMissing):
            project_id(bad)


# --- Credentials -----------------------------------------------------------------------


def test_missing_credentials_message(no_creds):
    with pytest.raises(CredentialsMissing, match="application-default login"):
        load_credentials()


def test_credentials_file_errors(tmp_path, monkeypatch):
    path = tmp_path / "c.json"
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(path))
    with pytest.raises(CredentialsMissing, match="bulunamadı"):
        load_credentials()
    path.write_text("{not json")
    with pytest.raises(CredentialsMissing, match="JSON"):
        load_credentials()
    path.write_text("[]")
    with pytest.raises(CredentialsMissing, match="biçimde"):
        load_credentials()
    path.write_text(" " * (google_auth.MAX_CREDENTIALS_BYTES + 1))
    with pytest.raises(CredentialsMissing, match="büyük"):
        load_credentials()


@pytest.mark.parametrize(
    "data",
    [
        {"type": "external_account"},
        {"type": "authorized_user", "client_id": "x"},
        {"type": "service_account", "client_email": "not-an-email", "private_key": "k"},
        {"type": "service_account", "client_email": "a@b.iam.gserviceaccount.com", "private_key": "k",
         "token_uri": "https://evil.example/token"},
    ],
)  # fmt: skip
def test_rejects_bad_credentials(data):
    with pytest.raises(CredentialsMissing):
        parse_credentials(data, "fp")


def test_default_adc_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path))
    assert google_auth.default_adc_path() == tmp_path / "application_default_credentials.json"
    monkeypatch.delenv("CLOUDSDK_CONFIG")
    assert google_auth.default_adc_path().parts[-3:] == (".config", "gcloud", "application_default_credentials.json")


def test_default_adc_file_is_used(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path))
    (tmp_path / "application_default_credentials.json").write_text(json.dumps(adc(quota_project_id="kota-projesi-1")))
    assert load_credentials().quota_project_id == "kota-projesi-1"


def test_secrets_are_not_shown(creds):
    c = load_credentials()
    assert REFRESH_TOKEN not in repr(c) and CLIENT_SECRET not in repr(c)


@respx.mock
def test_refresh_token_grant_and_cache(creds):
    route = respx.post(TOKEN_URL).mock(return_value=token_ok())
    cache = TokenCache()
    source = GoogleTokenSource(SecureHttpClient(rps=None, max_retries=0), "scope", cache)
    assert source.token().reveal() == ACCESS
    assert GoogleTokenSource(SecureHttpClient(rps=None, max_retries=0), "scope", cache).token().reveal() == ACCESS
    assert route.call_count == 1
    sent = dict(parse_qsl(route.calls[0].request.content.decode()))
    assert sent == {"grant_type": "refresh_token", "client_id": "123.apps.googleusercontent.com",
                    "client_secret": CLIENT_SECRET, "refresh_token": REFRESH_TOKEN}  # fmt: skip


def test_token_cache_expiry():
    now = [1000.0]
    cache = TokenCache(clock=lambda: now[0])
    cache.put("k", google_auth.Secret("v"), 60)
    assert cache.get("k").reveal() == "v"
    now[0] += 61
    assert cache.get("k") is None


@respx.mock
def test_rejected_refresh_token_is_reported_without_secrets(creds):
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant", "error_description": f"Bad {REFRESH_TOKEN}"})
    )
    source = GoogleTokenSource(SecureHttpClient(rps=None, max_retries=0), "scope", TokenCache())
    with pytest.raises(AuthenticationFailed, match="invalid_grant") as exc:
        source.token()
    assert REFRESH_TOKEN not in str(exc.value)


@respx.mock
@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(302, headers={"location": "https://evil.example"}), UnexpectedResponse),
        (httpx.Response(500, text="x"), ProviderError),
        (httpx.Response(200, text="<html>"), UnexpectedResponse),
        (httpx.Response(200, json={"access_token": "bad token with spaces"}), UnexpectedResponse),
    ],
)
def test_token_endpoint_errors(creds, response, error):
    respx.post(TOKEN_URL).mock(return_value=response)
    with pytest.raises(error):
        GoogleTokenSource(SecureHttpClient(rps=None, max_retries=0), "scope", TokenCache()).token()


def _service_account(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    path = tmp_path / "sa.json"
    path.write_text(json.dumps({"type": "service_account", "project_id": "sa-projesi-1", "private_key_id": "kid1",
                                "private_key": pem, "client_email": "mlb@sa-projesi-1.iam.gserviceaccount.com",
                                "token_uri": TOKEN_URL}))  # fmt: skip
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(path))
    return key


def _unb64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


@respx.mock
def test_service_account_jwt_grant(tmp_path, monkeypatch):
    key = _service_account(tmp_path, monkeypatch)
    route = respx.post(TOKEN_URL).mock(return_value=token_ok())
    cache = TokenCache(clock=lambda: 1_700_000_000.0)
    assert GoogleTokenSource(SecureHttpClient(rps=None, max_retries=0), "https://x/scope", cache).token().reveal() == ACCESS
    sent = dict(parse_qsl(route.calls[0].request.content.decode()))
    assert sent["grant_type"] == google_auth.JWT_BEARER
    header, claims, signature = sent["assertion"].split(".")
    assert json.loads(_unb64(header)) == {"alg": "RS256", "typ": "JWT", "kid": "kid1"}
    assert json.loads(_unb64(claims)) == {"iss": "mlb@sa-projesi-1.iam.gserviceaccount.com", "scope": "https://x/scope",
                                          "aud": TOKEN_URL, "iat": 1_700_000_000, "exp": 1_700_003_600}  # fmt: skip
    key.public_key().verify(_unb64(signature), f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
    assert "BEGIN PRIVATE KEY" not in route.calls[0].request.content.decode()


def test_service_account_bad_key(tmp_path, monkeypatch):
    _service_account(tmp_path, monkeypatch)
    c = replace(load_credentials(), private_key=google_auth.Secret("-----BEGIN PRIVATE KEY-----\nxx\n-----END"))
    with pytest.raises(CredentialsMissing, match="okunamadı"):
        google_auth.signed_jwt(c, "s", 0)


# --- BigQuery client -------------------------------------------------------------------


def _client(max_bytes=DEFAULT_MAX_BYTES):
    http = SecureHttpClient(rps=None, max_retries=0)
    return BigQueryClient(http, GoogleTokenSource(http, "scope", TokenCache()), PROJECT, max_bytes)


@respx.mock
def test_query_request_is_parameterized_and_capped(creds):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    route = respx.post(QUERY_URL).mock(return_value=bq(["a"], [["1"]]))
    result = _client(20971520).query(
        "SELECT @x", [Param("x", "STRING", "TR"), Param("d", "DATE", date(2026, 1, 2)), Param("t", "STRING", ["a", "b"])]
    )
    assert result.rows == [["1"]] and result.bytes_billed == 73400320
    sent = body(route.calls[0])
    assert sent["useLegacySql"] is False and sent["parameterMode"] == "NAMED"
    assert sent["maximumBytesBilled"] == "20971520"
    assert sent["queryParameters"] == [
        {"name": "x", "parameterType": {"type": "STRING"}, "parameterValue": {"value": "TR"}},
        {"name": "d", "parameterType": {"type": "DATE"}, "parameterValue": {"value": "2026-01-02"}},
        {"name": "t", "parameterType": {"type": "ARRAY", "arrayType": {"type": "STRING"}},
         "parameterValue": {"arrayValues": [{"value": "a"}, {"value": "b"}]}},
    ]  # fmt: skip
    assert route.calls[0].request.headers["authorization"] == f"Bearer {ACCESS}"
    assert ACCESS not in str(route.calls[0].request.url)


@respx.mock
def test_query_pages_and_waits_for_job(creds):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    respx.post(QUERY_URL).mock(return_value=bq([], [], complete=False))
    results = respx.get(url__startswith=f"{QUERY_URL}/job_abc123").mock(
        side_effect=[bq(["a"], [["1"]], page_token="p2"), bq(["a"], [["2"]])]
    )
    assert _client().query("SELECT 1", []).rows == [["1"], ["2"]]
    first, second = (parse_qs(urlsplit(str(c.request.url)).query) for c in results.calls)
    assert first["location"] == ["US"] and "pageToken" not in first and second["pageToken"] == ["p2"]


@respx.mock
def test_query_retries_once_with_fresh_token(creds):
    respx.post(TOKEN_URL).mock(side_effect=[token_ok(), token_ok(ACCESS2)])
    route = respx.post(QUERY_URL).mock(side_effect=[httpx.Response(401, json={"error": {"code": 401}}), bq(["a"], [])])
    _client().query("SELECT 1", [])
    assert route.calls[1].request.headers["authorization"] == f"Bearer {ACCESS2}"


@respx.mock
def test_query_401_twice_fails(creds):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    respx.post(QUERY_URL).mock(return_value=httpx.Response(401, json={"error": {"code": 401}}))
    with pytest.raises(AuthenticationFailed):
        _client().query("SELECT 1", [])


def _error(status, reason, message="m"):
    return httpx.Response(status, json={"error": {"code": status, "message": message,
                                                  "errors": [{"reason": reason, "message": message}]}})  # fmt: skip


@respx.mock
@pytest.mark.parametrize(
    ("response", "error", "match"),
    [
        (_error(400, "bytesBilledLimitExceeded", "Query exceeded limit for bytes billed: 10485760."), InvalidInput, "tavan"),
        (_error(400, "invalidQuery", "Query exceeded limit for bytes billed: 10485760."), InvalidInput, "tavan"),
        (_error(403, "accessDenied", f"denied {ACCESS}"), AuthenticationFailed, "Job User"),
        (_error(403, "billingNotEnabled"), AuthenticationFailed, "Job User"),
        (_error(400, "invalidQuery"), UnexpectedResponse, "kabul etmedi"),
        (_error(404, "notFound"), UnexpectedResponse, "bulunamadı"),
        (_error(409, "duplicate"), ProviderError, "409"),
        (httpx.Response(200, text="<html>", headers={"content-type": "text/html"}), UnexpectedResponse, "JSON"),
        (httpx.Response(200, text="{bad", headers={"content-type": "application/json"}), UnexpectedResponse, "çözümlenemedi"),
        (httpx.Response(200, json=[1]), UnexpectedResponse, "nesne"),
        (httpx.Response(301, headers={"location": "https://evil.example"}), UnexpectedResponse, "yönlendirme"),
        (httpx.Response(200, json={"jobComplete": True, "rows": []}), UnexpectedResponse, "schema"),
        (httpx.Response(200, json={"jobComplete": True, "schema": {"fields": [{"name": "a"}]}, "rows": [{"f": []}]}),
         UnexpectedResponse, "şemayla"),
        (httpx.Response(200, json={"jobComplete": True, "schema": {"fields": [{"name": "a"}]}, "rows": [{"f": [{"v": {"x": 1}}]}]}),
         UnexpectedResponse, "iç içe"),
        (httpx.Response(200, json={"jobComplete": True, "schema": {"fields": [{"name": "a"}]}, "rows": {}}),
         UnexpectedResponse, "liste"),
        (httpx.Response(200, json={"jobComplete": False, "jobReference": {"jobId": "bad/id"}}), UnexpectedResponse, "jobReference"),
    ],
)  # fmt: skip
def test_query_errors(creds, response, error, match):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    respx.post(QUERY_URL).mock(return_value=response)
    with pytest.raises(error, match=match) as exc:
        _client().query("SELECT 1", [])
    assert ACCESS not in str(exc.value)


@respx.mock
def test_query_gives_up_after_max_pages(creds, monkeypatch):
    monkeypatch.setattr("mlbooster.infrastructure.providers.bigquery_rest.MAX_PAGES", 2)
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    respx.post(QUERY_URL).mock(return_value=bq([], [], complete=False))
    respx.get(url__startswith=f"{QUERY_URL}/job_abc123").mock(return_value=bq([], [], complete=False))
    with pytest.raises(UnexpectedResponse, match="zamanında"):
        _client().query("SELECT 1", [])


@respx.mock
def test_dry_run(creds):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    route = respx.post(QUERY_URL).mock(return_value=httpx.Response(200, json={"totalBytesProcessed": "123", "jobComplete": True}))
    assert _client().query("SELECT 1", [], dry_run=True).bytes_processed == 123
    assert body(route.calls[0])["dryRun"] is True


# --- Provider ----------------------------------------------------------------------------


def _req(code, start=date(2026, 10, 1), end=TODAY, **kw):
    return FetchRequest.create("bq_trends", [code] if isinstance(code, str) else code, start, end, **kw)


@respx.mock
def test_count_series_per_refresh_date(provider, creds):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    route = respx.post(QUERY_URL).mock(return_value=bq(LIST_COLUMNS, listing()))
    df = provider.fetch(_req(["BQT.TOP.EKONOMI.SAYI", "BQT.TOP.TELEKOM.SAYI"]))
    assert route.call_count == 5  # 7-day chunks, shared by every count series of the same table
    eco = df[df["series_id"] == "BQT.TOP.EKONOMI.SAYI"]
    assert list(eco["date"]) == [date(2026, 11, 1), date(2026, 11, 2)]
    assert list(eco["value"]) == [2.0, 2.0]  # "Dolar kuru", "Asgari ücret 2027"
    assert list(df[df["series_id"] == "BQT.TOP.TELEKOM.SAYI"]["value"]) == [1.0, 1.0]  # "Türk Telekom arıza"
    assert set(df["freq"]) == {"D"} and df["is_proxy"].all() and set(df["entity_id"]) == {""}
    assert set(df["dataset_id"]) == {"international_top_terms"} and set(df["unit"]) == {"count"}
    sql = body(route.calls[0])["query"]
    assert "refresh_date BETWEEN @since AND @until" in sql and "country_code = @country" in sql
    assert "SELECT *" not in sql
    p = params(route.calls[0])
    assert p["country"] == {"value": "TR"} and p["since"] == {"value": "2026-10-01"} and p["until"] == {"value": "2026-10-07"}
    last = params(route.calls[-1])
    assert last["since"] == {"value": "2026-10-29"} and last["until"] == {"value": "2026-11-03"}


@respx.mock
def test_count_window_is_clamped_to_retention(provider, creds):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    route = respx.post(QUERY_URL).mock(return_value=bq(LIST_COLUMNS, []))
    assert provider.fetch(_req("BQT.TOP.EKONOMI.SAYI", start=date(2020, 1, 1))).empty
    assert params(route.calls[0])["since"] == {"value": (TODAY - timedelta(days=35)).isoformat()}


@respx.mock
def test_count_outside_retention_sends_no_query(provider, creds):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    route = respx.post(QUERY_URL)
    assert provider.fetch(_req("BQT.TOP.EKONOMI.SAYI", start=date(2024, 1, 1), end=date(2024, 6, 30))).empty
    assert route.call_count == 0


@respx.mock
def test_interest_uses_latest_list_and_one_partition(provider, creds):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    weeks = [(date(2026, 10, 18), {"TR-34": "40", "TR-06": "60"}), (date(2026, 10, 25), {"TR-34": "100", "TR-06": None})]
    rows = [[w.isoformat(), r, {"TR-34": "İstanbul", "TR-06": "Ankara"}[r], s] for w, regs in weeks for r, s in regs.items()]
    route = respx.post(QUERY_URL).mock(side_effect=[bq(LIST_COLUMNS, listing()), bq(INTEREST_COLUMNS, rows)])
    df = provider.fetch(_req("BQT.ILGI.TOGG"))
    p = params(route.calls[1])
    assert p["refresh"] == {"value": "2026-11-02"} and p["terms"] == {"arrayValues": [{"value": "togg"}]}
    assert "refresh_date = @refresh" in body(route.calls[1])["query"] and "start" not in p
    total = df[df["entity_id"] == ""]
    assert list(total["date"]) == [date(2026, 10, 24), date(2026, 10, 31)]  # week start + 6 days
    assert list(total["value"]) == [50.0, 50.0]  # mean over provinces; a null score (too little volume) counts as 0
    assert len(df[(df["entity_id"] == "TR-06")]) == 1  # province rows keep only published scores
    regions = df[df["entity_id"] != ""]
    assert set(regions["entity_id"]) == {"TR-34", "TR-06"} and "İstanbul" in set(regions["entity_name"])
    assert set(df["freq"]) == {"W"} and set(df["unit"]) == {"index_0_100"} and set(df["publication_lag_days"]) == {2}


@respx.mock
def test_interest_prefers_country_level_row(provider, creds):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    rows = [["2026-10-25", None, None, "70"], ["2026-10-25", "TR-34", "İstanbul", "10"]]
    respx.post(QUERY_URL).mock(side_effect=[bq(LIST_COLUMNS, listing()), bq(INTEREST_COLUMNS, rows)])
    df = provider.fetch(_req("BQT.ILGI.TOGG"))
    assert df[df["entity_id"] == ""]["value"].tolist() == [70.0]


@respx.mock
def test_interest_for_term_not_in_lists_is_empty(provider, creds):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    route = respx.post(QUERY_URL).mock(return_value=bq(LIST_COLUMNS, listing()))
    assert provider.fetch(_req("BQT.ILGI.TURKCELL")).empty
    assert route.call_count == 6  # every retained chunk was searched, most recent first


@respx.mock
@pytest.mark.parametrize(
    ("rows", "match"),
    [([["2026-10-25", "TR-34", "x", "120"]], "0–100"), ([["25.10.2026", "TR-34", "x", "1"]], "tanınmayan")],
)
def test_interest_rejects_bad_values(provider, creds, rows, match):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    respx.post(QUERY_URL).mock(side_effect=[bq(LIST_COLUMNS, listing()), bq(INTEREST_COLUMNS, rows)])
    with pytest.raises(UnexpectedResponse, match=match):
        provider.fetch(_req("BQT.ILGI.TOGG"))


@respx.mock
def test_interest_all_null_scores(provider, creds):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    respx.post(QUERY_URL).mock(side_effect=[bq(LIST_COLUMNS, listing()), bq(INTEREST_COLUMNS, [["2026-10-25", "TR-34", "x", None]])])
    assert provider.fetch(_req("BQT.ILGI.TOGG")).empty


@respx.mock
@pytest.mark.parametrize(
    ("first", "second"),
    [(bq(["x"], []), None), (bq(LIST_COLUMNS, [["bad-date", "t", "1"]]), None),
     (bq(LIST_COLUMNS, listing()), bq(["x"], []))],
)  # fmt: skip
def test_unexpected_columns(provider, creds, first, second):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    respx.post(QUERY_URL).mock(side_effect=[r for r in (first, second) if r is not None])
    with pytest.raises(UnexpectedResponse):
        provider.fetch(_req("BQT.ILGI.TOGG"))


def test_unknown_series_and_options(provider, creds):
    with pytest.raises(InvalidInput, match="BQT.ILGI"):
        provider.fetch(_req("BQT.YOK"))
    with pytest.raises(InvalidInput, match="formül"):
        provider.fetch(_req("BQT.TOP.EKONOMI.SAYI", formula=1))


def test_missing_project_fails_before_queries(provider, creds, monkeypatch):
    monkeypatch.delenv("BQ_PROJECT_ID")
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(url__startswith="https://")
        with pytest.raises(CredentialsMissing, match="BQ_PROJECT_ID"):
            provider.fetch(_req("BQT.TOP.EKONOMI.SAYI"))
    assert route.call_count == 0


@respx.mock
def test_project_falls_back_to_quota_project(make, creds):
    creds.write_text(json.dumps(adc(quota_project_id="kota-projesi-1")))
    import os

    os.environ.pop("BQ_PROJECT_ID")
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    route = respx.post("https://bigquery.googleapis.com/bigquery/v2/projects/kota-projesi-1/queries").mock(
        return_value=bq(LIST_COLUMNS, [])
    )
    make().fetch(_req("BQT.TOP.EKONOMI.SAYI"))
    assert route.call_count >= 1


def test_list_series(provider):
    df = provider.list_series("telekom")
    assert list(df["series_id"]) == ["BQT.TOP.TELEKOM.SAYI"]
    assert len(provider.list_series()) >= 6


# --- Healthcheck -----------------------------------------------------------------------


def test_health_skipped_without_credentials(provider, no_creds):
    assert provider.healthcheck().status is HealthStatus.SKIPPED


def test_health_skipped_without_project(provider, creds, monkeypatch):
    monkeypatch.delenv("BQ_PROJECT_ID")
    result = provider.healthcheck()
    assert result.status is HealthStatus.SKIPPED and "BQ_PROJECT_ID" in result.detail


@respx.mock
def test_health_ok(provider, creds):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    respx.post(QUERY_URL).mock(return_value=bq(LIST_COLUMNS, listing()))
    result = provider.healthcheck()
    assert result.status is HealthStatus.OK and "2026-11-02" in result.detail


@respx.mock
@pytest.mark.parametrize(("rows", "match"), [([], "liste yok"), (listing((date(2026, 10, 20),)), "son liste")])
def test_health_fails_on_missing_or_old_lists(provider, creds, rows, match):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    respx.post(QUERY_URL).mock(return_value=bq(LIST_COLUMNS, rows))
    result = provider.healthcheck()
    assert result.status is HealthStatus.FAIL and match in result.detail


@respx.mock
def test_health_fails_on_provider_error(provider, creds):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    respx.post(QUERY_URL).mock(return_value=_error(400, "bytesBilledLimitExceeded"))
    assert provider.healthcheck().status is HealthStatus.FAIL
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(500))
    provider._tokens.invalidate()
    assert provider.healthcheck().status is HealthStatus.FAIL


def test_health_without_canaries(make, catalog):
    source = catalog.source("bq_trends")
    p = make(replace(source, series=tuple(replace(s, canary=False) for s in source.series)))
    assert p.healthcheck().status is HealthStatus.SKIPPED


@respx.mock
def test_interest_returns_whole_backfill_on_one_scale(provider, creds):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    rows = [["2022-01-02", "TR-34", "İstanbul", "100"], ["2026-10-25", "TR-34", "İstanbul", "20"]]
    respx.post(QUERY_URL).mock(side_effect=[bq(LIST_COLUMNS, listing()), bq(INTEREST_COLUMNS, rows)])
    df = provider.fetch(_req("BQT.ILGI.TOGG", start=date(2026, 10, 1)))
    # weeks before the requested start come too: they were rescaled together with the recent ones
    assert df[df["entity_id"] == ""]["date"].min() == date(2022, 1, 8)


@respx.mock
def test_settled_chunks_are_shared_between_providers(make, creds):
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    route = respx.post(QUERY_URL).mock(return_value=bq(LIST_COLUMNS, listing((date(2026, 10, 20),))))
    request = _req("BQT.TOP.EKONOMI.SAYI", start=date(2026, 10, 20), end=date(2026, 10, 26))
    make().fetch(request)
    make().fetch(request)  # a new provider instance (the registry opens one per call): settled days are not re-billed
    assert route.call_count == 1
    recent = _req("BQT.TOP.EKONOMI.SAYI", start=date(2026, 11, 1), end=TODAY)
    make().fetch(recent)
    make().fetch(recent)  # recent days may still change upstream: asked again
    assert route.call_count == 3


@respx.mock
def test_shared_chunk_cache_is_bounded(make, creds, monkeypatch):
    monkeypatch.setattr(bigquery_trends, "_MAX_SHARED_CHUNKS", 1)
    respx.post(TOKEN_URL).mock(return_value=token_ok())
    respx.post(QUERY_URL).mock(return_value=bq(LIST_COLUMNS, []))
    make().fetch(_req("BQT.TOP.EKONOMI.SAYI", start=date(2026, 10, 6), end=date(2026, 10, 19)))
    assert len(bigquery_trends._SHARED_CHUNKS) == 1
