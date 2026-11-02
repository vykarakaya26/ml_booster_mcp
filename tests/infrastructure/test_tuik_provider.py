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
from mlbooster.infrastructure.providers.tuik import TuikProvider, parse_period, select_jsonstat, split_remote
from mlbooster.infrastructure.storage.raw_store import RawStore

# Responses recorded from databrowser2.tuik.gov.tr on 2026-11-02 (structure trimmed to the fields used).
FIXTURES = Path(__file__).parent / "fixtures" / "tuik"
API = "https://databrowser2.tuik.gov.tr/api/core/nodes/1/datasets/"
TUFE = API + "TR,DF_TUFE_SDMX_TT10,1.0/"
SUE = API + "TR,DF_SANAYI_URETIM_ENDEKS_ANA_C,1.0/"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _json(name: str) -> httpx.Response:
    return httpx.Response(200, json=_fixture(name))


@pytest.fixture
def tuik(catalog):
    provider = TuikProvider(
        catalog.source("tuik"), http=SecureHttpClient(rps=None, max_retries=1, sleep=lambda _: None)
    )
    yield provider
    provider.close()


def test_registered_and_catalog_points_to_adapter(catalog):
    source = catalog.source("tuik")
    assert PROVIDERS[source.provider] is TuikProvider
    assert source.canaries and all(s.remote for s in source.series)


def test_refuses_untrusted_host(catalog):
    hijacked = replace(catalog.source("tuik"), base_url="https://evil.example/api/core/nodes/1/datasets/")
    with pytest.raises(ProviderError, match="yalnızca"):
        TuikProvider(hijacked, http=SecureHttpClient())


@pytest.mark.parametrize(
    ("raw", "expected"), [("2026-09", date(2026, 9, 30)), ("2025-Q4", date(2025, 12, 31)), ("2025", date(2025, 12, 31))]
)
def test_parse_period(raw, expected):
    assert parse_period(raw) == expected


def test_parse_period_rejects_unknown():
    with pytest.raises(UnexpectedResponse, match="dönemi"):
        parse_period("dün")


@pytest.mark.parametrize("remote", ["DF_TUFE/TR.M", "TR,DF_TUFE,1.0/", "TR,DF_TUFE,1.0"])
def test_split_remote_requires_agency_dataflow_version_and_key(remote):
    with pytest.raises(InvalidInput, match="remote"):
        split_remote(remote)


@respx.mock
def test_fetch_posts_criteria_and_parses_jsonstat(tuik, tmp_path):
    tuik._raw_store = RawStore(tmp_path)
    structure = respx.get(TUFE + "structure").mock(return_value=_json("structure_tufe.json"))
    data = respx.post(TUFE + "data").mock(return_value=_json("data_tufe.json"))
    df = tuik.fetch(
        FetchRequest.create("tuik", ["TUIK.TUFE.ENDEKS", "TUIK.TUFE.AYLIK"], date(2025, 10, 1), date(2026, 3, 31))
    )

    assert structure.call_count == 1  # the structure is cached per dataset
    body = json.loads(data.calls.last.request.content)
    assert data.calls.last.request.headers["content-type"] == "application/json"
    assert {c["id"]: c["filterValues"] for c in body if c["type"] == "CodeValues"} == {
        "REF_AREA": ["TR"], "FREQ": ["M"], "SINIFLAMA_DUZEYI": ["TUFE"], "DEGISIM": ["2"],
        "BASE_PER": ["2025"], "COICOP_2018": ["0"], "INDICATOR": ["F_TFE"],
    }  # fmt: skip
    assert body[-1] == {"id": "TIME_PERIOD", "type": "TimeRange", "from": "2025-10-01", "to": "2026-03-31"}

    level = df[df["series_id"] == "TUIK.TUFE.ENDEKS"]
    assert level["value"].tolist() == [108.477581, 109.415936, 110.386963, 115.73, 119.16, 121.47]
    assert level["date"].tolist()[0] == date(2025, 10, 31) and level["date"].tolist()[-1] == date(2026, 3, 31)
    assert set(df["dataset_id"]) == {"DF_TUFE_SDMX_TT10"} and set(df["freq"]) == {"M"}
    assert set(level["unit"]) == {"index"} and level["publication_lag_days"].iloc[0] == 5
    assert set(df["metric"]) == {"level"} and not df["is_proxy"].any()
    assert (tmp_path / "tuik" / "manifest.jsonl").exists()


