from datetime import date

import httpx
import pandas as pd
import pytest
import respx

from conftest import TEST_KEY
from mlbooster.adapters.base import AuthError, SchemaError
from mlbooster.adapters.evds3 import date_chunks, parse_evds_date
from mlbooster.schema import SILVER_COLUMNS, validate_silver

BASE = "https://evds3.tcmb.gov.tr/igmevdsms-dis/"


def _items(code, rows):
    col = code.replace(".", "_")
    return {"totalCount": len(rows), "items": [{"Tarih": d, col: v, "UNIXTIME": {"$numberLong": "0"}} for d, v in rows]}


@pytest.mark.parametrize(
    ("raw", "expected", "freq"),
    [
        ("01-10-2026", date(2026, 10, 1), "D"),
        ("2026-2", date(2026, 2, 28), "M"),
        ("2024-02", date(2024, 2, 29), "M"),
        ("2026-Q3", date(2026, 9, 30), "Q"),
        ("2026-S1", date(2026, 6, 30), "S"),
        ("2025", date(2025, 12, 31), "A"),
    ],
)
def test_parse_evds_date(raw, expected, freq):
    assert parse_evds_date(raw) == (expected, freq)


def test_parse_evds_date_rejects_unknown():
    with pytest.raises(SchemaError):
        parse_evds_date("Ekim 2026")


def test_url_has_no_question_mark_and_no_key(evds):
    url = evds.build_url(series="TP.FG.J0", startDate="01-01-2020", endDate="01-01-2021", type="json", formulas=None)
    assert url == BASE + "series=TP.FG.J0&startDate=01-01-2020&endDate=01-01-2021&type=json"
    assert "?" not in url and TEST_KEY not in url


def test_date_chunks_cover_range_without_overlap():
    chunks = date_chunks(date(2020, 1, 1), date(2026, 10, 30), "B", 1000)
    assert chunks[0][0] == date(2020, 1, 1) and chunks[-1][1] == date(2026, 10, 30)
    for (_, a_end), (b_start, _) in zip(chunks, chunks[1:]):
        assert (b_start - a_end).days == 1
    assert all((e - s).days < 1000 for s, e in chunks)
    assert len(date_chunks(date(2000, 1, 1), date(2026, 1, 1), "M", 1000)) == 1


@respx.mock
def test_fetch_sends_key_in_header_and_returns_silver(evds):
    route = respx.get(url__startswith=BASE).mock(
        return_value=httpx.Response(200, json=_items("TP.FG.J0", [("2026-8", "3500.1"), ("2026-9", "3550.2"), ("2026-10", None)]))
    )
    df = evds.fetch(["TP.FG.J0"], date(2026, 8, 1), date(2026, 10, 30))
    request = route.calls.last.request
    assert request.headers["key"] == TEST_KEY
    assert TEST_KEY not in str(request.url) and "?" not in str(request.url)
    assert list(df.columns) == SILVER_COLUMNS
    assert df["value"].tolist() == [3500.1, 3550.2]  # None gözlem düşer
    assert df["date"].tolist() == [date(2026, 8, 31), date(2026, 9, 30)]
    assert set(df["metric"]) == {"level"} and set(df["freq"]) == {"M"}
    assert df["publication_lag_days"].iloc[0] == 3
    validate_silver(df)


@respx.mock
def test_formula_also_requests_level(evds):
    def responder(request):
        url = str(request.url)
        if "formulas=3" in url:
            payload = {"items": [{"Tarih": "2026-9", "TP_FG_J0-3": "33.3", "TP_FG_J0": None}]}
        else:
            payload = _items("TP.FG.J0", [("2026-9", "3550.2")])
        return httpx.Response(200, json=payload)

    route = respx.get(url__startswith=BASE).mock(side_effect=responder)
    df = evds.fetch(["TP.FG.J0"], date(2026, 9, 1), date(2026, 9, 30), formula=3)
    assert route.call_count == 2
    assert dict(zip(df["metric"], df["value"])) == {"level": 3550.2, "yoy_pct": 33.3}


