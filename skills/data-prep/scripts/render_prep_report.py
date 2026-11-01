#!/usr/bin/env python3
"""Renders a distribution-based preprocessing report (HTML) from `mlbooster prep --json` output.

Usage:
  python3 render_prep_report.py --prep prep.json --out prep.html \
      [--raw panel.csv --prepared prepared.csv] [--notes notes.md] [--assumptions assumptions.json]
Requires only the standard library.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))

from mlb_report import assumptions as A
from mlb_report import html as H
from mlb_report.data import read_json, read_panel_csv
from mlb_report.svg import LineChart, Series, line_chart

DESCRIPTION = "mlbooster prep --json çıktısından dağılıma dayalı ön işleme raporu (HTML) üretir."
STEP_LABELS = {
    "ffill": "Missing value: forward fill", "diff": "Birinci fark", "log": "Log", "yeo_johnson": "Yeo–Johnson",
    "outlier_winsorize": "Outlier: winsorize + gösterge", "outlier_flag": "Outlier: gösterge",
    "scale_standard": "Scaling: ortalama/std", "scale_robust": "Scaling: medyan/IQR",
}  # fmt: skip
FAMILY = {"linear": "doğrusal / düzenlileştirilmiş", "tree": "ağaç tabanlı", "neural": "yapay sinir ağı"}
STATIONARITY = {"stationary": "durağan", "unit_root": "unit root", "break_suspected": "structural break şüphesi", "inconclusive": "kararsız"}
CENTER_SCALE = {"mean/standard deviation": "ortalama/standart sapma", "median/IQR": "medyan/IQR"}


def num(v: float | None, d: int = 2) -> str:
    return H.num(v, d)


def arrow(before: float | None, after: float | None) -> str:
    return f"{num(before)} → {num(after)}" if before is not None and after is not None else num(before)


def build(prep: dict, raw: tuple | None, prepared: tuple | None, notes: str | None, assumptions: dict | None) -> str:
    cfg, cols = prep["config"], prep["columns"]
    robust = [c for c in cols if c["before"]["center_scale"] == "median/IQR"]
    changed = [c for c in cols if c["steps"]]
    flags = prep.get("added_columns", [])
    sections = []
    body = H.kpis([
        ("Değişken", str(len(cols)), f"{prep['period']['rows']} dönem"),
        ("Robust merkez/ölçek", str(len(robust)), "medyan ve IQR seçilen değişken"),
        ("Dönüştürülen", str(len(changed)), "en az bir ön işleme adımı"),
        ("Eklenen gösterge", str(len(flags)), "missing value / outlier işaretleri"),
        ("Eğitim penceresi sonu", cfg["train_end"], "parametreler yalnızca bu tarihe kadar"),
        ("Model ailesi", FAMILY.get(cfg["model_family"], cfg["model_family"]), None),
    ])  # fmt: skip
    for note in prep.get("notes", []):
        body += f'<p><span class="badge serious"><span aria-hidden="true">▲</span> Not</span> {H.esc(note)}</p>'
    if notes:
        body += f'<div class="narrative">{H.markdown(notes)}</div>'
    sections.append(H.section("Özet", body, "summary"))

    stats, decisions = [], []
    for c in cols:
        b, a = c["before"], c["after"]
        steps = " → ".join(STEP_LABELS.get(s["step"], s["step"]) for s in c["steps"]) or "—"
        name = H.esc(c["feature"]) + (" <strong>(hedef)</strong>" if c["is_target"] else "")
        stats.append((name, b["n"], f"%{num(b['missing_ratio'] * 100, 1)}", num(b["mean"]), num(b["std"]), num(b["median"]),
                      num(b["mad"]), num(b["iqr"]), f"%{num(b['outlier_share'] * 100, 1)}"))  # fmt: skip
        decisions.append((name, arrow(b["skew"], a["skew"]), arrow(b["excess_kurtosis"], a["excess_kurtosis"]),
                          H.esc(STATIONARITY.get(b["stationarity"], b["stationarity"] or "—")), H.esc(CENTER_SCALE.get(b["center_scale"], b["center_scale"])), H.esc(steps)))  # fmt: skip
    body = "<h3>Dağılım istatistikleri (eğitim penceresi)</h3>" + H.table(
        ["Değişken", "n", "Eksik", "Ortalama", "Std. sapma", "Medyan", "MAD (σ ölçekli)", "IQR", "Outlier oranı"],
        stats, numeric=[1, 2, 3, 4, 5, 6, 7, 8], raw=[0],
    )
    body += "<h3>Kararlar</h3>" + H.table(
        ["Değişken", "Çarpıklık (önce → sonra)", "Aşırı basıklık (önce → sonra)", "Durağanlık", "Merkez / ölçek", "Adımlar"],
        decisions, numeric=[1, 2], raw=[0, 3, 4, 5],
    )
    body += ('<p class="muted">İstatistikler eğitim penceresinde hesaplanmıştır. MAD, normal dağılımda standart sapmayla '
             "karşılaştırılabilmesi için 1,4826 ile ölçeklenmiştir. Outlier oranı, Iglewicz–Hoaglin modified z-score'unun "
             f"|z| &gt; {num(cfg['outlier_k'], 1)} olduğu gözlemlerin payıdır.</p>")  # fmt: skip
    sections.append(H.section("Dağılım profili ve uygulanan adımlar", body, "profile"))

    detail = ""
    for c in changed:
        items = "".join(
            f"<li><strong>{H.esc(STEP_LABELS.get(s['step'], s['step']))}</strong>: {H.esc(s['reason'])}{_params(s)}</li>"
            for s in c["steps"]
        )
        detail += f"<h3>{H.esc(c['feature'])}</h3><ul>{items}</ul>"
    if detail:
        sections.append(H.section("Adımların gerekçeleri", detail, "rationale"))

    if raw and prepared:
        charts = ""
        raw_dates, raw_cols = raw
        prep_dates, prep_cols = prepared
        for c in sorted(changed, key=lambda c: -len(c["steps"]))[:4]:
            name = c["feature"]
            if name in raw_cols and name in prep_cols:
                charts += line_chart(LineChart(raw_dates, [Series("Ham", raw_cols[name])], f"{name}: ham"))
                charts += line_chart(LineChart(prep_dates, [Series("Ön işlenmiş", prep_cols[name], 2)], f"{name}: ön işlenmiş"))
        if charts:
            sections.append(H.section("Önce ve sonra", f'<div class="grid-2">{charts}</div>', "charts"))

    if assumptions:
        sections.append(A.section(assumptions))
    sections.append(H.section("Yöntem", METHOD, "method"))
    nav = [("summary", "Özet"), ("profile", "Profil"), ("rationale", "Gerekçeler"), ("method", "Yöntem")]
    return H.page(f"Veri ön işleme — {prep['sector']}", f"{prep['period']['first']} – {prep['period']['last']} · "
                  f"{FAMILY.get(cfg['model_family'], cfg['model_family'])} model için", sections,
                  "Veri: TCMB EVDS ve katalogdaki diğer kaynaklar; ön işleme mlbooster prep.", nav)  # fmt: skip


def _params(step: dict) -> str:
    p = step.get("params") or {}
    parts = []
    if "lambda" in p:
        parts.append(f"λ = {num(p['lambda'], 2)}")
    if "center" in p and "scale" in p:
        parts.append(f"merkez {num(p['center'], 3)}, ölçek {num(p['scale'], 3)}")
    if "count_train" in p:
        parts.append(f"eğitimde {p['count_train']} gözlem işaretlendi")
    return f" <span class=\"muted\">({H.esc('; '.join(parts))})</span>" if parts else ""


METHOD = """
<ul>
<li><strong>Merkez ve ölçek seçimi:</strong> |çarpıklık| &gt; 1, aşırı basıklık &gt; 3 ya da aykırı oranı &gt; %2 ise ortalama ve standart
sapma yerine medyan ve IQR kullanılır. Ortalama ve standart sapma tek bir uç değerden büyük ölçüde etkilenir; medyan ve IQR
etkilenmez.</li>
<li><strong>Outlier'lar:</strong> nedensel (causal) Hampel filtresiyle tespit edilir: t anındaki değer, yalnızca t−1'e kadarki 24 dönemin
medyanı ve MAD'ı ile karşılaştırılır. Doğrusal modellerde değer bu aralığa winsorize edilir ve gösterge değişken eklenir; ağaç
modellerinde değer korunur. Türkiye verisindeki uç değerler çoğunlukla gerçek şoklardır ve silinmez.</li>
<li><strong>Dönüşümler:</strong> pozitif ve mertebe farkı büyük serilerde log; işaret içeren çarpık serilerde Yeo–Johnson (λ eğitim
penceresinde profile likelihood ile tahmin edilir). ADF ve KPSS birlikte birim kök gösteriyorsa doğrusal modelde birinci fark alınır.</li>
<li><strong>Leakage önlemi:</strong> bütün parametreler (λ, merkez, ölçek) yalnızca eğitim penceresinden tahmin edilir ve test dönemine aynı
değerlerle uygulanır; missing value'lar yalnızca forward fill ile doldurulur (geleceğe ait değer kullanılmaz). Hedef değişkenin dağılımı değiştirilmez.</li>
</ul>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=DESCRIPTION)
    ap.add_argument("--prep", required=True, type=Path, help="mlbooster prep --json çıktısı")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--raw", type=Path, help="Ham panel CSV (build-panel --out)")
    ap.add_argument("--prepared", type=Path, help="Ön işlenmiş panel CSV (prep --out)")
    ap.add_argument("--notes", type=Path, help="Yorum (Markdown)")
    ap.add_argument("--assumptions", type=Path, help="Girdi/varsayım kaydı (assumptions.json)")
    args = ap.parse_args()
    try:
        assumptions = A.load(args.assumptions) if args.assumptions else None
    except A.AssumptionsError as exc:
        sys.exit(f"Hata: {exc}")
    raw = read_panel_csv(args.raw) if args.raw else None
    prepared = read_panel_csv(args.prepared) if args.prepared else None
    notes = args.notes.read_text(encoding="utf-8") if args.notes else None
    html = build(read_json(args.prep), raw, prepared, notes, assumptions)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"Rapor → {args.out}")


if __name__ == "__main__":
    main()
