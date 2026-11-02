"""EPİAŞ provider against respx mocks built from the documented formats (swagger.json, CAS REST guide) and the
error bodies observed live without credentials on 2026-11-02."""

import json
from dataclasses import replace
from datetime import date, timedelta
from urllib.parse import parse_qsl

import httpx
import pandas as pd
import pytest
import respx

from mlbooster.application.dto import FetchRequest
from mlbooster.application.errors import (
    AuthenticationFailed,
    CredentialsMissing,
    ProviderError,
    ProviderUnavailable,
    UnexpectedResponse,
)
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.epias import TICKET_URL, EpiasProvider, TicketCache, parse_remote
from mlbooster.infrastructure.providers.registry import PROVIDERS, ProviderRegistry
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.storage.raw_store import RawStore

DATA = "https://seffaflik.epias.com.tr/electricity-service/v1/"
PTF_URL = DATA + "markets/dam/data/mcp"
SMF_URL = DATA + "markets/bpm/data/system-marginal-price"
CONSUMPTION_URL = DATA + "consumption/data/realtime-consumption"
GENERATION_URL = DATA + "generation/data/realtime-generation"
USERNAME = "kullanici@example.com"
PASSWORD = "s3cret-Pass-0123"
TGT = "TGT-1-abcDEF0123456789-giris"
TGT2 = "TGT-2-zyx9876543210ABC-giris"
AUTH002 = {
    "status": "401 UNAUTHORIZED", "userName": None,
    "errors": [{"errorCode": "AUTH002", "errorMessage": "Kimlik doğrulama(authentication) bilgisi bulunamadı."}], "body": {},
}  # fmt: skip
AUTH009 = {**AUTH002, "errors": [{"errorCode": "AUTH009", "errorMessage": "Güvenlik bilgisi(TGT) hatalı!"}]}
GEN_406 = "Sorguya erişebilmeniz için TGT göndermeniz gerekmektedir."


def hours(day: date, n: int = 24, **fields) -> list[dict]:
    """n hourly items of one day; a field value may be a constant or a function of the hour."""
    return [
        {"date": f"{day.isoformat()}T{h:02d}:00:00+03:00", "hour": f"{h:02d}:00",
         **{k: (v(h) if callable(v) else v) for k, v in fields.items()}}
        for h in range(n)
    ]  # fmt: skip


def _ok(items: list[dict], **extra) -> httpx.Response:
    return httpx.Response(200, json={"items": items, **extra})


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("EPIAS_USERNAME", USERNAME)
    monkeypatch.setenv("EPIAS_PASSWORD", PASSWORD)


@pytest.fixture
def no_creds(monkeypatch):
    monkeypatch.delenv("EPIAS_USERNAME", raising=False)
    monkeypatch.delenv("EPIAS_PASSWORD", raising=False)


@pytest.fixture
def tickets():
    return TicketCache()


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def make(catalog, tickets, sleeps):
    def factory(source=None, raw_store=None, cache=None):
        return EpiasProvider(
            source or catalog.source("epias"),
            http=SecureHttpClient(rps=None, max_retries=1, sleep=lambda _: None),
            secrets=EnvSecretProvider(),
            raw_store=raw_store,
            tickets=cache or tickets,
            sleep=sleeps.append,
        )

    return factory


@pytest.fixture
def epias(make):
    provider = make()
    yield provider
    provider.close()


def _ticket_route(mock, *responses):
    return mock.post(TICKET_URL).mock(side_effect=list(responses) or [httpx.Response(201, text=TGT)])


def _req(code, start, end, **kw):
    return FetchRequest.create("epias", [code] if isinstance(code, str) else code, start, end, **kw)


# --- Catalog, hosts ---------------------------------------------------------------------


