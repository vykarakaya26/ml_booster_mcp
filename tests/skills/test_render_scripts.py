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

from mlbooster.domain.services.backtest import BacktestConfig, rolling_origin_backtest
from mlbooster.domain.services.quality import audit_panel
from mlbooster.interfaces.presenters import backtest_dict, quality_dict

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
    bt = rolling_origin_backtest(panel, BacktestConfig(target="tufe_yoy", min_train=36))
    (tmp_path / "bt.json").write_text(json.dumps(backtest_dict(bt, "telekom", 24)), encoding="utf-8")
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
    (tmp_path / "notes.md").write_text(f"Naive kazandı {EVIL}", encoding="utf-8")
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


def test_backtest_report(artifacts):
    out = artifacts / "bt.html"
    _run(SKILLS / "forecast-baseline/scripts/render_backtest_report.py", "--backtest", str(artifacts / "bt.json"),
         "--notes", str(artifacts / "notes.md"), "--assumptions", str(artifacts / "assumptions.json"), "--out", str(out))  # fmt: skip
    html = out.read_text(encoding="utf-8")
    _assert_safe(html)
    assert "Model karşılaştırması" in html and "Gerçekleşen" in html


def test_sector_report(artifacts):
    out = artifacts / "report.html"
    _run(SKILLS / "sector-analysis/scripts/render_sector_report.py", "--map", str(artifacts / "map.json"),
         "--panel", str(artifacts / "panel.csv"), "--audit", str(artifacts / "audit.json"),
         "--narrative", str(artifacts / "narrative.md"), "--backtest", str(artifacts / "bt.json"),
         "--assumptions", str(artifacts / "assumptions.json"), "--out", str(out))  # fmt: skip
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


def _external(values, published="2026-10-01"):
    return {
        "retrieved": "2026-10-30",
        "forecasts": [
            {"institution": f"Banka{i}", "variable": "usdtry", "horizon": "2026-12", "value": v, "unit": "TRY",
             "published": published, "source_url": "https://example.com/n", "primary": False}
            for i, v in enumerate(values)
        ],
    }


def test_external_consensus_rule_and_validation():
    sys.path.insert(0, str(SKILLS / "_shared"))
    from mlb_report.external import ExternalForecastError, consensus, section, validate

    tight = consensus([50.0, 50.5, 51.0, 51.2])
    assert tight["rule"] == "mean" and tight["consensus"] == pytest.approx(50.675)
    wide = consensus([45.0, 50.0, 52.0, 70.0])  # a single outlying view drags the mean
    assert wide["dispersed"] and wide["consensus"] == pytest.approx(51.0) and wide["mean"] > 54
    assert consensus([50.0, 60.0])["rule"].startswith("median")
    for bad in ({"forecasts": []}, _external([1.0]) | {"forecasts": [{"institution": "x"}]}):
        with pytest.raises(ExternalForecastError):
            validate(bad)
    evil = _external([50.0])
    evil["forecasts"][0]["source_url"] = "javascript:alert(1)"
    with pytest.raises(ExternalForecastError):
        validate(evil)
    old = _external([50.0, 51.0, 52.0], published="2026-01-01")
    old["forecasts"][0]["institution"] = EVIL
    html = section(old, latest={"usdtry": ("2026-10", 49.7)})
    assert "güncel değil" in html and EVIL not in html and "Son değere göre" in html


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


def test_competitors_validation():
    sys.path.insert(0, str(SKILLS / "market-share/scripts"))
    from render_share_report import CompetitorsError, validate_competitors

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
    bt = {"config": {"eval_start": "2021-12-31"}, "best_model": "naive", "common_origins": 20,
          "metrics": [{"model": "naive", "mae": 1.0}, {"model": "ridge", "mae": 0.9}]}  # fmt: skip
    (tmp_path / "bt.json").write_text(json.dumps(bt), encoding="utf-8")
    out = tmp_path / "fe.html"
    _run(SKILLS / "feature-engineering/scripts/render_feature_report.py", "--features", str(tmp_path / "fe.json"),
         "--bt-base", str(tmp_path / "bt.json"), "--bt-features", str(tmp_path / "bt.json"), "--out", str(out))  # fmt: skip
    html = out.read_text(encoding="utf-8")
    _assert_safe(html)
    assert "Feature aileleri" in html and "Seçimin tahmin performansına katkısı" in html and "%10,0" in html
