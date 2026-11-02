import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from mlbooster.application.dto import FetchRequest
from mlbooster.application.errors import ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.registry import PROVIDERS
from mlbooster.infrastructure.providers.tbb import TbbProvider, parse_remote
from mlbooster.infrastructure.storage.raw_store import RawStore

# Responses recorded from verisistemi.tbb.org.tr (/api/router) on 2026-11-02; bank lists trimmed to Ziraat (3),
# Akbank (8) and İş Bankası (32), bank records trimmed to the fields that are read.
FIXTURES = Path(__file__).parent / "fixtures" / "tbb"
URL = "https://verisistemi.tbb.org.tr/api/router"
PERIOD_KEYS = {(2017, 12): -1448273060, (2018, 3): 1477303129, (2025, 12): -1447409121, (2026, 3): 1477330998, (2026, 6): 1477331001}  # fmt: skip


def _fixture(name: str) -> list:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _body(call) -> dict:
    return json.loads(call.request.content)


def _router(values: str = "degerler_aktif_2026.json", **overrides):
    """respx side effect answering by the `route` field of the JSON body, like the report page's endpoint."""
    answers = {"donemler": "donemler.json", "bankalar": "bankalar.json", "bankaGruplari": "bankaGruplari.json", "degerler": values}

    def handler(request: httpx.Request) -> httpx.Response:
        route = json.loads(request.content)["route"]
        if route in overrides:
            return overrides[route]
        return httpx.Response(200, json=_fixture(answers[route]))

    return handler


@pytest.fixture
def tbb(catalog):
    provider = TbbProvider(catalog.source("tbb"), http=SecureHttpClient(rps=None, max_retries=1, sleep=lambda _: None))
    yield provider
    provider.close()


def test_registered_and_catalog_points_to_adapter(catalog):
    source = catalog.source("tbb")
    assert PROVIDERS[source.provider] is TbbProvider
    assert source.canaries and all(parse_remote(s.remote) for s in source.series)
    assert source.verification.value == "verified" and source.license.commercial is False


def test_refuses_untrusted_host(catalog):
    hijacked = replace(catalog.source("tbb"), base_url="https://evil.example/api/router")
    with pytest.raises(ProviderError, match="yalnızca"):
        TbbProvider(hijacked, http=SecureHttpClient())


def test_parse_remote():
    assert parse_remote("5/16018+2087") == (5, (16018, 2087), "TOPLAM")
    assert parse_remote("5/102152.TP") == (5, (102152,), "TP_DEGER")
    assert parse_remote("5/1+2+3.YP")[2] == "YP_DEGER"


@pytest.mark.parametrize("remote", ["16018", "5/abc", "5/16018+16018", "5/16018.Toplam", "x/16018", "1/16005+2098", "5/../1"])
def test_parse_remote_rejects_malformed(remote):
    with pytest.raises(InvalidInput, match="remote"):
        parse_remote(remote)