def test_registered_and_catalog_points_to_adapter(catalog):
    source = catalog.source("epias")
    assert PROVIDERS[source.provider] is EpiasProvider
    assert source.canaries and all(parse_remote(s.remote) for s in source.series)
    assert source.auth.env_vars == ("EPIAS_USERNAME", "EPIAS_PASSWORD") and source.auth.ticket_url == TICKET_URL


def test_registry_creates_provider(catalog, no_creds):
    provider = ProviderRegistry(EnvSecretProvider()).create(catalog.source("epias"))
    assert isinstance(provider, EpiasProvider)
    provider.close()


def test_refuses_untrusted_data_host(catalog):
    hijacked = replace(catalog.source("epias"), base_url="https://evil.example/electricity-service/")
    with pytest.raises(ProviderError, match="yalnızca"):
        EpiasProvider(hijacked, http=SecureHttpClient(), secrets=EnvSecretProvider())


def test_refuses_untrusted_ticket_host(catalog):
    source = catalog.source("epias")
    hijacked = replace(source, auth=replace(source.auth, ticket_url="https://evil.example/cas/v1/tickets"))
    with pytest.raises(ProviderError, match="giris.epias.com.tr"):
        EpiasProvider(hijacked, http=SecureHttpClient(), secrets=EnvSecretProvider())


def test_parse_remote():
    assert parse_remote("dam-mcp/price") == ("dam-mcp", "price")
    assert parse_remote("realtime-generation/by_source") == ("realtime-generation", "by_source")


@pytest.mark.parametrize(
    "remote", ["dam-mcp", "dam-mcp/", "unknown/price", "dam-mcp/total", "dam-mcp/by_source", "../x/price", "DAM/price"]
)
def test_parse_remote_rejects(remote):
    with pytest.raises(InvalidInput):
        parse_remote(remote)


def test_list_series_filters_by_query(epias):
    assert set(epias.list_series()["series_id"]) >= {"EPIAS.PTF", "EPIAS.SMF", "EPIAS.TUKETIM", "EPIAS.URETIM"}
    assert list(epias.list_series("smf")["series_id"]) == ["EPIAS.SMF"]
    assert set(epias.list_series("rüzgar")["series_id"]) == {"EPIAS.URETIM.RUZGAR"}


# --- Credentials and tickets ------------------------------------------------------------


@respx.mock(assert_all_called=False)
def test_missing_credentials_fail_before_any_request(respx_mock, epias, no_creds):
    route = respx_mock.route()
    with pytest.raises(CredentialsMissing, match="EPIAS_USERNAME"):
        epias.fetch(_req("EPIAS.PTF", date(2026, 10, 1), date(2026, 10, 1)))
    assert not route.called


def test_healthcheck_skipped_without_credentials(epias, no_creds):
    result = epias.healthcheck()
    assert result.status is HealthStatus.SKIPPED and "EPIAS_USERNAME" in result.detail


@respx.mock
def test_credentials_only_in_ticket_body_and_tgt_only_in_header(respx_mock, make, creds, tmp_path):
    raw = RawStore(tmp_path / "raw")
    tickets = _ticket_route(respx_mock)
    data = respx_mock.post(PTF_URL).mock(return_value=_ok(hours(date(2026, 10, 1), price=2500.0)))
    df = make(raw_store=raw).fetch(_req("EPIAS.PTF", date(2026, 10, 1), date(2026, 10, 1)))
    assert df["value"].tolist() == [2500.0]

    ticket_request = tickets.calls.last.request
    assert dict(parse_qsl(ticket_request.content.decode())) == {"username": USERNAME, "password": PASSWORD}
    assert ticket_request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert ticket_request.headers["accept"] == "text/plain"
    assert USERNAME not in str(ticket_request.url) and PASSWORD not in str(ticket_request.url)

    data_request = data.calls.last.request
    assert data_request.headers["TGT"] == TGT
    assert json.loads(data_request.content) == {
        "startDate": "2026-10-01T00:00:00+03:00",
        "endDate": "2026-10-01T00:00:00+03:00",
    }
    for secret in (TGT, USERNAME, PASSWORD):
        assert secret not in str(data_request.url) and secret.encode() not in data_request.content
        for path in (tmp_path / "raw").rglob("*"):
            assert path.is_dir() or secret.encode() not in path.read_bytes()
    assert any((tmp_path / "raw").rglob("*")), "raw response must be kept in bronze"


