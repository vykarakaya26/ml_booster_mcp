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


def _req(code="TP.TUKFIY2025.GENEL", start=date(2026, 8, 1), end=date(2026, 10, 30), **kw):
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
        return_value=httpx.Response(200, json=_items("TP.TUKFIY2025.GENEL", [("2026-8", "3500.1"), ("2026-9", "3550.2"), ("2026-10", None)]))
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
            return httpx.Response(200, json={"items": [{"Tarih": "2026-9", "TP_TUKFIY2025_GENEL-3": "33.3", "TP_TUKFIY2025_GENEL": None}]})
        return httpx.Response(200, json=_items("TP.TUKFIY2025.GENEL", [("2026-9", "3550.2")]))

    route = respx.get(url__startswith=BASE).mock(side_effect=responder)
    df = evds.fetch(_req(start=date(2026, 9, 1), end=date(2026, 9, 30), formula=3))
    assert route.call_count == 2
    assert dict(zip(df["metric"], df["value"])) == {"level": 3550.2, "yoy_pct": 33.3}


@respx.mock
def test_chunked_fetch_deduplicates(evds):
    respx.get(url__startswith=BASE).mock(return_value=httpx.Response(200, json=_items("TP.DK.USD.A", [("02-01-2020", "5.9")])))
    df = evds.fetch(_req("TP.DK.USD.A", date(2020, 1, 1), date(2026, 1, 1)))
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
        return_value=httpx.Response(200, json=[{"SERIE_CODE": "TP.DK.USD.A", "SERIE_NAME": "USD alış", "FREQUENCY_STR": "İŞGÜNÜ", "DATAGROUP_CODE": "bie_dkdovizgn"}])
    )
    row = evds.list_series("bie_dkdovizgn").iloc[0]
    assert row["series_id"] == "TP.DK.USD.A" and row["freq"] == "B"


@respx.mock
def test_healthcheck_fails_on_html(evds):
    respx.get(url__startswith=BASE).mock(return_value=httpx.Response(200, text="<html>", headers={"content-type": "text/html"}))
    result = evds.healthcheck()
    assert result.status is HealthStatus.FAIL and "HTML" in result.detail


def test_hourly_frequency_rejected(evds):
    from mlbooster.domain.errors import InvalidInput

    with pytest.raises(InvalidInput, match="frekans"):
        evds.fetch(_req(freq="H"))


def test_registry_shares_rate_limiter_per_source(catalog):
    from mlbooster.infrastructure.providers.registry import ProviderRegistry

    registry = ProviderRegistry(EnvSecretProvider())
    a, b = registry.create(catalog.source("evds3")), registry.create(catalog.source("evds3"))
    assert a._http._limiter is b._http._limiter
    a.close(), b.close()


@respx.mock
def test_healthcheck_fails_on_stale_series(evds):
    # Seri hâlâ veri dönüyor ama aylar önce durmuş (TP.FG.J0'ın 2026-01'de arşive taşınması gibi).
    def responder(request):
        url = str(request.url)
        if "TP.TUKFIY2025.GENEL" in url:
            return httpx.Response(200, json=_items("TP.TUKFIY2025.GENEL", [("2025-1", "100")]))
        return httpx.Response(200, json=_items("TP.DK.USD.A", [(date.today().strftime("%d-%m-%Y"), "49")]))

    respx.get(url__startswith=BASE).mock(side_effect=responder)
    result = evds.healthcheck()
    assert result.status is HealthStatus.FAIL and "durmuş" in result.detail


@pytest.mark.parametrize(("label", "freq"), [("İŞ GÜNÜ", "B"), ("İŞGÜNÜ", "B"), ("ÜÇ AYLIK", "Q"), ("aylık", "M"), ("GÜNLÜK", "D"),
     ("HAFTALIK(CUMA)", "W"), ("HAFTALIK(ÇARŞAMBA)", "W"), ("AYDA İKİ KEZ", "SM")])
def test_freq_labels_normalized(label, freq):
    from mlbooster.infrastructure.providers.evds3 import normalize_freq_label

    assert normalize_freq_label(label) == freq


@respx.mock
def test_search_is_turkish_insensitive_and_ranks_archives_last(evds):
    respx.get(url__startswith=BASE + "datagroups/").mock(
        return_value=httpx.Response(200, json=[
            {"DATAGROUP_CODE": "bie_old", "DATAGROUP_NAME": "Tüketici Kredi Faizleri (Arşiv)", "FREQUENCY_STR": "AYLIK", "END_DATE": "01-01-2020"},
            {"DATAGROUP_CODE": "bie_new", "DATAGROUP_NAME": "Tüketici Kredi Faiz Oranları", "FREQUENCY_STR": "HAFTALIK", "END_DATE": "24-10-2026"},
            {"DATAGROUP_CODE": "bie_fx", "DATAGROUP_NAME": "Döviz Kurları", "FREQUENCY_STR": "GÜNLÜK", "END_DATE": "30-10-2026"},
        ])  # fmt: skip
    )
    df = evds.search("TUKETICI kredi faizi")
    assert list(df["datagroup"]) == ["bie_new", "bie_old"]
    assert df.iloc[0]["freq"] == "W" and bool(df.iloc[1]["archived"])


def test_infer_dmy_frequency():
    from datetime import timedelta

    from mlbooster.infrastructure.providers.evds3 import infer_dmy_frequency

    start = date(2026, 1, 2)  # Cuma
    assert infer_dmy_frequency([start + timedelta(weeks=i) for i in range(10)]) is Frequency.WEEKLY
    bdays = [d for d in (start + timedelta(days=i) for i in range(30)) if d.weekday() < 5]
    assert infer_dmy_frequency(bdays) is Frequency.BUSINESS
    assert infer_dmy_frequency([start + timedelta(days=i) for i in range(30)]) is Frequency.DAILY
    assert infer_dmy_frequency([date(2026, m, d) for m in range(1, 7) for d in (1, 16)]) is Frequency.SEMI_MONTHLY