@respx.mock
def test_selects_one_series_from_a_sparse_cube(tuik):
    respx.get(SUE + "structure").mock(return_value=_json("structure_sue.json"))
    respx.post(SUE + "data").mock(return_value=_json("data_sue_cube.json"))
    df = tuik.fetch(FetchRequest.create("tuik", ["TUIK.SUE.MA"], date(2026, 5, 1), date(2026, 12, 31)))
    assert df["value"].tolist() == [110.3171410011, 110.4904861434, 109.4564795378, 108.3293251551]
    assert df["date"].tolist()[-1] == date(2026, 8, 31) and df["publication_lag_days"].iloc[0] == 41


def test_select_jsonstat_cells():
    cube = _fixture("data_sue_cube.json")
    key = {
        "REF_AREA": "TR",
        "FREQ": "M",
        "ACTIVITY_NACE_REV2": "B-C-D",
        "INDICATOR": "II_SUE",
        "BASE_PER": "2021",
        "FAAL_GRUP": "1",
    }
    annual = select_jsonstat(cube, {**key, "SEASONAL_ADJUST": "W", "DEGISIM_TUR": "4"}, "TIME_PERIOD")
    assert annual[-1] == ("2026-08", -1.8617552633)
    assert (
        select_jsonstat(cube, {**key, "SEASONAL_ADJUST": "N", "DEGISIM_TUR": "2"}, "TIME_PERIOD") == []
    )  # empty cells
    assert (
        select_jsonstat(cube, {**key, "SEASONAL_ADJUST": "X", "DEGISIM_TUR": "1"}, "TIME_PERIOD") == []
    )  # unknown code
    as_list = {**cube, "value": [None] * 36}
    as_list["value"][35] = "1.5"  # dense array form: SEASONAL_ADJUST=Y (2*12) + DEGISIM_TUR=4 (2*4) + 2026-08 (3)
    assert select_jsonstat(as_list, {**key, "SEASONAL_ADJUST": "Y", "DEGISIM_TUR": "4"}, "TIME_PERIOD") == [
        ("2026-08", 1.5)
    ]


def test_select_jsonstat_rejects_changed_dimensions():
    cube = _fixture("data_sue_cube.json")
    with pytest.raises(UnexpectedResponse, match="boyutları"):
        select_jsonstat(cube, {"REF_AREA": "TR"}, "TIME_PERIOD")
    with pytest.raises(UnexpectedResponse, match="JSON-stat"):
        select_jsonstat({"id": ["A"], "size": []}, {}, "TIME_PERIOD")
    bad = _fixture("data_tufe.json")
    bad["value"]["0"] = "n/a"
    key = dict(zip(bad["id"][:-1], ["TR", "M", "TUFE", "1", "2025", "0", "F_TFE"], strict=True))
    with pytest.raises(UnexpectedResponse, match="sayısal"):
        select_jsonstat(bad, key, "TIME_PERIOD")


@respx.mock
def test_empty_period_returns_no_rows(tuik):
    respx.get(TUFE + "structure").mock(return_value=_json("structure_tufe.json"))
    respx.post(TUFE + "data").mock(return_value=_json("data_empty.json"))
    assert tuik.fetch(FetchRequest.create("tuik", ["TUIK.TUFE.ENDEKS"], date(1990, 1, 1), date(1991, 12, 31))).empty


@respx.mock
def test_key_length_must_match_structure(tuik):
    structure = _fixture("structure_tufe.json")
    structure["criteria"].insert(2, {"id": "NEW_DIM", "label": "x"})
    respx.get(TUFE + "structure").mock(return_value=httpx.Response(200, json=structure))
    with pytest.raises(UnexpectedResponse, match="8 boyutlu"):
        tuik.fetch(FetchRequest.create("tuik", ["TUIK.TUFE.ENDEKS"], date(2026, 1, 1)))


@respx.mock
@pytest.mark.parametrize(
    ("response", "error", "match"),
    [
        (
            httpx.Response(302, headers={"location": "https://veriportali.tuik.gov.tr/"}),
            UnexpectedResponse,
            "yönlendirme",
        ),
        (httpx.Response(403, text="Erişim engellendi"), ProviderError, "HTTP 403"),
        (
            httpx.Response(200, text="<!doctype html>", headers={"content-type": "text/html"}),
            UnexpectedResponse,
            "JSON yerine",
        ),
        (
            httpx.Response(200, text="{bozuk", headers={"content-type": "application/json"}),
            UnexpectedResponse,
            "çözümlenemedi",
        ),
        (httpx.Response(200, json=[1, 2]), UnexpectedResponse, "nesne"),
        (httpx.Response(200, json={"criteria": "x"}), UnexpectedResponse, "boyut listesi"),
        (
            httpx.Response(200, json={"criteria": [{"id": "A"}], "timeDimension": "T"}),
            UnexpectedResponse,
            "zaman boyutu",
        ),
    ],
)
def test_bad_structure_responses(tuik, response, error, match):
    respx.get(TUFE + "structure").mock(return_value=response)
    with pytest.raises(error, match=match):
        tuik.fetch(FetchRequest.create("tuik", ["TUIK.TUFE.ENDEKS"], date(2026, 1, 1)))