@respx.mock
def test_ticket_is_shared_between_instances_and_renewed_after_ttl(respx_mock, make, catalog, creds):
    now = [1000.0]
    cache = TicketCache(clock=lambda: now[0])
    tickets = _ticket_route(respx_mock, httpx.Response(201, text=TGT), httpx.Response(201, text=TGT2))
    data = respx_mock.post(PTF_URL).mock(return_value=_ok(hours(date(2026, 10, 1), price=1.0)))
    request = _req("EPIAS.PTF", date(2026, 10, 1), date(2026, 10, 1))
    make(cache=cache).fetch(request)
    make(cache=cache).fetch(request)
    assert tickets.call_count == 1
    now[0] += 2 * 3600 - 600 + 1  # documented 2 h lifetime minus the renewal margin
    make(cache=cache).fetch(request)
    assert tickets.call_count == 2 and data.calls.last.request.headers["TGT"] == TGT2


@respx.mock
def test_rejected_cached_ticket_is_renewed_once(respx_mock, epias, creds):
    tickets = _ticket_route(respx_mock, httpx.Response(201, text=TGT), httpx.Response(201, text=TGT2))
    data = respx_mock.post(PTF_URL).mock(
        side_effect=[httpx.Response(401, json=AUTH009), _ok(hours(date(2026, 10, 1), price=3.0))]
    )
    df = epias.fetch(_req("EPIAS.PTF", date(2026, 10, 1), date(2026, 10, 1)))
    assert df["value"].tolist() == [3.0]
    assert tickets.call_count == 2 and [c.request.headers["TGT"] for c in data.calls] == [TGT, TGT2]


@respx.mock
def test_persistent_auth_error_is_reported_without_secrets(respx_mock, epias, creds):
    _ticket_route(respx_mock, httpx.Response(201, text=TGT), httpx.Response(201, text=TGT2))
    echo = {**AUTH009, "errors": [{"errorCode": "AUTH009", "errorMessage": f"TGT {TGT2} hatalı ({USERNAME})"}]}
    respx_mock.post(PTF_URL).mock(return_value=httpx.Response(401, json=echo))
    with pytest.raises(AuthenticationFailed, match="AUTH009") as err:
        epias.fetch(_req("EPIAS.PTF", date(2026, 10, 1), date(2026, 10, 1)))
    assert TGT2 not in str(err.value) and USERNAME not in str(err.value)


@respx.mock
def test_generation_406_without_valid_ticket_is_an_auth_error(respx_mock, epias, creds):
    _ticket_route(respx_mock, httpx.Response(201, text=TGT), httpx.Response(201, text=TGT2))
    respx_mock.post(GENERATION_URL).mock(return_value=httpx.Response(406, text=GEN_406))
    with pytest.raises(AuthenticationFailed, match="TGT göndermeniz"):
        epias.fetch(_req("EPIAS.URETIM", date(2026, 10, 1), date(2026, 10, 1)))


@respx.mock
def test_other_406_is_a_provider_error(respx_mock, epias, creds):
    _ticket_route(respx_mock)
    respx_mock.post(GENERATION_URL).mock(return_value=httpx.Response(406, text="Not acceptable"))
    with pytest.raises(ProviderError, match="HTTP 406"):
        epias.fetch(_req("EPIAS.URETIM", date(2026, 10, 1), date(2026, 10, 1)))