@respx.mock
def test_fetch_maps_banks_to_entities_and_group_to_total(tbb, tmp_path):
    tbb._raw_store = RawStore(tmp_path)
    route = respx.post(URL).mock(side_effect=_router())
    df = tbb.fetch(FetchRequest.create("tbb", ["TBB.AKTIF"], date(2025, 10, 1), date(2026, 10, 31)))

    assert [_body(c)["route"] for c in route.calls] == ["donemler", "bankalar", "bankaGruplari", "degerler"]
    request = route.calls.last.request
    assert request.headers["LANG"] == "tr" and request.headers["content-type"] == "application/json"
    assert _body(route.calls.last) == {
        "route": "degerler", "donemler": [PERIOD_KEYS[(2025, 12)], PERIOD_KEYS[(2026, 3)], PERIOD_KEYS[(2026, 6)]],
        "maliTablolar": [2087, 16018], "bankalar": [3, 8, 32], "bankaGruplari": [5],
    }  # fmt: skip

    jun = df[df["date"] == date(2026, 6, 30)].set_index("entity_id")
    assert jun.loc["", "value"] == 47776632  # million TL: 47.78 trillion TL, Türkiye Bankacılık Sistemi
    assert jun.loc["3", "value"] == 9076507 and jun.loc["8", "value"] == 3727680 and jun.loc["32", "value"] == 5091171
    names = dict(zip(df["entity_id"], df["entity_name"], strict=True))
    assert names == {
        "": "Türkiye Bankacılık Sistemi", "3": "Türkiye Cumhuriyeti Ziraat Bankası A.Ş.", "8": "Akbank T.A.Ş.",
        "32": "Türkiye İş Bankası A.Ş.",
    }  # fmt: skip
    assert sorted(set(df["date"])) == [date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)]
    assert set(df["dataset_id"]) == {"mali_tablolar_solo"} and set(df["unit"]) == {"million_try"}
    assert set(df["freq"]) == {"Q"} and set(df["metric"]) == {"level"} and set(df["currency"]) == {"TRY"}
    assert not df["is_proxy"].any() and set(df["publication_lag_days"]) == {120}
    assert len(df) == 3 * 4
    manifest = [json.loads(line) for line in (tmp_path / "tbb" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(manifest) == 4 and '"route":"degerler"' in manifest[-1]["url"]


@respx.mock
def test_older_statement_keys_fill_periods_before_tfrs9(tbb):
    respx.post(URL).mock(side_effect=_router("degerler_kredi_2018.json"))
    df = tbb.fetch(FetchRequest.create("tbb", ["TBB.KREDI"], date(2017, 10, 1), date(2018, 3, 31)))
    total = df[df["entity_id"] == ""].set_index("date")["value"]
    assert total.to_dict() == {date(2017, 12, 31): 2071375.707, date(2018, 3, 31): 2156752.738}  # 2098 → 16005
    ziraat = df[df["entity_id"] == "3"].set_index("date")["value"]
    assert ziraat.to_dict() == {date(2017, 12, 31): 298258.032, date(2018, 3, 31): 312876.675}


@respx.mock
def test_tp_column(tbb, catalog):
    source = replace(catalog.source("tbb"), series=(replace(catalog.source("tbb").seed("TBB.AKTIF"), remote="5/16018.TP"),))
    provider = TbbProvider(source, http=SecureHttpClient(rps=None, max_retries=1, sleep=lambda _: None))
    respx.post(URL).mock(side_effect=_router())
    df = provider.fetch(FetchRequest.create("tbb", ["TBB.AKTIF"], date(2026, 6, 1), date(2026, 6, 30)))
    assert df.set_index("entity_id").loc["", "value"] == 30665644  # TL part of 47,776,632 million TL


@respx.mock
def test_periods_are_requested_in_chunks(tbb, monkeypatch):
    monkeypatch.setattr("mlbooster.infrastructure.providers.tbb.PERIODS_PER_CALL", 2)
    route = respx.post(URL).mock(side_effect=_router())
    tbb.fetch(FetchRequest.create("tbb", ["TBB.AKTIF"], date(2025, 10, 1), date(2026, 6, 30)))
    chunks = [_body(c)["donemler"] for c in route.calls if _body(c)["route"] == "degerler"]
    assert chunks == [[PERIOD_KEYS[(2025, 12)], PERIOD_KEYS[(2026, 3)]], [PERIOD_KEYS[(2026, 6)]]]


@respx.mock
def test_range_without_published_quarters_makes_no_value_requests(tbb):
    route = respx.post(URL).mock(side_effect=_router())
    assert tbb.fetch(FetchRequest.create("tbb", ["TBB.AKTIF"], date(2026, 7, 1), date(2026, 10, 31))).empty
    assert [_body(c)["route"] for c in route.calls] == ["donemler"]


@respx.mock
def test_missing_values_are_skipped(tbb):
    rows = [r for r in _fixture("degerler_aktif_2026.json") if r["BANKA_KODU"] != 8 or r["BANKA_GRUP"]]
    rows[0] = {**rows[0], "TOPLAM": None}
    respx.post(URL).mock(side_effect=_router(degerler=httpx.Response(200, json=rows)))
    df = tbb.fetch(FetchRequest.create("tbb", ["TBB.AKTIF"], date(2025, 10, 1), date(2026, 6, 30)))
    assert "8" not in set(df["entity_id"]) and len(df) == 3 * 3 - 1


@respx.mock
def test_empty_value_answer_returns_no_rows(tbb):
    respx.post(URL).mock(side_effect=_router(degerler=httpx.Response(200, json=[])))
    assert tbb.fetch(FetchRequest.create("tbb", ["TBB.AKTIF"], date(2025, 10, 1), date(2026, 6, 30))).empty


@respx.mock
@pytest.mark.parametrize(
    ("response", "error", "match"),
    [
        (httpx.Response(302, headers={"location": "https://verisistemi.tbb.org.tr/"}), UnexpectedResponse, "yönlendirme"),
        (httpx.Response(400, json={"message": "Missing request header 'LANG'"}), ProviderError, "HTTP 400"),
        (httpx.Response(200, text="<html>FORBIDDEN</html>", headers={"content-type": "text/html"}), UnexpectedResponse, "JSON yerine"),
        (httpx.Response(200, text="asd", headers={"content-type": "application/json;charset=utf-8"}), UnexpectedResponse, "çözümlenemedi"),
        (httpx.Response(200, json={"items": []}), UnexpectedResponse, "liste"),
        (httpx.Response(200, json=[{"YIL": 2026}]), UnexpectedResponse, "dönem listesi"),
    ],
)
def test_bad_period_answers(tbb, response, error, match):
    respx.post(URL).mock(side_effect=_router(donemler=response))
    with pytest.raises(error, match=match):
        tbb.fetch(FetchRequest.create("tbb", ["TBB.AKTIF"], date(2026, 1, 1), date(2026, 6, 30)))


@respx.mock
@pytest.mark.parametrize(
    ("route", "rows", "match"),
    [
        ("bankalar", [{"TR_ADI": "Banka"}], "banka listesi"),
        ("bankaGruplari", [{"GRUP_NO": "x", "TR_ADI": "Grup"}], "banka grubu listesi"),
        ("degerler", [{"TOPLAM": 1}], "değer satırları"),
        ("degerler", [{**_fixture("degerler_aktif_2026.json")[0], "TOPLAM": "47.776"}], "sayısal olmayan"),
    ],
)
def test_changed_answer_layout_is_reported(tbb, route, rows, match):
    respx.post(URL).mock(side_effect=_router(**{route: httpx.Response(200, json=rows)}))
    with pytest.raises(UnexpectedResponse, match=match):
        tbb.fetch(FetchRequest.create("tbb", ["TBB.AKTIF"], date(2026, 1, 1), date(2026, 6, 30)))


def test_unknown_series_and_transforms_rejected(tbb):
    with pytest.raises(InvalidInput, match="bilinmeyen seri"):
        tbb.fetch(FetchRequest.create("tbb", ["TBB.YOK"], date(2026, 1, 1)))
    with pytest.raises(InvalidInput, match="desteklenmiyor"):
        tbb.fetch(FetchRequest.create("tbb", ["TBB.AKTIF"], date(2026, 1, 1), formula=1))


def test_list_series_filters_catalog(tbb):
    assert "TBB.KREDI" in tbb.list_series()["series_id"].tolist()
    assert set(tbb.list_series("özkaynak")["series_id"]) == {"TBB.OZKAYNAK"}


def _published_until(last: date):
    """Period list cut after `last`, values recorded for June 2026 answered for whatever quarter is asked."""
    june = _fixture("degerler_aktif_2026.json")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["route"] == "donemler":
            periods = [p for p in _fixture("donemler.json") if (p["YIL"], p["AY"]) <= (last.year, last.month)]
            return httpx.Response(200, json=periods)
        if body["route"] == "degerler":
            rows = [{**r, "DONEM_KEY": key} for key in body["donemler"] for r in june if r["DONEM_KEY"] == PERIOD_KEYS[(2026, 6)]]
            return httpx.Response(200, json=rows)
        return _router()(request)

    return handler


@respx.mock
def test_healthcheck_ok(tbb, monkeypatch):
    class _Today(date):
        @classmethod
        def today(cls):
            return date(2026, 11, 2)

    monkeypatch.setattr("mlbooster.infrastructure.providers.tbb.date", _Today)
    respx.post(URL).mock(side_effect=_published_until(date(2026, 6, 30)))
    result = tbb.healthcheck()
    assert result.status is HealthStatus.OK, result.detail
    assert "TBB.AKTIF son=2026-06-30" in result.detail and "banka=3" in result.detail and result.checked == ("TBB.AKTIF",)


@respx.mock
def test_healthcheck_fails_on_stale_series(tbb, monkeypatch):
    monkeypatch.setattr("mlbooster.infrastructure.providers.tbb._HEALTH_LOOKBACK_DAYS", 900)
    respx.post(URL).mock(side_effect=_published_until(date.today() - timedelta(days=400)))
    result = tbb.healthcheck()
    assert result.status is HealthStatus.FAIL and "son gözlem" in result.detail


@respx.mock
def test_healthcheck_fails_without_recent_observations(tbb):
    respx.post(URL).mock(side_effect=_router(degerler=httpx.Response(200, json=[])))
    result = tbb.healthcheck()
    assert result.status is HealthStatus.FAIL and "gözlem yok" in result.detail


@respx.mock
def test_healthcheck_reports_provider_errors(tbb):
    respx.post(URL).mock(return_value=httpx.Response(403, text="Forbidden"))
    result = tbb.healthcheck()
    assert result.status is HealthStatus.FAIL and "HTTP 403" in result.detail


def test_healthcheck_skipped_without_canaries(catalog):
    source = replace(catalog.source("tbb"), series=())
    assert TbbProvider(source, http=SecureHttpClient()).healthcheck().status is HealthStatus.SKIPPED
