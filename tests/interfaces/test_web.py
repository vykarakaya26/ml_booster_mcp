import io
import json
import re
from pathlib import Path

import pandas as pd
import pytest
from starlette.testclient import TestClient

from fakes import FakeProviderFactory, InMemoryObservationStore, InMemoryPanelStore
from mlbooster.bootstrap import Container
from mlbooster.interfaces.web import create_app

TOKEN = "test-token"
MONTHS = pd.date_range("2018-01-31", "2026-09-30", freq="ME")
DAYS = pd.bdate_range("2018-01-01", "2026-09-30")
SERIES = {
    "TP.TUKFIY2025.GENEL": pd.Series([100.0 * 1.02**i for i in range(len(MONTHS))], index=MONTHS),
    "TP.DK.USD.A": pd.Series([5 + i / 300 + (i % 7) / 50 for i in range(len(DAYS))], index=DAYS),
}
AUTH = {"X-MLB-Token": TOKEN}
BASE = "http://127.0.0.1:8765"


@pytest.fixture
def client(catalog_repo):
    def factory():
        return Container(
            catalog=catalog_repo,
            providers=FakeProviderFactory(SERIES),
            observations=InMemoryObservationStore(),
            panels=InMemoryPanelStore(),
            overrides=catalog_repo,
        )

    return TestClient(create_app(factory, token=TOKEN), base_url=BASE, raise_server_exceptions=False)


def test_index_embeds_escaped_token_and_security_headers(client):
    res = client.get("/")
    assert res.status_code == 200
    assert f'content="{TOKEN}"' in res.text
    csp = res.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
    assert res.headers["x-frame-options"] == "DENY"
    assert client.get("/static/app.js").status_code == 200


def test_rejects_non_loopback_host(client):
    # DNS rebinding: even if an attacker's domain resolves to 127.0.0.1, its Host header does not match
    assert client.get("/", headers={"Host": "evil.example:8765"}).status_code == 400
    assert client.get("/api/sectors", headers={**AUTH, "Host": "evil.example"}).status_code == 400


def test_api_requires_token_and_same_origin(client):
    assert client.get("/api/sectors").status_code == 403
    assert client.get("/api/sectors", headers={"X-MLB-Token": "wrong-token"}).status_code == 403
    cross = client.post("/api/panel", json={"sector": "telekom"}, headers={**AUTH, "Origin": "https://evil.example"})
    assert cross.status_code == 403
    same = client.get("/api/sectors", headers={**AUTH, "Origin": BASE})
    assert same.status_code == 200
    telekom = next(s for s in same.json() if s["id"] == "telekom")
    assert telekom["name"] == "Telekom" and telekom["ready"] <= telekom["features"]


def test_invalid_input_returns_safe_message(client):
    res = client.get("/api/sectors/..%2F..%2Fetc/map", headers=AUTH)
    assert res.status_code in (400, 404)
    bad = client.post("/api/panel", json={"sector": "../../etc/passwd"}, headers=AUTH)
    assert bad.status_code == 400 and "Geçersiz sektör" in bad.json()["error"]
    assert client.post("/api/panel", json={"sector": "telekom", "freq": "X"}, headers=AUTH).status_code == 400
    assert (
        client.post("/api/panel", content=b"[]", headers={**AUTH, "content-type": "application/json"}).status_code
        == 400
    )
    assert (
        client.post(
            "/api/panel", content=b"x" * 70_000, headers={**AUTH, "content-type": "application/json"}
        ).status_code
        == 400
    )


def test_unexpected_errors_are_not_leaked(catalog_repo):
    def broken():
        raise RuntimeError("/Users/gizli/yol ve sır")

    client = TestClient(create_app(broken, token=TOKEN), base_url=BASE, raise_server_exceptions=False)
    res = client.get("/api/meta", headers=AUTH)
    assert res.status_code == 500 and "gizli" not in res.text