@pytest.mark.parametrize(
    ("response", "error", "match"),
    [
        (httpx.Response(401, text="Unauthorized"), AuthenticationFailed, "şifresi kabul edilmedi"),
        (httpx.Response(400, text=f"bad request {PASSWORD}"), ProviderError, "HTTP 400"),
        (
            httpx.Response(302, headers={"location": "https://giris.epias.com.tr/cas/"}),
            UnexpectedResponse,
            "yönlendirme",
        ),
        (httpx.Response(201, text="<html>login</html>"), UnexpectedResponse, "TGT biçiminde"),
        (httpx.Response(201, text=""), UnexpectedResponse, "TGT biçiminde"),
    ],
)
@respx.mock
def test_ticket_errors(respx_mock, epias, creds, response, error, match):
    _ticket_route(respx_mock, response)
    data = respx_mock.post(PTF_URL)
    with pytest.raises(error, match=match) as err:
        epias.fetch(_req("EPIAS.PTF", date(2026, 10, 1), date(2026, 10, 1)))
    assert PASSWORD not in str(err.value) and not data.called


# --- Data -------------------------------------------------------------------------------


@respx.mock
def test_hourly_prices_become_daily_means_and_incomplete_days_are_dropped(respx_mock, epias, creds):
    _ticket_route(respx_mock)
    items = hours(date(2026, 9, 30), price=1.0)  # outside the requested range
    items += hours(date(2026, 10, 1), price=lambda h: 100.0 + h, priceUsd=2.5)
    items += hours(date(2026, 10, 2), n=12, price=7.0)  # incomplete (e.g. today)
    respx_mock.post(PTF_URL).mock(return_value=_ok(items))
    df = epias.fetch(_req(["EPIAS.PTF", "EPIAS.PTF.USD"], date(2026, 10, 1), date(2026, 10, 2)))
    assert respx_mock.calls.call_count == 2  # one ticket, one data request for both series of the same service
    rows = df.set_index("series_id")
    assert rows.loc["EPIAS.PTF", "value"] == pytest.approx(111.5)
    assert rows.loc["EPIAS.PTF.USD", "value"] == 2.5
    assert set(df["date"]) == {date(2026, 10, 1)}
    assert rows.loc["EPIAS.PTF", "unit"] == "try_per_mwh" and rows.loc["EPIAS.PTF.USD", "currency"] == "USD"
    assert set(df["freq"]) == {"D"} and set(df["metric"]) == {"level"} and set(df["entity_id"]) == {""}
    assert set(df["dataset_id"]) == {"dam-mcp"} and not df["is_proxy"].any()


@respx.mock
def test_consumption_is_summed_and_23_hour_days_are_kept(respx_mock, epias, creds):
    _ticket_route(respx_mock)
    items = hours(date(2016, 3, 27), n=23, consumption=1000.0) + hours(
        date(2016, 3, 28), consumption=lambda h: float(h)
    )
    items += [dict(items[-1])]  # a repeated hour counts once
    respx_mock.post(CONSUMPTION_URL).mock(return_value=_ok(items, statistics={"consumptionTotal": 0}))
    df = epias.fetch(_req("EPIAS.TUKETIM", date(2016, 3, 27), date(2016, 3, 28)))
    assert df.set_index("date")["value"].to_dict() == {date(2016, 3, 27): 23000.0, date(2016, 3, 28): 276.0}
    assert set(df["unit"]) == {"mwh"}


@respx.mock
def test_smf_and_null_hours(respx_mock, epias, creds):
    _ticket_route(respx_mock)
    items = hours(date(2026, 10, 1), systemMarginalPrice=lambda h: None if h == 3 else 10.0)  # 23 values
    items += hours(date(2026, 10, 2), systemMarginalPrice=lambda h: None if h < 2 else 4.0)  # 22 values
    respx_mock.post(SMF_URL).mock(return_value=_ok(items))
    df = epias.fetch(_req("EPIAS.SMF", date(2026, 10, 1), date(2026, 10, 2)))
    assert df.set_index("date")["value"].to_dict() == {date(2026, 10, 1): 10.0}


