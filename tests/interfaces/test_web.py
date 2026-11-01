import io

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
    models = client.post("/api/backtest", json={"sector": "telekom", "target": "x", "models": ["rm -rf"]}, headers=AUTH)
    assert models.status_code == 400 and "Tanımsız model" in models.json()["error"]


def test_unexpected_errors_are_not_leaked(catalog_repo):
    def broken():
        raise RuntimeError("/Users/gizli/yol ve sır")

    client = TestClient(create_app(broken, token=TOKEN), base_url=BASE, raise_server_exceptions=False)
    res = client.get("/api/meta", headers=AUTH)
    assert res.status_code == 500 and "gizli" not in res.text


def test_meta_and_sector_map(client):
    meta = client.get("/api/meta", headers=AUTH).json()
    assert "naive" in meta["models"] and meta["providers"] == ["evds3"]
    m = client.get("/api/sectors/telekom/map", headers=AUTH).json()
    assert m["sector"]["id"] == "telekom" and m["macro_features"] and m["sources"]


def test_panel_audit_backtest_and_export(client):
    scope = {"sector": "telekom", "start": "2019-01", "end": "2026-09-30"}
    panel = client.post("/api/panel", json=scope, headers=AUTH).json()
    assert "usdtry_logdiff" in panel["features"]
    assert len(panel["dates"]) == panel["rows"] == len(panel["columns"]["usdtry_logdiff"])
    assert panel["saved"] is True and "path" not in panel  # the local file path is never sent to the client

    audit = client.post("/api/audit", json=scope, headers=AUTH).json()
    assert audit["months"] == len(audit["dates"]) and "counts" in audit

    bt = client.post(
        "/api/backtest",
        json={**scope, "target": "usdtry_logdiff", "min_train": 24, "models": ["naive", "drift", "mean12"]},
        headers=AUTH,
    ).json()
    assert {m["model"] for m in bt["metrics"]} == {"naive", "drift", "mean12"}
    assert bt["best_model"] in {"naive", "drift", "mean12"}
    assert len(bt["history"]["dates"]) == len(bt["history"]["values"]) > 0

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
    assert pending["series"] is None and pending["label"] == "Kredi faizi"


def test_sector_map_and_list_include_readable_metadata(client):
    m = client.get("/api/sectors/bankacilik/map", headers=AUTH).json()
    assert m["feature_info"]["aofm"]["source_name"]
    sectors = client.get("/api/sectors", headers=AUTH).json()
    bank = next(s for s in sectors if s["id"] == "bankacilik")
    assert bank["pending"] >= 1 and bank["ml_use_cases"]
