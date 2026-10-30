from dataclasses import replace
from datetime import date

import httpx
import pytest
import respx

from mlbooster.application.dto import FetchRequest
from mlbooster.application.errors import AuthenticationFailed, CredentialsMissing, ProviderError, UnexpectedResponse
from mlbooster.domain.model import Frequency, HealthStatus
from mlbooster.domain.observations import OBSERVATION_COLUMNS, validate_observations
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.evds3 import Evds3Provider, parse_evds_date
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.storage.raw_store import RawStore

BASE = "https://evds3.tcmb.gov.tr/igmevdsms-dis/"


def _items(code, rows):
    col = code.replace(".", "_")
    return {"totalCount": len(rows), "items": [{"Tarih": d, col: v, "UNIXTIME": {"$numberLong": "0"}} for d, v in rows]}


def _req(code="TP.FG.J0", start=date(2026, 8, 1), end=date(2026, 10, 30), **kw):
    return FetchRequest.create("evds3", [code], start, end, **kw)


@pytest.fixture
def evds(catalog, api_key, tmp_path):
    http = SecureHttpClient(rps=None, max_retries=2, sleep=lambda _: None)
    provider = Evds3Provider(catalog.source("evds3"), http=http, secrets=EnvSecretProvider(), raw_store=RawStore(tmp_path / "bronze"))
    yield provider
    provider.close()


@pytest.mark.parametrize(
    ("raw", "expected", "freq"),
    [
        ("01-10-2026", date(2026, 10, 1), Frequency.DAILY),
        ("2026-2", date(2026, 2, 28), Frequency.MONTHLY),
        ("2024-02", date(2024, 2, 29), Frequency.MONTHLY),
        ("2026-Q3", date(2026, 9, 30), Frequency.QUARTERLY),
        ("2026-S1", date(2026, 6, 30), Frequency.SEMI_ANNUAL),
        ("2025", date(2025, 12, 31), Frequency.ANNUAL),
    ],
)
def test_parse_evds_date(raw, expected, freq):
    assert parse_evds_date(raw) == (expected, freq)


@pytest.mark.parametrize("raw", ["Ekim 2026", "2026-13", "32-01-2026"])
def test_parse_evds_date_rejects_garbage(raw):
    with pytest.raises(UnexpectedResponse):
        parse_evds_date(raw)


def test_refuses_untrusted_host(catalog):
    hijacked = replace(catalog.source("evds3"), base_url="https://attacker.example/igmevdsms-dis/")
    with pytest.raises(ProviderError, match="yalnızca"):
        Evds3Provider(hijacked, http=SecureHttpClient(), secrets=EnvSecretProvider())


@respx.mock
def test_key_only_in_header_never_in_url_or_bronze(evds, api_key, tmp_path):
    route = respx.get(url__startswith=BASE).mock(
        return_value=httpx.Response(200, json=_items("TP.FG.J0", [("2026-8", "3500.1"), ("2026-9", "3550.2"), ("2026-10", None)]))
    )
    df = evds.fetch(_req())
    request = route.calls.last.request
    assert request.headers["key"] == api_key
    assert api_key not in str(request.url) and "?" not in str(request.url)
    assert api_key not in (tmp_path / "bronze" / "evds3" / "manifest.jsonl").read_text()
    assert list(df.columns) == OBSERVATION_COLUMNS
    assert df["value"].tolist() == [3500.1, 3550.2]
    assert df["date"].tolist() == [date(2026, 8, 31), date(2026, 9, 30)]
    assert set(df["metric"]) == {"level"} and set(df["freq"]) == {"M"}
    assert df["publication_lag_days"].iloc[0] == 3
    validate_observations(df)


@respx.mock
def test_formula_also_requests_level(evds):
    def responder(request):
        if "formulas=3" in str(request.url):
            return httpx.Response(200, json={"items": [{"Tarih": "2026-9", "TP_FG_J0-3": "33.3", "TP_FG_J0": None}]})
        return httpx.Response(200, json=_items("TP.FG.J0", [("2026-9", "3550.2")]))

    route = respx.get(url__startswith=BASE).mock(side_effect=responder)
    df = evds.fetch(_req(start=date(2026, 9, 1), end=date(2026, 9, 30), formula=3))
    assert route.call_count == 2
    assert dict(zip(df["metric"], df["value"])) == {"level": 3550.2, "yoy_pct": 33.3}


@respx.mock
def test_chunked_fetch_deduplicates(evds):
    respx.get(url__startswith=BASE).mock(return_value=httpx.Response(200, json=_items("TP.DK.USD.A.YTL", [("02-01-2020", "5.9")])))
    df = evds.fetch(_req("TP.DK.USD.A.YTL", date(2020, 1, 1), date(2026, 1, 1)))
    assert respx.calls.call_count > 1 and len(df) == 1


@pytest.mark.parametrize(
    ("response", "error", "match"),
    [
        (httpx.Response(200, text="<!doctype html>", headers={"content-type": "text/html"}), UnexpectedResponse, "HTML"),
        (httpx.Response(302, headers={"location": "https://evds3.tcmb.gov.tr/"}), UnexpectedResponse, "302"),
        (httpx.Response(200, content=b""), UnexpectedResponse, "boş"),
        (httpx.Response(200, json={"foo": 1}), UnexpectedResponse, "items"),
        (httpx.Response(200, json={"items": [{"Tarih": "2026-1", "OTHER": "1"}]}), UnexpectedResponse, "kolon"),
        (httpx.Response(200, content=b"{not json"), UnexpectedResponse, "JSON"),
    ],
)
@respx.mock
def test_schema_violations_raise(evds, response, error, match):
    respx.get(url__startswith=BASE).mock(return_value=response)
    with pytest.raises(error, match=match):
        evds.fetch(_req(start=date(2026, 1, 1), end=date(2026, 1, 31)))


@respx.mock
def test_auth_error_not_retried_and_echoed_key_redacted(evds, api_key):
    route = respx.get(url__startswith=BASE).mock(return_value=httpx.Response(401, text=f"Invalid API Key {api_key}"))
    with pytest.raises(AuthenticationFailed) as exc:
        evds.fetch(_req())
    assert route.call_count == 1
    assert api_key not in str(exc.value) and "********" in str(exc.value)


def test_missing_key(evds, monkeypatch):
    monkeypatch.delenv("EVDS_API_KEY")
    with pytest.raises(CredentialsMissing):
        evds.fetch(_req())
    assert evds.healthcheck().status is HealthStatus.SKIPPED


@respx.mock
def test_list_series_from_serielist(evds):
    respx.get(url__startswith=BASE + "serieList/").mock(
        return_value=httpx.Response(200, json=[{"SERIE_CODE": "TP.DK.USD.A.YTL", "SERIE_NAME": "USD alış", "FREQUENCY_STR": "İŞGÜNÜ", "DATAGROUP_CODE": "bie_dkdovizgn"}])
    )
    row = evds.list_series("bie_dkdovizgn").iloc[0]
    assert row["series_id"] == "TP.DK.USD.A.YTL" and row["freq"] == "B"


@respx.mock
def test_healthcheck_fails_on_html(evds):
    respx.get(url__startswith=BASE).mock(return_value=httpx.Response(200, text="<html>", headers={"content-type": "text/html"}))
    result = evds.healthcheck()
    assert result.status is HealthStatus.FAIL and "HTML" in result.detail
