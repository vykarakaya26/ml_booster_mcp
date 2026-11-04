"""Search interest in the brand flow: opt-in everywhere, kept apart from shares, never billed without a request."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from fakes import (
    BrandFactory,
    FakeTermLookup,
    InMemoryEvidenceStore,
    InMemoryObservationStore,
    InMemoryPanelStore,
    StaticSearchTerms,
)
from mlbooster.application.errors import CredentialsMissing
from mlbooster.bootstrap import Container
from mlbooster.interfaces import cli, mcp_server
from mlbooster.interfaces.presenters import brand_features_dict, search_lines
from mlbooster.interfaces.web.app import create_app

TOKEN = "t" * 32
BASE = "http://127.0.0.1:8765"
HEADERS = {"X-MLB-Token": TOKEN, "Origin": BASE}
PERIOD = {"start": "2022-01", "end": "2025-12"}
REPORT = Path(__file__).resolve().parents[2] / "skills/market-share/scripts/render_brand_report.py"


@pytest.fixture
def lookup():
    return FakeTermLookup()


def make_container(catalog_repo, lookup) -> Container:
    return Container(
        catalog=catalog_repo, providers=BrandFactory(), observations=InMemoryObservationStore(), panels=InMemoryPanelStore(),
        overrides=catalog_repo, evidence_store=InMemoryEvidenceStore(), search_terms=StaticSearchTerms(), term_lookup=lambda: lookup,
    )  # fmt: skip


@pytest.fixture
def container(catalog_repo, lookup):
    return make_container(catalog_repo, lookup)


def test_analysis_does_not_query_without_a_request(container, lookup):
    out = container.analyze_brand("Togg")
    assert lookup.queries == 0
    block = out["search_interest"]
    assert block["status"] == "not_run" and block["is_proxy"] is True and "pazar payı değildir" in block["note"]
    assert "--search-interest" in block["message"]


def test_analysis_with_the_flag_stays_apart_from_shares(container, lookup):
    plain = container.analyze_brand("Togg")
    out = container.analyze_brand("Togg", search_interest=True)
    block = out["search_interest"]
    assert lookup.queries > 0 and block["status"] == "done"
    assert {k: v for k, v in out.items() if k != "search_interest"} == {k: v for k, v in plain.items() if k != "search_interest"}
    assert block["terms"][0]["label"].casefold() == "togg" and block["terms"][0]["status_label"] == "bulundu"
    assert block["withheld"]["arama_payi"].startswith("arama_payi hesaplanmadı")
    assert block["scale"]["share_of_search"] is None
    json.dumps(block)  # goes to MCP, the panel and the report as it is


def test_competitors_come_from_the_brand_analysis(container):
    out = container.analyze_brand("Togg", search_interest=True)
    rivals = [t["label"] for t in out["search_interest"]["terms"] if t["mode"] == "competitor"]
    assert rivals == [c["entity"] for c in out["competitors"]]


def test_brand_without_a_source_shows_its_list_presence(container):
    out = container.analyze_brand("Migros", search_interest=True)
    assert out["status"] == "not_found" and "share" not in out
    term = out["search_interest"]["terms"][0]
    assert term["status"] == "related" and term["related_terms"] == ["migros'ta kampanya"]


def test_not_found_without_the_flag_says_it_was_not_run(container, lookup):
    assert container.analyze_brand("Migros")["search_interest"]["status"] == "not_run" and lookup.queries == 0


def test_missing_google_setup_does_not_stop_the_analysis(catalog_repo):
    def missing():
        raise CredentialsMissing("Google kimlik bilgisi bulunamadı")

    c = make_container(catalog_repo, None)
    c.term_lookup = missing
    out = c.analyze_brand("Togg", search_interest=True)
    assert out["status"] == "resolved" and out["search_interest"]["status"] == "unavailable"
    assert "kimlik" in out["search_interest"]["message"]


def test_container_without_search_interest_reports_it(catalog_repo):
    c = Container(catalog=catalog_repo, providers=BrandFactory(), observations=InMemoryObservationStore(), panels=InMemoryPanelStore(), overrides=catalog_repo)
    assert c.analyze_brand("Togg", search_interest=True)["search_interest"]["status"] == "unavailable"


def test_feature_panel_columns_are_opt_in_and_marked(container, lookup):
    plain = container.brand_features("Togg", save=False)
    assert lookup.queries == 0 and not {s.family for s in plain.specs} & {"search_interest"}
    assert any("Arama ilgisi feature'ları üretilmedi" in n for n in plain.notes)
    built = container.brand_features("Togg", save=False, search_interest=True)
    summary = brand_features_dict(built)
    columns = {c["name"]: c for c in summary["brand_columns"]}
    assert columns["marka_ilgi_degisim"]["family"] == "search_interest" and "marka_ilgi_degisim" in built.panel.columns
    assert "arama_payi" not in built.panel.columns and "arama_payi" not in columns
    assert any("pazar payı değildir" in n for n in built.notes)
    assert any(s["id"] == "bq_trends" and s["is_proxy"] for s in built.sources)


def test_feature_panel_reports_a_missing_setup(catalog_repo):
    c = Container(catalog=catalog_repo, providers=BrandFactory(), observations=InMemoryObservationStore(), panels=InMemoryPanelStore(), overrides=catalog_repo)
    built = c.brand_features("Togg", save=False, search_interest=True)
    assert "search_interest" in built.skipped and "marka_ilgi_degisim" not in built.panel.columns


def test_search_lines():
    not_run = {"status": "not_run", "note": "Arama ilgisi; pazar payı değildir.", "message": "çalıştırılmadı"}
    assert search_lines(not_run)[1] == "  çalıştırılmadı"


def test_cli_flag(monkeypatch, container, lookup):
    monkeypatch.setattr(cli, "container", lambda: container)
    out = CliRunner().invoke(cli.app, ["brand", "Togg"])
    assert out.exit_code == 0 and lookup.queries == 0 and "Arama ilgisi çalıştırılmadı" in out.output
    out = CliRunner().invoke(cli.app, ["brand", "Togg", "--search-interest"])
    assert out.exit_code == 0 and "bulundu" in out.output and "arama_payi hesaplanmadı" in out.output and "Listede yok:" in out.output
    assert "BigQuery:" in out.output


def test_cli_limits_show_the_block(monkeypatch, container):
    monkeypatch.setattr(cli, "container", lambda: container)
    out = CliRunner().invoke(cli.app, ["brand", "Migros", "--search-interest"])
    assert out.exit_code == 0 and "yalnızca ilişkili terim" in out.output


def test_mcp_tool_and_flag(monkeypatch, container, lookup):
    monkeypatch.setattr(mcp_server, "container", lambda: container)
    assert mcp_server.analyze_brand("Togg")["search_interest"]["status"] == "not_run" and lookup.queries == 0
    assert mcp_server.analyze_brand("Togg", search_interest=True)["search_interest"]["status"] == "done"
    out = mcp_server.analyze_search_interest("Togg", "otomotiv", ["Tesla"])
    assert out["status"] == "done" and [t["label"] for t in out["terms"]][:2] == ["Togg", "Tesla"]
    json.dumps(out)
    features = mcp_server.build_brand_features("Togg", search_interest=True)
    assert "search_interest" in features["families"]


@pytest.fixture
def client(catalog_repo, lookup):
    container = make_container(catalog_repo, lookup)
    return TestClient(create_app(lambda: container, token=TOKEN), base_url=BASE, raise_server_exceptions=False)


def test_web_api_is_opt_in(client, lookup):
    res = client.post("/api/brand/analyze", json={"query": "Togg", **PERIOD}, headers=HEADERS)
    assert res.status_code == 200 and res.json()["search_interest"]["status"] == "not_run" and lookup.queries == 0
    res = client.post("/api/brand/analyze", json={"query": "Togg", "search_interest": True, **PERIOD}, headers=HEADERS)
    assert res.status_code == 200 and res.json()["search_interest"]["status"] == "done"
    res = client.post("/api/brand/features", json={"query": "Togg", "search_interest": True, **PERIOD}, headers=HEADERS)
    assert res.status_code == 200 and "search_interest" in res.json()["families"]
    res = client.post("/api/brand/export", json={"query": "Togg", "search_interest": True, "format": "csv", **PERIOD}, headers=HEADERS)
    assert res.status_code == 200 and b"marka_ilgi_degisim" in res.content and b"marka_ilgisi" not in res.content


def render(path: Path, out: Path) -> str:
    subprocess.run([sys.executable, str(REPORT), "--analysis", str(path), "--out", str(out)], check=True, capture_output=True, text=True)
    return out.read_text(encoding="utf-8")


def test_report_section(container, tmp_path):
    evil = "<script>alert(1)</script>"
    result = container.analyze_brand("Togg", search_interest=True)
    result["search_interest"]["terms"][0]["label"] += evil
    path = tmp_path / "brand.json"
    path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    html = render(path, tmp_path / "r.html")
    assert "Arama ilgisi" in html and "Arama payı neden yok?" in html and "pazar payı değildir" in html and evil not in html
    assert html.index("Rakipler") < html.index('id="search"')


def test_report_says_when_it_was_not_run_and_for_a_name_without_a_source(container, tmp_path):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps(container.analyze_brand("Togg"), ensure_ascii=False), encoding="utf-8")
    assert "Arama ilgisi çalıştırılmadı" in render(path, tmp_path / "a.html")
    path.write_text(json.dumps(container.analyze_brand("Migros", search_interest=True), ensure_ascii=False), encoding="utf-8")
    html = render(path, tmp_path / "b.html")
    assert "yalnızca ilişkili terim" in html and "migros" in html
