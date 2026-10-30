"""Skill render script'leri: uçtan uca üretim, XSS dayanıklılığı ve yalnızca stdlib kullanımı."""

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
            {"name": "tufe_yoy", "source": "evds3", "series": "TP.TUKFIY2025.GENEL", "transform": "yoy_pct", "status": "hazir", "discover_hint": None},
            {"name": "x", "source": "evds3", "series": None, "transform": "level", "status": "kesif_bekliyor", "discover_hint": EVIL},
        ],
        "market_share": [{"source": "btk", "metric": "abone payı", "is_proxy": False, "notes": None}],
    }  # fmt: skip
    (tmp_path / "map.json").write_text(json.dumps(sector_map), encoding="utf-8")
    (tmp_path / "narrative.md").write_text(f"## Ana bulgular\n- **TÜFE** %31,5 {EVIL}\n\n<img src=x onerror=alert(1)>\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text(f"Naive kazandı {EVIL}", encoding="utf-8")
    return tmp_path


def _run(script: Path, *args: str) -> None:
    subprocess.run([sys.executable, "-I", str(script), *args], check=True, capture_output=True, text=True)


def _assert_safe(html: str) -> None:
    assert EVIL not in html and "<img src=x" not in html
    assert html.count("<script>") == 1  # yalnızca kendi tooltip script'imiz


def test_audit_report(artifacts):
    out = artifacts / "audit.html"
    _run(SKILLS / "data-quality-audit/scripts/render_audit_report.py", "--audit", str(artifacts / "audit.json"),
         "--panel", str(artifacts / "panel.csv"), "--sector", "telekom", "--out", str(out))  # fmt: skip
    html = out.read_text(encoding="utf-8")
    _assert_safe(html)
    assert "Veri kalitesi denetimi — telekom" in html and "data-chart=" in html


def test_backtest_report(artifacts):
    out = artifacts / "bt.html"
    _run(SKILLS / "forecast-baseline/scripts/render_backtest_report.py", "--backtest", str(artifacts / "bt.json"),
         "--notes", str(artifacts / "notes.md"), "--out", str(out))  # fmt: skip
    html = out.read_text(encoding="utf-8")
    _assert_safe(html)
    assert "Model karşılaştırması" in html and "Gerçekleşen" in html


def test_sector_report(artifacts):
    out = artifacts / "report.html"
    _run(SKILLS / "sector-analysis/scripts/render_sector_report.py", "--map", str(artifacts / "map.json"),
         "--panel", str(artifacts / "panel.csv"), "--audit", str(artifacts / "audit.json"),
         "--narrative", str(artifacts / "narrative.md"), "--backtest", str(artifacts / "bt.json"), "--out", str(out))  # fmt: skip
    html = out.read_text(encoding="utf-8")
    _assert_safe(html)
    assert "<strong>TÜFE</strong>" in html and "Telekom sektörü" in html and "Metodoloji ve sınırlar" in html


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
        for ref in ("references/", "scripts/"):
            for mention in {line for line in text.split() if ref in line}:
                rel = mention.strip("`\"'(),").split(ref, 1)[1].split()[0].rstrip("`\"').,")
                if rel and "*" not in rel and "<" not in rel:
                    assert (skill / ref / rel).exists(), f"{skill.name}: {ref}{rel} yok"
