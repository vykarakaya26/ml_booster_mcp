#!/usr/bin/env python3
"""Renders a feature generation and selection report (HTML) from `mlbooster features --json` output.

Usage:
  python3 render_feature_report.py --features features.json --out features.html \
      [--notes notes.md] [--assumptions assumptions.json]
Requires only the standard library.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))

from mlb_report import assumptions as A
from mlb_report import html as H
from mlb_report.data import read_json
from mlb_report.svg import bar_chart

DESCRIPTION = "mlbooster features --json çıktısından feature üretimi ve seçimi raporu (HTML) üretir."
FAMILIES = {
    "base": "Panel feature'ı", "indicator": "Ön işleme göstergesi", "sector_recipe": "Sektör tarifi",
    "lag": "Lag", "rolling_mean": "Rolling mean", "rolling_std": "Rolling std",
    "momentum": "Momentum", "acceleration": "İvme (acceleration)", "zscore": "Z-score", "ewm": "EWM",
    "calendar": "Takvim", "event": "Olay / rejim",
}  # fmt: skip
REASONS = {"selected": "seçildi"}


def num(v: float | None, d: int = 2) -> str:
    return H.num(v, d)


def build(fe: dict, notes: str | None, assumptions: dict | None) -> str:
    selected = fe.get("selected", [])
    screening = fe.get("screening", [])
    by_name = {r["name"]: r for r in screening}
    sections = []
    kpi = [("Üretilen feature", str(fe["n_features"]), f"{fe['period']['rows']} dönem"),
           ("Sektör tarifi", str(sum(1 for _ in fe.get("recipes", []))), f"{len(fe.get('skipped_recipes', {}))} tarif atlandı")]  # fmt: skip
    if fe.get("label"):
        kind = "Değişim" if fe.get("label_mode") == "change" else "Düzey"
        kpi += [("Etiket türü", kind, f"{fe['label']} · eğitim sonu {fe.get('train_end') or '—'}"),
                ("Seçilen", str(len(selected)), f"{len(screening)} aday tarandı")]  # fmt: skip
    body = H.kpis(kpi)
    for note in fe.get("notes", []):
        body += f"<p>{H.esc(note)}</p>"
    if notes:
        body += f'<div class="narrative">{H.markdown(notes)}</div>'
    sections.append(H.section("Özet", body, "summary"))

    fam_rows = [(H.esc(FAMILIES.get(k, k)), v) for k, v in sorted(fe["families"].items(), key=lambda kv: -kv[1])]
    sections.append(H.section("Feature aileleri", H.table(["Aile", "Sayı"], fam_rows, numeric=[1], raw=[0]), "families"))

    if selected:
        rows, labels, values = [], [], []
        for s in selected:
            r = by_name.get(s["name"], {})
            rows.append((H.esc(s["name"]), H.esc(FAMILIES.get(s["family"], s["family"])), H.esc(s["description"]),
                         num(r.get("r")), num(r.get("n_eff"), 0), num(r.get("q"), 3), num(r.get("stability"), 2)))  # fmt: skip
            labels.append(s["name"])
            values.append(abs(r.get("r") or 0))
        body = bar_chart(labels, values, "Seçilen feature'lar: etiketle Spearman sıra korelasyonu (mutlak değer)", fmt=lambda v: num(v, 2))
        body += H.table(["Feature", "Aile", "Açıklama", "Spearman r", "Etkin n", "q (BH)", "Alt dönem istikrarı"],
                        rows, numeric=[3, 4, 5, 6], raw=[0, 1, 2])  # fmt: skip
        sections.append(H.section("Seçilen feature'lar", body, "selected"))
    elif fe.get("label"):
        sections.append(H.section("Seçilen feature'lar", "<p>False discovery rate ve istikrar ölçütlerini sağlayan feature bulunamadı. "
                                  "Bu, panelin hedefin kendi geçmişinin ötesinde güvenilir bilgi taşımadığını gösterebilir.</p>", "selected"))  # fmt: skip

    if screening:
        rows = [(H.esc(r["name"]), H.esc(FAMILIES.get(r.get("family"), r.get("family") or "—")), num(r.get("r")), num(r.get("n_eff"), 0),
                 num(r.get("q"), 3), num(r.get("stability"), 2), "✓" if r.get("selected") else "", H.esc(REASONS.get(r.get("reason"), r.get("reason") or "—")))
                for r in screening[:40]]  # fmt: skip
        sections.append(H.section("Tarama (ilk 40 aday)", H.table(
            ["Feature", "Aile", "r", "Etkin n", "q", "İstikrar", "Seçildi", "Gerekçe"], rows, numeric=[2, 3, 4, 5], raw=[0, 1, 7]), "screening"))

    recipes = fe.get("recipes", [])
    if recipes or fe.get("skipped_recipes"):
        rows = [(H.esc(r["name"]), H.esc(r.get("op") or "—"), H.esc(", ".join(r["inputs"])), H.esc(r["description"])) for r in recipes]
        rows += [(H.esc(n), "—", "—", H.esc(f"Atlandı: {why}")) for n, why in fe.get("skipped_recipes", {}).items()]
        sections.append(H.section("Sektör tarifleri", H.table(["Tarif", "İşlem", "Girdiler", "Açıklama"], rows, raw=[0, 1, 2, 3]), "recipes"))

    if assumptions:
        sections.append(A.section(assumptions))
    sections.append(H.section("Yöntem", METHOD, "method"))
    nav = [("summary", "Özet"), ("selected", "Seçilen"), ("screening", "Tarama"), ("recipes", "Tarifler"), ("method", "Yöntem")]
    return H.page(f"Feature üretimi ve seçimi — {fe['sector']}", f"{fe['period']['first']} – {fe['period']['last']}",
                  sections, "Veri: TCMB EVDS ve katalogdaki diğer kaynaklar; feature'lar mlbooster features ile üretildi.", nav)  # fmt: skip


METHOD = """
<ul>
<li><strong>Üretim:</strong> lag'ler (1, 2, 3, 6, 12), rolling mean ve rolling std (3, 6, 12), 3 ve 12 dönemlik
momentum, ivme, geçmiş 24 döneme göre z-score, EWM; sektör tarifleri (reel faiz, faiz farkı, kur sepeti vb.);
takvim (iş günü, bayram, Ramazan ayı ve bayram öncesi günleri); bilinen olay ve sektör düzenlemesi göstergeleri. Bütün
feature'lar nedenseldir (causal): t anındaki değer yalnızca t ve öncesindeki gözlemlerden hesaplanır.</li>
<li><strong>Etiket:</strong> hedef kalıcıysa (lag 1 otokorelasyonu ≥ 0,9) h dönemlik değişim, değilse h dönem sonraki
düzey. Düzeyler arası korelasyon, ortak trend nedeniyle sahte ilişki üretebilir.</li>
<li><strong>Seçim:</strong> yalnızca etiketi eğitim sonuna kadar gözlenmiş dönemlerde Spearman korelasyonu; otokorelasyona göre
etkin örneklem; Benjamini–Hochberg ile false discovery rate kontrolü (q ≤ 0,10); üç alt dönemin en az ikisinde aynı işaret; |r| ≥ 0,95
olan çiftlerde fazlalık eleme; seçilen feature sayısı etkin örneklem / 15 ile sınırlı (Harrell).</li>
<li><strong>Sınırlar:</strong> tarama ilişkiyi gösterir, nedenselliği göstermez. Seçim bir ön elemedir; bir modele katkısı bu
raporda ölçülmez.</li>
</ul>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=DESCRIPTION)
    ap.add_argument("--features", required=True, type=Path, help="mlbooster features --json çıktısı")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--notes", type=Path, help="Yorum (Markdown)")
    ap.add_argument("--assumptions", type=Path, help="Girdi/varsayım kaydı (assumptions.json)")
    args = ap.parse_args()
    try:
        assumptions = A.load(args.assumptions) if args.assumptions else None
    except A.AssumptionsError as exc:
        sys.exit(f"Hata: {exc}")
    notes = args.notes.read_text(encoding="utf-8") if args.notes else None
    html = build(read_json(args.features), notes, assumptions)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"Rapor → {args.out}")


if __name__ == "__main__":
    main()