def test_meta_and_sector_map(client):
    meta = client.get("/api/meta", headers=AUTH).json()
    assert meta["providers"] == ["evds3"]
    m = client.get("/api/sectors/telekom/map", headers=AUTH).json()
    assert m["sector"]["id"] == "telekom" and m["macro_features"] and m["sources"]


def test_panel_audit_and_export(client):
    scope = {"sector": "telekom", "start": "2019-01", "end": "2026-09-30"}
    panel = client.post("/api/panel", json=scope, headers=AUTH).json()
    assert "usdtry_logdiff" in panel["features"]
    assert len(panel["dates"]) == panel["rows"] == len(panel["columns"]["usdtry_logdiff"])
    assert panel["saved"] is True and "path" not in panel  # the local file path is never sent to the client

    audit = client.post("/api/audit", json=scope, headers=AUTH).json()
    assert audit["months"] == len(audit["dates"]) and "counts" in audit

    csv = client.post("/api/panel/export", json={**scope, "format": "csv"}, headers=AUTH)
    assert csv.status_code == 200 and csv.headers["content-disposition"] == 'attachment; filename="telekom_m_panel.csv"'
    assert csv.text.splitlines()[0].startswith("date,")
    pq = client.post("/api/panel/export", json={**scope, "format": "parquet"}, headers=AUTH)
    assert not pd.read_parquet(io.BytesIO(pq.content)).empty


def test_health_skips_without_key(client):
    rows = client.get("/api/health", headers=AUTH).json()
    assert rows and all("status" in r for r in rows)


def test_cli_web_command_is_registered():
    from typer.testing import CliRunner

    from mlbooster.interfaces import cli

    out = CliRunner().invoke(cli.app, ["web", "--help"])
    assert out.exit_code == 0 and "127.0.0.1" in out.output


def test_free_port_skips_busy_port():
    import socket

    from mlbooster.interfaces.cli import _free_port

    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert _free_port(port) not in (None, port)


def test_feature_labels_are_readable_and_distinct(catalog):
    from mlbooster.domain.services.sector_map import build_sector_map
    from mlbooster.interfaces.web.labels import sector_feature_info

    info = sector_feature_info(build_sector_map(catalog, "bankacilik", {"evds3", "bis"}))
    assert info["aofm"]["label"] != info["aofm_son"]["label"]  # average and month-end versions are told apart
    assert "ay sonu" in info["aofm_son"]["label"]
    usd = info["usdtry_logdiff"]
    assert usd["label"].startswith("USD/TRY") and usd["transform_label"] == "Aylık değişim (log)"
    assert "≈" in usd["unit"] and "İş günü" in usd["description"]  # Turkish capital İ
    assert info["tufe_yoy"]["unit"] == "yüzde" and info["tufe_yoy"]["publication_lag_days"] == 3
    pending = info["kredi_faizi"]
    assert pending["series"] is None and pending["label"] == "Kredi faizi"  # long hint: name-derived label
    auto = sector_feature_info(build_sector_map(catalog, "otomotiv", {"evds3", "odmd"}))
    assert auto["tuketici_guveni"]["label"] == "Tüketici güven endeksi"  # short hint keeps Turkish letters


def test_sector_map_and_list_include_readable_metadata(client):
    m = client.get("/api/sectors/bankacilik/map", headers=AUTH).json()
    assert m["feature_info"]["aofm"]["source_name"]
    sectors = client.get("/api/sectors", headers=AUTH).json()
    bank = next(s for s in sectors if s["id"] == "bankacilik")
    assert bank["pending"] >= 1 and bank["ml_use_cases"]


def test_sector_map_sends_turkish_labels_for_every_enum(client):
    from mlbooster.domain.model import Access, Verification

    m = client.get("/api/sectors/bankacilik/map", headers=AUTH).json()
    labels = m["labels"]
    assert set(labels["access"]) == {a.value for a in Access}
    assert set(labels["verification"]) == {v.value for v in Verification}
    assert set(labels["status"]) == {"ready", "pending_discovery", "no_provider"}
    assert all(f in labels["frequency"] for s in m["sources"] for f in s["frequency"])
    assert all(f["status"] in labels["status"] for f in m["macro_features"])