@respx.mock
def test_chunked_fetch_deduplicates(evds):
    respx.get(url__startswith=BASE).mock(
        return_value=httpx.Response(200, json=_items("TP.DK.USD.A.YTL", [("02-01-2020", "5.9")]))
    )
    df = evds.fetch(["TP.DK.USD.A.YTL"], date(2020, 1, 1), date(2026, 1, 1))
    assert respx.calls.call_count > 1
    assert len(df) == 1


@respx.mock
def test_html_response_raises(evds):
    respx.get(url__startswith=BASE).mock(
        return_value=httpx.Response(200, text="<!doctype html><html></html>", headers={"content-type": "text/html"})
    )
    with pytest.raises(SchemaError, match="HTML"):
        evds.fetch(["TP.FG.J0"], date(2026, 1, 1), date(2026, 2, 1))


@respx.mock
def test_redirect_raises(evds):
    respx.get(url__startswith=BASE).mock(return_value=httpx.Response(302, headers={"location": "https://evds3.tcmb.gov.tr/"}))
    with pytest.raises(SchemaError, match="302"):
        evds.fetch(["TP.FG.J0"], date(2026, 1, 1), date(2026, 2, 1))


@respx.mock
def test_auth_error_is_not_retried(evds):
    route = respx.get(url__startswith=BASE).mock(return_value=httpx.Response(401, text="Invalid API Key"))
    with pytest.raises(AuthError):
        evds.fetch(["TP.FG.J0"], date(2026, 1, 1), date(2026, 2, 1))
    assert route.call_count == 1


@respx.mock
def test_429_is_retried(evds):
    route = respx.get(url__startswith=BASE).mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=_items("TP.FG.J0", [("2026-1", "1")]))]
    )
    df = evds.fetch(["TP.FG.J0"], date(2026, 1, 1), date(2026, 1, 31))
    assert route.call_count == 2 and len(df) == 1


@respx.mock
def test_missing_series_column_raises(evds):
    respx.get(url__startswith=BASE).mock(return_value=httpx.Response(200, json=_items("TP.OTHER", [("2026-1", "1")])))
    with pytest.raises(SchemaError, match="kolon"):
        evds.fetch(["TP.FG.J0"], date(2026, 1, 1), date(2026, 1, 31))


@respx.mock
def test_bronze_saved_with_hash(evds, tmp_path):
    respx.get(url__startswith=BASE).mock(return_value=httpx.Response(200, json=_items("TP.FG.J0", [("2026-1", "1")])))
    evds.fetch(["TP.FG.J0"], date(2026, 1, 1), date(2026, 1, 31))
    files = list((tmp_path / "bronze" / "evds3").glob("*.json"))
    manifest = (tmp_path / "bronze" / "evds3" / "manifest.jsonl").read_text()
    assert len(files) == 1 and TEST_KEY not in manifest


@respx.mock
def test_list_series_from_serielist(evds):
    respx.get(url__startswith=BASE + "serieList/").mock(
        return_value=httpx.Response(200, json=[
            {"SERIE_CODE": "TP.DK.USD.A.YTL", "SERIE_NAME": "(USD) ABD Doları (Döviz Alış)",
             "FREQUENCY_STR": "İŞGÜNÜ", "START_DATE": "02-01-1950", "END_DATE": "30-10-2026",
             "DATAGROUP_CODE": "bie_dkdovizgn"},
        ])  # fmt: skip
    )
    df = evds.list_series("bie_dkdovizgn")
    assert df.iloc[0]["series_id"] == "TP.DK.USD.A.YTL" and df.iloc[0]["freq"] == "B"


def test_healthcheck_skips_without_key(evds, monkeypatch):
    monkeypatch.delenv("EVDS_API_KEY")
    assert evds.healthcheck().status == "skipped"


@respx.mock
def test_healthcheck_fails_on_html(evds):
    respx.get(url__startswith=BASE).mock(return_value=httpx.Response(200, text="<html>", headers={"content-type": "text/html"}))
    result = evds.healthcheck()
    assert result.status == "fail" and "HTML" in result.detail