def test_unknown_series_and_transforms_rejected(tuik):
    with pytest.raises(InvalidInput, match="bilinmeyen seri"):
        tuik.fetch(FetchRequest.create("tuik", ["DF_TUFE_SDMX_TT10"], date(2026, 1, 1)))
    with pytest.raises(InvalidInput, match="desteklenmiyor"):
        tuik.fetch(FetchRequest.create("tuik", ["TUIK.TUFE.ENDEKS"], date(2026, 1, 1), formula=1))


def test_list_series_filters_catalog(tuik):
    assert "TUIK.ISSIZLIK.MA" in tuik.list_series()["series_id"].tolist()
    found = tuik.list_series("işsizlik")
    assert found["series_id"].tolist() == ["TUIK.ISSIZLIK.MA"] and found["datagroup"].iloc[0].startswith("TR,DF_ISGUCU")


def _previous_month() -> str:
    first = date.today().replace(day=1)
    return (first - timedelta(days=1)).isoformat()[:7]


def _canary_routes(tufe_data: dict) -> None:
    isgucu = API + "TR,DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C,1.0/"
    respx.get(TUFE + "structure").mock(return_value=_json("structure_tufe.json"))
    respx.post(TUFE + "data").mock(return_value=httpx.Response(200, json=tufe_data))
    dims = ["REF_AREA", "FREQ", "INDICATOR", "ISGUCU_GOSTERGE", "TEMEL_YAS", "SEASONAL_ADJUST", "SEX", "TIME_PERIOD"]
    respx.get(isgucu + "structure").mock(
        return_value=httpx.Response(200, json={"criteria": [{"id": d} for d in dims], "timeDimension": "TIME_PERIOD"})
    )
    codes = ["TR", "M", "ISGI_TIG", "8", "Y_GE15", "S", "_T", _previous_month()]
    cube = {
        "id": dims, "size": [1] * 8, "value": {"0": "8.4"},
        "dimension": {d: {"category": {"index": [c]}} for d, c in zip(dims, codes, strict=True)},
    }  # fmt: skip
    respx.post(isgucu + "data").mock(return_value=httpx.Response(200, json=cube))


@respx.mock
def test_healthcheck_ok(tuik):
    fresh = _fixture("data_tufe.json")
    fresh["dimension"]["TIME_PERIOD"]["category"]["index"][-1] = _previous_month()
    _canary_routes(fresh)
    result = tuik.healthcheck()
    assert result.status is HealthStatus.OK, result.detail
    assert "TUIK.ISSIZLIK.MA" in result.detail and result.checked == ("TUIK.TUFE.ENDEKS", "TUIK.ISSIZLIK.MA")


@respx.mock
def test_healthcheck_fails_on_stale_series(tuik):
    _canary_routes(_fixture("data_tufe.json"))  # last observation 2026-03, recorded 2026-11-02
    result = tuik.healthcheck()
    assert result.status is HealthStatus.FAIL and "TUIK.TUFE.ENDEKS: son gözlem 2026-03-31" in result.detail


@respx.mock
def test_healthcheck_fails_without_observations(tuik):
    _canary_routes(_fixture("data_empty.json"))
    result = tuik.healthcheck()
    assert result.status is HealthStatus.FAIL and "gözlem yok" in result.detail


@respx.mock
def test_healthcheck_reports_provider_errors(tuik):
    respx.get(url__startswith=API).mock(return_value=httpx.Response(403, text="Erişim engellendi"))
    result = tuik.healthcheck()
    assert result.status is HealthStatus.FAIL and "HTTP 403" in result.detail


def test_healthcheck_skipped_without_canaries(catalog):
    source = replace(catalog.source("tuik"), series=())
    assert TuikProvider(source, http=SecureHttpClient()).healthcheck().status is HealthStatus.SKIPPED