# Turkified technical terms and old headings the panel must not show (CLAUDE.md language standard).
BANNED_PANEL_TERMS = ("çıta", "çapa", "kırılma", "durağan", "eğilim", "hizala", "geleceği görmeyen", "sızıntı",
                      "aykırı", "bayat", "kantil", "ne yapmalı", "sizden gerekenler", "kısaca")


def test_panel_text_keeps_technical_terms_in_english():
    for name in ("app.js", "glossary.js", "charts.js", "index.html"):
        text = (WEB_STATIC / name).read_text(encoding="utf-8").lower().replace("İ", "i")
        found = [t for t in BANNED_PANEL_TERMS if t in text]
        assert not found, f"{name}: {found}"


def test_stationarity_labels_cover_the_domain_classes():
    import inspect

    from mlbooster.domain.services import distribution

    classes = set(re.findall(r'return "(\w+)"', inspect.getsource(distribution._classify)))
    app_js = (WEB_STATIC / "app.js").read_text(encoding="utf-8")
    mapping = re.search(r"const STATIONARITY = \{([^}]*)\}", app_js).group(1)
    assert classes == set(re.findall(r"(\w+):", mapping)) == {"stationary", "unit_root", "break_suspected", "inconclusive"}


def test_glossary_has_one_entry_per_term():
    js = (WEB_STATIC / "glossary.js").read_text(encoding="utf-8")
    terms = re.findall(r'term: "([^"]+)"', js)
    assert len(terms) == len(set(terms)) and len(terms) > 20
    keys = set(re.findall(r"^  (\w+): \{$", js, re.M))
    used = set(re.findall(r'term\("(\w+)"', (WEB_STATIC / "app.js").read_text(encoding="utf-8")))
    assert used and used <= keys


# --- market share screen -----------------------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "skills" / "market-share" / "assets" / "sample_market_share.csv"
WEB_STATIC = REPO / "src" / "mlbooster" / "interfaces" / "web" / "static"


def test_sample_data_is_served_and_matches_skill_asset(client):
    # the web panel ships its own copy (skills/ is not package data); both must stay identical
    assert (WEB_STATIC / "sample_market_share.csv").read_bytes() == SAMPLE.read_bytes()
    res = client.get("/static/sample_market_share.csv")
    assert res.status_code == 200 and res.text.startswith("entity,date,value,segment")


def test_market_share_on_sample_with_focus(client):
    body = {"text": SAMPLE.read_text(encoding="utf-8"), "format": "csv", "focus": "marka c", "unit": "adet"}
    res = client.post("/api/market-share", json=body, headers=AUTH)
    assert res.status_code == 200, res.text
    out = res.json()
    meta = out["meta"]
    assert meta["focus"] == "Marka C" and meta["basis"] == "ttm" and meta["has_total"] and meta["has_segments"]
    assert meta["entities"] == 7 and meta["unit"] == "adet"
    latest = out["concentration"]["latest"]
    assert 0 < latest["hhi"] <= 10_000 and latest["cr3"] <= latest["cr5"] <= 100.0001
    assert {p["trend"] for p in out["players"]} <= {"gaining", "losing", "flat", "insufficient_data"}
    assert out["competitors"]["focus"] == "Marka C" and out["competitors"]["candidates"]
    assert all(c["entity"] != "Marka C" and c["reasons"] for c in out["competitors"]["candidates"])
    shift = out["segments"]["shift_share"]
    assert shift["total_pp"] == pytest.approx(shift["share_effect_pp"] + shift["mix_effect_pp"], abs=1e-3)
    assert "Marka C" in out["shares"]["series"] and len(out["shares"]["dates"]) == len(out["concentration"]["hhi"])
    assert out["labels"]["trend"]["gaining"] == "pay kazanıyor" and out["labels"]["hhi_class"][latest["class"]]
    assert out["generated_at"]


