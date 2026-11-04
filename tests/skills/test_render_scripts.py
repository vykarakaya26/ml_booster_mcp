"""Skill render scripts: end-to-end generation, XSS resistance and stdlib-only imports."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mlbooster.domain.services.quality import audit_panel
from mlbooster.interfaces.presenters import quality_dict

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"
EVIL = '<script>alert("x")</script>'


@pytest.fixture
def artifacts(tmp_path):
    idx = pd.date_range("2015-01-31", "2026-09-30", freq="ME")
    rng = np.random.default_rng(1)
    panel = pd.DataFrame(
        {"tufe_yoy": np.linspace(8, 30, len(idx)) + rng.normal(0, 1, len(idx)), EVIL: rng.normal(0, 1, len(idx))},
        index=idx,
    )
    panel.index.name = "date"
    panel.to_csv(tmp_path / "panel.csv", date_format="%Y-%m-%d")
    audit = quality_dict(audit_panel(panel, date(2026, 10, 30)), {"kredi_faizi": EVIL})
    (tmp_path / "audit.json").write_text(json.dumps(audit), encoding="utf-8")
    sector_map = {
        "sector": {"id": "telekom", "name": "Telekom", "ml_use_cases": ["Churn"]},
        "sources": [
            {"id": "evds3", "name": "EVDS3", "roles": ["makro"], "access": "rest_api", "frequency": ["M"],
             "publication_lag_days": 1, "verification_status": "verified", "provider_ready": True, "license": EVIL},
        ],
        "macro_features": [
            {"name": "tufe_yoy", "source": "evds3", "series": "TP.TUKFIY2025.GENEL", "transform": "yoy_pct", "status": "ready", "discover_hint": None},
            {"name": "x", "source": "evds3", "series": None, "transform": "level", "status": "pending_discovery", "discover_hint": EVIL},
        ],
        "market_share": [{"source": "btk", "metric": "abone payı", "is_proxy": False, "notes": None}],
    }  # fmt: skip
    (tmp_path / "map.json").write_text(json.dumps(sector_map), encoding="utf-8")
    (tmp_path / "narrative.md").write_text(f"## Ana bulgular\n- **TÜFE** %31,5 {EVIL}\n\n<img src=x onerror=alert(1)>\n", encoding="utf-8")
    assumptions = json.loads((SKILLS / "_shared/assets/assumptions.example.json").read_text(encoding="utf-8"))
    assumptions["assumptions"][0]["statement"] += EVIL
    (tmp_path / "assumptions.json").write_text(json.dumps(assumptions, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _run(script: Path, *args: str) -> None:
    subprocess.run([sys.executable, "-I", str(script), *args], check=True, capture_output=True, text=True)


def _assert_safe(html: str) -> None:
    assert EVIL not in html and "<img src=x" not in html
    assert html.count("<script>") == 1  # only our own tooltip script


def test_audit_report(artifacts):
    out = artifacts / "audit.html"
    _run(SKILLS / "data-quality-audit/scripts/render_audit_report.py", "--audit", str(artifacts / "audit.json"),
         "--panel", str(artifacts / "panel.csv"), "--sector", "telekom", "--assumptions", str(artifacts / "assumptions.json"),
         "--out", str(out))  # fmt: skip
    html = out.read_text(encoding="utf-8")
    _assert_safe(html)
    assert "Veri kalitesi denetimi — telekom" in html and "data-chart=" in html
    assert "Dağılım ve stationarity" in html and "Girdiler ve varsayımlar" in html and 'lang="tr"' in html
    assert 'id="findings"' in html and 'href="#series-summary"' in html


def test_sector_report(artifacts):
    out = artifacts / "report.html"
    _run(SKILLS / "sector-analysis/scripts/render_sector_report.py", "--map", str(artifacts / "map.json"),
         "--panel", str(artifacts / "panel.csv"), "--audit", str(artifacts / "audit.json"),
         "--narrative", str(artifacts / "narrative.md"), "--assumptions", str(artifacts / "assumptions.json"),
         "--out", str(out))  # fmt: skip
    html = out.read_text(encoding="utf-8")
    _assert_safe(html)
    assert "2 varsayım kullanıcı tarafından onaylanmadı (A1, A2)" in html
    assert "<strong>TÜFE</strong>" in html and "Telekom sektörü" in html and "Metodoloji ve sınırlar" in html
    assert "Seri kodu bekleniyor" in html and "Panelde" in html


def test_skill_scripts_use_only_stdlib():
    allowed_local = {"mlb_report"}
    for path in [*SKILLS.glob("*/scripts/*.py"), *SKILLS.glob("_shared/mlb_report/*.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module] if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 else []
            )
            for name in names:
                top = name.split(".")[0]
                assert top in sys.stdlib_module_names or top in allowed_local, f"{path.name}: {name}"


def test_every_skill_has_valid_frontmatter():
    for skill in SKILLS.iterdir():
        if skill.name.startswith("_") or not skill.is_dir():
            continue
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        front = text.split("---")[1]
        assert f"name: {skill.name}" in front
        assert "description:" in front and len(front) < 2500
        for ref in ("references/", "scripts/", "assets/"):
            for mention in {word for word in text.split() if ref in word}:
                clean = mention.strip("`\"'(),").rstrip("`\"').,:")
                rel = clean.split(ref, 1)[1]
                base = SKILLS / "_shared" if "_shared/" in clean else skill
                if rel and "*" not in rel and "<" not in rel:
                    assert (base / ref / rel).exists(), f"{skill.name}: {base.name}/{ref}{rel} missing"


def test_assumptions_validation():
    sys.path.insert(0, str(SKILLS / "_shared"))
    from mlb_report.assumptions import AssumptionsError, validate

    good = json.loads((SKILLS / "_shared/assets/assumptions.example.json").read_text(encoding="utf-8"))
    validate(good)
    for broken in (
        {"context": {}, "assumptions": []},
        {"context": {}, "assumptions": [{"id": "A1", "topic": "x", "statement": "y", "kind": "user", "confirmed": False}]},
        {"context": {}, "assumptions": [{"id": "1", "topic": "x", "statement": "y", "kind": "data", "confirmed": True}]},
        {"context": {}, "assumptions": [{"id": "A1", "topic": "x", "statement": "y", "kind": "guess", "confirmed": True}]},
    ):
        with pytest.raises(AssumptionsError):
            validate(broken)


def test_stale_assumptions_flagged():
    sys.path.insert(0, str(SKILLS / "_shared"))
    from datetime import date as _d

    from mlb_report.assumptions import section, stale

    data = json.loads((SKILLS / "_shared/assets/assumptions.example.json").read_text(encoding="utf-8"))
    data["assumptions"][0]["as_of"] = "2025-01-01"
    assert [a["id"] for a in stale(data, _d(2026, 10, 30))] == ["A1"]
    assert "güncel değil — doğrulanmalı" in section(data)


def test_market_share_report(tmp_path):
    from mlbooster.domain.services.market_share import ShareConfig, analyze

    sample = pd.read_csv(SKILLS / "market-share/assets/sample_market_share.csv")
    sample.loc[sample["entity"] == "Marka G", "entity"] = EVIL[:60]
    (tmp_path / "share.json").write_text(json.dumps(analyze(sample, ShareConfig(focus="Marka B"))), encoding="utf-8")
    comp = json.loads((SKILLS / "market-share/assets/competitors.example.json").read_text(encoding="utf-8"))
    comp["competitors"][0]["evidence"] += EVIL
    (tmp_path / "competitors.json").write_text(json.dumps(comp, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "narrative.md").write_text(f"## Ana bulgu\nPay %26,5 {EVIL}", encoding="utf-8")
    out = tmp_path / "report.html"
    _run(SKILLS / "market-share/scripts/render_share_report.py", "--analysis", str(tmp_path / "share.json"),
         "--competitors", str(tmp_path / "competitors.json"), "--narrative", str(tmp_path / "narrative.md"),
         "--source", "Sentetik örnek", "--out", str(out))  # fmt: skip
    html = out.read_text(encoding="utf-8")
    _assert_safe(html)
    for heading in ("Pazar yapısı ve yoğunlaşma", "Oyuncular ve pay dinamikleri", "Rakip seti", "Segment kırılımı", "Coverage ve veri kalitesi", "Yöntem"):
        assert heading in html
    assert "Doğrudan rakip" in html and "pay etkisi" in html and "data-chart=" in html


def _brand_analysis(tmp_path, catalog_repo, query, evidence=(), **kwargs):
    from fakes import BrandFactory, InMemoryEvidenceStore, InMemoryObservationStore, InMemoryPanelStore
    from mlbooster.bootstrap import Container
    from mlbooster.interfaces.presenters import brand_features_dict

    c = Container(catalog=catalog_repo, providers=BrandFactory(), observations=InMemoryObservationStore(),
                  panels=InMemoryPanelStore(), overrides=catalog_repo, evidence_store=InMemoryEvidenceStore())  # fmt: skip
    for draft in evidence:
        c.record_evidence(draft)
    out = c.analyze_brand(query, **kwargs)
    if out["status"] == "resolved":
        out["features"] = brand_features_dict(c.brand_features(query, out["brand"]["id"], kwargs.get("start"), kwargs.get("end")))
    path = tmp_path / "brand.json"
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out, path


def test_brand_report_starts_with_the_brand_and_shows_every_section(tmp_path, catalog_repo):
    out, path = _brand_analysis(tmp_path, catalog_repo, "Togg", start=date(2022, 1, 1), end=date(2025, 12, 31))
    out["competitors"][0]["reasons"].append({"series": EVIL, "reason": EVIL})
    out["headline"] += EVIL
    out["sources"][0]["license"] += EVIL
    out["features"]["brand_columns"][0]["description"] += EVIL
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    comp = {"competitors": [{"entity": out["competitors"][0]["entity"], "tier": "direct", "evidence": EVIL, "confidence": "high"}]}
    (tmp_path / "competitors.json").write_text(json.dumps(comp, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "narrative.md").write_text(f"## Ana bulgu\n{EVIL}", encoding="utf-8")
    target = tmp_path / "report.html"
    _run(SKILLS / "market-share/scripts/render_brand_report.py", "--analysis", str(path), "--competitors", str(tmp_path / "competitors.json"),
         "--narrative", str(tmp_path / "narrative.md"), "--out", str(target))  # fmt: skip
    html = target.read_text(encoding="utf-8")
    _assert_safe(html)
    order = [html.index(f"<h2>{h}</h2>") for h in ("Marka özeti", "Sektör ve konum", "Rakipler", "Pay geçmişi", "Yoğunlaşma", "Segmentler", "Feature&#x27;lar", "Kaynaklar ve lisans", "Yöntem")]
    assert order == sorted(order)
    assert "Doğrudan rakip" in html and "marka_pay" in html and "data-chart=" in html and "Kaynak: Otomotiv Distribütörleri" in html


def test_brand_report_of_a_name_without_a_source_has_no_share(tmp_path, catalog_repo):
    _, path = _brand_analysis(tmp_path, catalog_repo, "Migros")
    target = tmp_path / "report.html"
    _run(SKILLS / "market-share/scripts/render_brand_report.py", "--analysis", str(path), "--out", str(target))
    html = target.read_text(encoding="utf-8")
    assert "pazar payı hesaplanmadı" in html and "Perakende" in html and "BQT.ILGI.MIGROS" in html and "Marka özeti" not in html


def _draft(**kwargs):
    from mlbooster.domain.services.evidence import EvidenceDraft

    year = date.today().year - 1
    base = {"brand": "Migros", "value": 24.5, "basis": "revenue", "period": str(year), "published": f"{year + 1}-03-01", "publisher": "Migros Ticaret",
            "url": "https://www.migroskurumsal.com/rapor.pdf", "quote": "pazar payımız %24,5 oldu", "sector": "perakende"}  # fmt: skip
    return EvidenceDraft(**{**base, **kwargs})


def test_brand_report_of_a_name_without_a_source_leads_with_evidence(tmp_path, catalog_repo):
    out, path = _brand_analysis(tmp_path, catalog_repo, "Migros", [_draft()])
    item = out["evidence"]["current"][0]
    item["publisher"] += EVIL
    item["quote"] += EVIL
    out["evidence"]["historical"] = [{**item, "url": "javascript:alert(1)", "period": "2019", "publisher": "eski yayın"}]
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    target = tmp_path / "report.html"
    _run(SKILLS / "market-share/scripts/render_brand_report.py", "--analysis", str(path), "--out", str(target))
    html = target.read_text(encoding="utf-8")
    _assert_safe(html)
    assert html.index("Yayımlanmış pay bulguları</h2>") < html.index("<h2>Sonuç</h2>") < html.index("Bunun yerine kullanılabilecekler")
    assert "derlenmiş; hesaplanmış pay değildir" in html and "%24,5" in html and 'rel="noopener noreferrer"' in html
    assert "javascript:" not in html and "Marka özeti" not in html


def test_brand_report_shows_computed_and_published_side_by_side(tmp_path, catalog_repo):
    start, end = date(2022, 1, 1), date(2025, 12, 31)
    togg = _draft(brand="Togg", value=1.5, quote="payımız %1,5", basis="units", url="https://www.togg.com.tr/x", sector="otomotiv")
    bad = _draft(brand="Togg", value=5, quote="payımız %5", basis="units", url="https://www.rekabet.gov.tr/x", publisher="Rekabet Kurumu", sector="otomotiv")
    out, path = _brand_analysis(tmp_path, catalog_repo, "Togg", [togg, bad], start=start, end=end)
    assert out["evidence"]["contradictions"] and len(out["evidence"]["comparison"]) == 2
    target = tmp_path / "report.html"
    _run(SKILLS / "market-share/scripts/render_brand_report.py", "--analysis", str(path), "--out", str(target))
    html = target.read_text(encoding="utf-8")
    assert html.index("<h2>Marka özeti</h2>") < html.index("Yayımlanmış pay bulguları</h2>") < html.index("Kaynaklar ve lisans</h2>")
    assert "Hesaplanan pay ile karşılaştırma" in html and "Çelişen bulgular" in html and "Olası neden" in html


def test_brand_report_refuses_an_unclear_name(tmp_path, catalog_repo):
    _, path = _brand_analysis(tmp_path, catalog_repo, "Anadolu")
    done = subprocess.run([sys.executable, str(SKILLS / "market-share/scripts/render_brand_report.py"), "--analysis", str(path), "--out", str(tmp_path / "r.html")],
                          capture_output=True, text=True)  # fmt: skip
    assert done.returncode != 0 and "--choice" in done.stderr and not (tmp_path / "r.html").exists()


def test_competitors_validation():
    sys.path.insert(0, str(SKILLS / "_shared"))
    from mlb_report.share import CompetitorsError, validate_competitors

    validate_competitors(json.loads((SKILLS / "market-share/assets/competitors.example.json").read_text(encoding="utf-8")))
    for broken in (
        {"competitors": [{"entity": "A", "tier": "rival"}]},
        {"competitors": [{"entity": "A", "tier": "direct", "confidence": "certain"}]},
        {"competitors": [{"entity": "A", "tier": "direct", "source_url": "javascript:alert(1)"}]},
        {"competitors": [{"tier": "direct"}]},
    ):
        with pytest.raises(CompetitorsError):
            validate_competitors(broken)


def test_prep_and_feature_reports(tmp_path):
    from mlbooster.domain.services.feature_engineering import FeatureConfig, engineer
    from mlbooster.domain.services.preprocessing import PrepConfig, prepare
    from mlbooster.interfaces.presenters import features_dict, prep_dict

    rng = np.random.default_rng(4)
    idx = pd.date_range("2013-01-31", periods=140, freq="ME")
    panel = pd.DataFrame({"target": np.cumsum(rng.normal(0, 1, 140)), EVIL: rng.lognormal(0, 1, 140), "x": rng.normal(0, 1, 140)}, index=idx)
    panel.index.name = "date"
    prep = prepare(panel, PrepConfig(target="target", train_end=date(2021, 12, 31)))
    (tmp_path / "prep.json").write_text(json.dumps(prep_dict(prep, "telekom")), encoding="utf-8")
    panel.to_csv(tmp_path / "raw.csv", date_format="%Y-%m-%d")
    prep.panel.to_csv(tmp_path / "prepared.csv", date_format="%Y-%m-%d")
    out = tmp_path / "prep.html"
    _run(SKILLS / "data-prep/scripts/render_prep_report.py", "--prep", str(tmp_path / "prep.json"), "--raw", str(tmp_path / "raw.csv"),
         "--prepared", str(tmp_path / "prepared.csv"), "--out", str(out))  # fmt: skip
    html = out.read_text(encoding="utf-8")
    _assert_safe(html)
    assert "Dağılım profili ve uygulanan adımlar" in html and "Yeo–Johnson" in html and "(hedef)" in html

    fe = engineer(prep.panel, [], FeatureConfig(target="target", train_end=date(2021, 12, 31)))
    (tmp_path / "fe.json").write_text(json.dumps(features_dict(fe, "telekom")), encoding="utf-8")
    out = tmp_path / "fe.html"
    _run(SKILLS / "feature-engineering/scripts/render_feature_report.py", "--features", str(tmp_path / "fe.json"), "--out", str(out))
    html = out.read_text(encoding="utf-8")
    _assert_safe(html)
    assert "Feature aileleri" in html and "Yöntem" in html