@respx.mock
def test_generation_by_source_writes_total_and_source_entities(respx_mock, epias, creds):
    _ticket_route(respx_mock)
    day = date(2026, 10, 1)
    items = hours(day, total=100.0, naturalGas=40.0, wind=35.0, sun=lambda h: 5.0 if 6 <= h <= 18 else 0.0,
                  importExport=-3.0, nuclear=10.0, note="x")  # fmt: skip
    respx_mock.post(GENERATION_URL).mock(return_value=_ok(items))
    df = epias.fetch(_req(["EPIAS.URETIM.KAYNAK", "EPIAS.URETIM.RUZGAR"], day, day))
    mix = df[df["series_id"] == "EPIAS.URETIM.KAYNAK"].set_index("entity_id")
    assert mix["value"].to_dict() == {"": 2400.0, "naturalGas": 960.0, "nuclear": 240.0, "sun": 65.0, "wind": 840.0}
    assert mix.loc["wind", "entity_name"] == "Rüzgâr" and mix.loc["nuclear", "entity_name"] == "Nükleer"
    assert pd.isna(mix.loc["", "entity_name"])
    assert df[df["series_id"] == "EPIAS.URETIM.RUZGAR"]["value"].tolist() == [840.0]


@respx.mock
def test_long_ranges_are_chunked(respx_mock, epias, creds):
    _ticket_route(respx_mock)
    data = respx_mock.post(PTF_URL).mock(return_value=_ok([]))
    df = epias.fetch(_req("EPIAS.PTF", date(2026, 1, 1), date(2026, 7, 19)))  # 200 days, 93 days per call
    assert df.empty
    bodies = [json.loads(c.request.content) for c in data.calls]
    assert [(b["startDate"][:10], b["endDate"][:10]) for b in bodies] == [
        ("2026-01-01", "2026-04-03"), ("2026-04-04", "2026-07-05"), ("2026-07-06", "2026-07-19"),
    ]  # fmt: skip


@respx.mock
def test_throttled_request_waits_30_seconds_once(respx_mock, epias, creds, sleeps):
    _ticket_route(respx_mock)
    throttled = httpx.Response(429, text="Too Many Requests")
    respx_mock.post(PTF_URL).mock(side_effect=[throttled, throttled, _ok(hours(date(2026, 10, 1), price=1.0))])
    df = epias.fetch(_req("EPIAS.PTF", date(2026, 10, 1), date(2026, 10, 1)))
    assert sleeps == [30] and df["value"].tolist() == [1.0]