def test_market_share_without_focus_has_no_competitor_set(client):
    res = client.post("/api/market-share", json={"text": SAMPLE.read_text(encoding="utf-8")}, headers=AUTH)
    assert res.status_code == 200
    assert "competitors" not in res.json() and "shift_share" not in res.json()["segments"]


def test_market_share_accepts_json_and_turkish_csv(client):
    months = [f"2024-{m:02d}" for m in range(1, 13)]
    rows = [{"entity": e, "date": d, "value": v + i} for i, d in enumerate(months) for e, v in (("A", 50), ("B", 30), ("TOTAL", 100))]
    res = client.post("/api/market-share", json={"text": json.dumps({"rows": rows}), "format": "json", "focus": "A"}, headers=AUTH)
    assert res.status_code == 200 and res.json()["meta"]["has_total"]
    csv_tr = "oyuncu;dönem;değer\n" + "".join(f"{e};{d};{v},5\n" for d in months for e, v in (("A", 6), ("B", 4)))
    res = client.post("/api/market-share", json={"text": csv_tr, "measure": "value", "kind": "stock"}, headers=AUTH)
    assert res.status_code == 200
    assert res.json()["players"][0]["share"] == pytest.approx(6.5 / 11 * 100, abs=1e-3)


def test_market_share_entity_names_are_returned_as_data(client):
    # names travel as JSON strings; the frontend writes them with textContent only
    evil = "<img src=x onerror=alert(1)>"
    text = "entity,date,value\n" + "".join(f"{e},2024-{m:02d},{v}\n" for m in range(1, 13) for e, v in ((evil, 5), ("B", 3)))
    res = client.post("/api/market-share", json={"text": text}, headers=AUTH)
    assert res.status_code == 200 and res.headers["content-type"].startswith("application/json")
    assert res.json()["players"][0]["entity"] == evil
    app_js = (WEB_STATIC / "app.js").read_text(encoding="utf-8")
    assert ".innerHTML" not in app_js and "insertAdjacentHTML" not in app_js and "outerHTML" not in app_js


@pytest.mark.parametrize(("body", "match"), [
    ({}, "tablosu boş"),
    ({"text": 42}, "tablosu boş"),
    ({"text": "entity,date,value\nA,2024-01,1", "format": "xlsx"}, "csv ya da json"),
    ({"text": "entity,date,value\nA,2024-01,1\nB,2024-01,2", "measure": "adet"}, "measure"),
    ({"text": "entity,date,value\nA,2024-01,1\nB,2024-01,2", "trend_years": 99}, "trend_years"),
    ({"text": "entity,date,value\nA,2024-01,1\nB,2024-01,2", "focus": "Z"}, "Odak oyuncu tabloda yok"),
    ({"text": "firma,tutar\nA,1"}, "eksik kolon"),
    ({"text": "entity,date,value\nA,dün,1\nB,dün,2"}, "Dönem biçimi"),
    ({"text": "[1, 2]", "format": "json"}, "liste"),
])  # fmt: skip
def test_market_share_rejects_bad_input_with_safe_message(client, body, match):
    res = client.post("/api/market-share", json=body, headers=AUTH)
    assert res.status_code == 400 and match.casefold() in res.json()["error"].casefold()


def test_market_share_requires_token_and_caps_body(client):
    assert client.post("/api/market-share", json={"text": "x"}).status_code == 403
    big = b'{"text": "' + b"a" * (8 * 1024 * 1024) + b'"}'
    res = client.post("/api/market-share", content=big, headers={**AUTH, "content-type": "application/json"})
    assert res.status_code == 400 and "8 MB" in res.json()["error"]
    # without a Content-Length header the streamed body is still cut at the limit
    chunks = (b"a" * 1024 * 1024 for _ in range(10))
    res = client.post("/api/market-share", content=chunks, headers={**AUTH, "content-type": "application/json"})
    assert res.status_code == 400 and "çok büyük" in res.json()["error"]


def test_views_drop_optional_sections_instead_of_printing_null():
    # DOM replaceChildren() renders null as the text "null"; views go through fill(), which drops it
    app_js = (WEB_STATIC / "app.js").read_text(encoding="utf-8")
    assert "function fill(el, ...children)" in app_js
    assert not re.search(r"\b(root|out|detail)\.replaceChildren\(", app_js)