@respx.mock
def test_server_errors_are_not_waited_for(respx_mock, epias, creds, sleeps):
    _ticket_route(respx_mock)
    respx_mock.post(PTF_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(ProviderUnavailable):
        epias.fetch(_req("EPIAS.PTF", date(2026, 10, 1), date(2026, 10, 1)))
    assert sleeps == []


@pytest.mark.parametrize(
    ("response", "error", "match"),
    [
        (
            httpx.Response(400, json={"errors": [{"errorCode": "VAL001", "errorMessage": "Tarih aralığı hatalı"}]}),
            ProviderError,
            "Tarih aralığı",
        ),
        (
            httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"}),
            UnexpectedResponse,
            "JSON yerine",
        ),
        (
            httpx.Response(200, text="{oops", headers={"content-type": "application/json"}),
            UnexpectedResponse,
            "çözümlenemedi",
        ),
        (httpx.Response(200, json=[1, 2]), UnexpectedResponse, "nesne"),
        (httpx.Response(200, json={"items": {"a": 1}}), UnexpectedResponse, "items"),
        (httpx.Response(200, json={"items": [], "page": {"total": 5}}), UnexpectedResponse, "sayfalanmış"),
        (
            httpx.Response(301, headers={"location": "https://seffaflik.epias.com.tr/"}),
            UnexpectedResponse,
            "yönlendirme",
        ),
        (_ok([{"hour": "00:00", "price": 1.0}]), UnexpectedResponse, "'date'"),
        (_ok([{"date": "01.10.2026 00:00", "price": 1.0}]), UnexpectedResponse, "tarih biçimi"),
        (_ok(hours(date(2026, 10, 1), priceUsd=1.0)), UnexpectedResponse, "'price' alanı yok"),
        (_ok(hours(date(2026, 10, 1), price="1,5")), UnexpectedResponse, "sayısal olmayan"),
    ],
)
@respx.mock
def test_unexpected_responses(respx_mock, epias, creds, response, error, match):
    _ticket_route(respx_mock)
    respx_mock.post(PTF_URL).mock(return_value=response)
    with pytest.raises(error, match=match):
        epias.fetch(_req("EPIAS.PTF", date(2026, 10, 1), date(2026, 10, 1)))


@respx.mock
def test_items_inside_envelope_body_are_read(respx_mock, epias, creds):
    _ticket_route(respx_mock)
    respx_mock.post(PTF_URL).mock(
        return_value=httpx.Response(200, json={"body": {"items": hours(date(2026, 10, 1), price=2.0)}})
    )
    assert epias.fetch(_req("EPIAS.PTF", date(2026, 10, 1), date(2026, 10, 1)))["value"].tolist() == [2.0]


@pytest.mark.parametrize("kw", [{"formula": 1}, {"agg": "avg"}, {"freq": "M"}, {"freq": "H"}])
def test_rejects_transformations(epias, creds, kw):
    with pytest.raises(InvalidInput, match="günlük"):
        epias.fetch(_req("EPIAS.PTF", date(2026, 10, 1), date(2026, 10, 1), **kw))


def test_unknown_series(epias, creds):
    with pytest.raises(InvalidInput, match="bilinmeyen seri"):
        epias.fetch(_req("EPIAS.YOK", date(2026, 10, 1), date(2026, 10, 1)))


# --- Health -----------------------------------------------------------------------------


@respx.mock
def test_healthcheck_ok(respx_mock, epias, creds):
    _ticket_route(respx_mock)
    yesterday = date.today() - timedelta(days=1)
    respx_mock.post(PTF_URL).mock(return_value=_ok(hours(yesterday, price=2900.0)))
    result = epias.healthcheck()
    assert result.status is HealthStatus.OK and "EPIAS.PTF" in result.detail and result.checked == ("EPIAS.PTF",)


@pytest.mark.parametrize(
    ("response", "match"),
    [
        (_ok([]), "gözlem yok"),
        (_ok(hours(date.today() - timedelta(days=14), price=1.0)), "gün önce"),
        (httpx.Response(401, json=AUTH002), "AuthenticationFailed"),
    ],
)
@respx.mock
def test_healthcheck_fail(respx_mock, epias, creds, response, match):
    _ticket_route(respx_mock, httpx.Response(201, text=TGT), httpx.Response(201, text=TGT2))
    respx_mock.post(PTF_URL).mock(return_value=response)
    result = epias.healthcheck()
    assert result.status is HealthStatus.FAIL and match in result.detail


def test_healthcheck_without_canaries(make, catalog, creds):
    source = catalog.source("epias")
    seeds = tuple(replace(s, canary=False) for s in source.series)
    assert make(source=replace(source, series=seeds)).healthcheck().status is HealthStatus.SKIPPED


def test_ticket_cache_drop_and_clear():
    from mlbooster.infrastructure.secret_store import Secret

    cache = TicketCache(clock=lambda: 0.0)
    cache.put("a", Secret(TGT), 10)
    cache.put("b", Secret(TGT2), 10)
    cache.drop("a")
    assert cache.get("a") is None and cache.get("b").reveal() == TGT2
    cache.clear()
    assert cache.get("b") is None