@pytest.fixture
def source_client(catalog_repo):
    from fakes import EntityFactory

    factory = EntityFactory()

    def container():
        return Container(
            catalog=catalog_repo, providers=factory, observations=InMemoryObservationStore(),
            panels=InMemoryPanelStore(), overrides=catalog_repo,
        )  # fmt: skip

    client = TestClient(create_app(container, token=TOKEN), base_url=BASE, raise_server_exceptions=False)
    client.factory = factory
    return client


def test_share_series_lists_sources_with_a_ready_provider(source_client, client):
    res = source_client.get("/api/share-series", headers=AUTH)
    assert res.status_code == 200
    (bddk,) = res.json()  # TBB, ODMD, ... have no provider yet
    assert bddk["source"] == "bddk" and "bankacilik" in bddk["sectors"] and "yazılı iznine" in bddk["license"]
    assert {"code": "BDDK.AKTIF", "name": "Toplam aktifler, banka grubuna göre (milyon TL)", "unit": "million_try"} in bddk["series"]
    assert client.get("/api/share-series", headers=AUTH).json() == []  # only evds3 is ready there
    assert source_client.get("/api/share-series").status_code == 403


def test_market_share_from_source(source_client):
    body = {"source": "bddk", "series": "BDDK.AKTIF", "start": "2023-01", "end": "2025-12", "focus": "Katılım"}
    res = source_client.post("/api/market-share/source", json=body, headers=AUTH)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["meta"]["unit"] == "milyon TL" and out["meta"]["kind"] == "stock" and out["meta"]["entities"] == 3
    assert out["meta"]["source"]["attribution"] == "Kaynak: BDDK — BDDK Aylık Bülten" and out["meta"]["has_total"]
    assert out["competitors"]["focus"] == "Katılım" and out["labels"]["trend"]["gaining"]
    assert out["market"]["coverage"][-1] == pytest.approx(100)
    calls = source_client.factory.calls
    assert calls[0].period.start.isoformat() == "2023-01-01" and calls[0].series_ids == ("BDDK.AKTIF",)
    again = source_client.post("/api/market-share/source", json={**body, "focus": None}, headers=AUTH)
    assert again.status_code == 200 and len(calls) == 1  # second request is served from the store
    refreshed = source_client.post("/api/market-share/source", json={**body, "refresh": True}, headers=AUTH)
    assert refreshed.status_code == 200 and len(calls) == 2


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ({"series": "BDDK.AKTIF"}, "'source' alanı zorunlu"),
        ({"source": "bddk"}, "'series' alanı zorunlu"),
        ({"source": "../etc", "series": "BDDK.AKTIF"}, "Geçersiz"),
        ({"source": "bddk", "series": "BDDK.AKTIF&x=1"}, "Geçersiz seri"),
        ({"source": "bddk", "series": "BDDK.YOK"}, "serisi yok"),
        ({"source": "bddk", "series": "BDDK.AKTIF", "start": "dün"}, "YYYY-AA"),
        ({"source": "bddk", "series": "BDDK.AKTIF", "refresh": "evet"}, "doğru/yanlış"),
        ({"source": "bddk", "series": "BDDK.AKTIF", "basis": "x"}, "basis"),
        ({"source": "tbb", "series": "X"}, "serisi yok"),
    ],
)
def test_market_share_from_source_rejects_bad_input(source_client, body, match):
    res = source_client.post("/api/market-share/source", json=body, headers=AUTH)
    assert res.status_code == 400 and match in res.json()["error"]


def test_web_assets_offer_the_source_mode():
    js = (Path(__file__).parents[2] / "src/mlbooster/interfaces/web/static/app.js").read_text(encoding="utf-8")
    assert '"/api/market-share/source"' in js and '"/api/share-series"' in js and "Kaynaktan çek" in js
    assert ".innerHTML" not in js
