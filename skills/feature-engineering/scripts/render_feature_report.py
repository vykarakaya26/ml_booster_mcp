#!/usr/bin/env python3
"""`mlbooster features --json` çıktısından değişken üretimi ve seçim raporu (HTML) üretir.

Kullanım:
  python3 render_feature_report.py --features features.json --out features.html \
      [--bt-base bt_base.json --bt-features bt_features.json] [--notes notes.md] [--assumptions assumptions.json]
`--bt-base` ve `--bt-features`, aynı hedef ve değerlendirme başlangıcıyla (--eval-start) koşulmuş iki backtest'tir:
biri sektör paneliyle, diğeri seçilen değişkenlerle. Yalnızca standart kütüphane gerekir.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))

from mlb_report import assumptions as A  # noqa: E402
from mlb_report import html as H  # noqa: E402
from mlb_report.data import read_json  # noqa: E402
from mlb_report.svg import bar_chart  # noqa: E402

FAMILIES = {
    "temel": "Panel değişkeni", "gosterge": "Ön işlem göstergesi", "sektor_tarifi": "Sektör tarifi",
    "gecikme": "Gecikme", "hareketli_ortalama": "Hareketli ortalama", "hareketli_oynaklik": "Hareketli oynaklık",
    "momentum": "Momentum", "ivme": "İvme", "standart_skor": "Standart skor", "ussel_ortalama": "Üstel ortalama",
    "takvim": "Takvim", "olay": "Olay / rejim",
}  # fmt: skip


def num(v: float | None, d: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:,.{d}f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _mae(bt: dict, model: str) -> float | None:
    return next((m["mae"] for m in bt.get("metrics", []) if m["model"] == model), None)


def backtest_section(base: dict, feat: dict) -> str:
    naive = _mae(feat, "naive") or _mae(base, "naive")
    rows = []
    for label, bt in (("Sektör paneli", base), ("Seçilen değişkenler", feat)):
        ridge = _mae(bt, "ridge")
        best = bt.get("best_model")
        gain = (1 - ridge / naive) * 100 if ridge is not None and naive else None
        rows.append((label, num(ridge, 3), num(_mae(bt, best), 3) + f" ({H.esc(best)})" if best else "—",
                     f"%{num(gain, 1)}" if gain is not None else "—", bt.get("common_origins", "—")))  # fmt: skip
    cfg = feat.get("config", {})
    body = H.table(["Değişken kümesi", "Ridge MAE", "En iyi model MAE", "Ridge'in naive'e göre iyileşmesi", "Ortak nokta"],
                   rows, numeric=[1, 2, 3, 4], raw=[2])  # fmt: skip
    body += (f'<p class="muted">Naive MAE: {num(naive, 3)}. Değerlendirme başlangıcı: {H.esc(cfg.get("eval_start") or "belirtilmedi")}. '
             "Değişkenler bu tarihe kadarki veriyle seçildiği için yalnızca sonraki dönemlerdeki hata, seçimin gerçek katkısını "
             "gösterir. Değerlendirme başlangıcı belirtilmediyse sonuç iyimserdir.</p>")  # fmt: skip
    return H.section("Seçimin tahmin performansına katkısı", body, "backtest")


def build(fe: dict, bt_base: dict | None, bt_feat: dict | None, notes: str | None, assumptions: dict | None) -> str:
    selected = fe.get("selected", [])
    screening = fe.get("screening", [])
    by_name = {r["name"]: r for r in screening}
    sections = []
    kpi = [("Üretilen değişken", str(fe["n_features"]), f"{fe['period']['rows']} dönem"),
           ("Sektör tarifi", str(sum(1 for _ in fe.get("recipes", []))), f"{len(fe.get('skipped_recipes', {}))} tarif atlandı")]  # fmt: skip
    if fe.get("label"):
        kind = "Değişim" if "değişim" in fe["label"] else "Düzey"
        kpi += [("Etiket türü", kind, f"{fe['label']} · eğitim sonu {fe.get('train_end') or '—'}"),
                ("Seçilen", str(len(selected)), f"{len(screening)} aday tarandı")]  # fmt: skip
    body = H.kpis(kpi)
    for note in fe.get("notes", []):
        body += f"<p>{H.esc(note)}</p>"
    if notes:
        body += f'<div class="narrative">{H.markdown(notes)}</div>'
    sections.append(H.section("Özet", body, "ozet"))

    fam_rows = [(H.esc(FAMILIES.get(k, k)), v) for k, v in sorted(fe["families"].items(), key=lambda kv: -kv[1])]
    sections.append(H.section("Değişken aileleri", H.table(["Aile", "Sayı"], fam_rows, numeric=[1], raw=[0]), "aileler"))

    if selected:
        rows, labels, values = [], [], []
        for s in selected:
            r = by_name.get(s["name"], {})
            rows.append((H.esc(s["name"]), H.esc(FAMILIES.get(s["family"], s["family"])), H.esc(s["description"]),
                         num(r.get("r")), num(r.get("n_eff"), 0), num(r.get("q"), 3), num(r.get("stability"), 2)))  # fmt: skip
            labels.append(s["name"])
            values.append(abs(r.get("r") or 0))
        body = bar_chart(labels, values, "Seçilen değişkenler: etiketle sıra korelasyonu (mutlak değer)", fmt=lambda v: num(v, 2))
        body += H.table(["Değişken", "Aile", "Açıklama", "Spearman r", "Etkin n", "q (BH)", "Alt dönem istikrarı"],
                        rows, numeric=[3, 4, 5, 6], raw=[0, 1, 2])  # fmt: skip
        sections.append(H.section("Seçilen değişkenler", body, "secilen"))
    elif fe.get("label"):
        sections.append(H.section("Seçilen değişkenler", "<p>Yanlış keşif oranı ve istikrar ölçütlerini sağlayan değişken bulunamadı. "
                                  "Bu, panelin hedefin kendi geçmişinin ötesinde güvenilir bilgi taşımadığını gösterebilir; naive ve "
                                  "çapa modelleri bu durumda doğru kıyas düzeyidir.</p>", "secilen"))  # fmt: skip

    if screening:
        rows = [(H.esc(r["name"]), H.esc(FAMILIES.get(r.get("family"), r.get("family") or "—")), num(r.get("r")), num(r.get("n_eff"), 0),
                 num(r.get("q"), 3), num(r.get("stability"), 2), "✓" if r.get("selected") else "", H.esc(r.get("reason") or "—"))
                for r in screening[:40]]  # fmt: skip
        sections.append(H.section("Tarama (ilk 40 aday)", H.table(
            ["Değişken", "Aile", "r", "Etkin n", "q", "İstikrar", "Seçildi", "Gerekçe"], rows, numeric=[2, 3, 4, 5], raw=[0, 1, 7]), "tarama"))

    recipes = fe.get("recipes", [])
    if recipes or fe.get("skipped_recipes"):
        rows = [(H.esc(r["name"]), H.esc(r.get("op") or "—"), H.esc(", ".join(r["inputs"])), H.esc(r["description"])) for r in recipes]
        rows += [(H.esc(n), "—", "—", H.esc(f"Atlandı: {why}")) for n, why in fe.get("skipped_recipes", {}).items()]
        sections.append(H.section("Sektör tarifleri", H.table(["Tarif", "İşlem", "Girdiler", "Açıklama"], rows, raw=[0, 1, 2, 3]), "tarifler"))

    if bt_base and bt_feat:
        sections.append(backtest_section(bt_base, bt_feat))
    if assumptions:
        sections.append(A.section(assumptions))
    sections.append(H.section("Yöntem", METHOD, "yontem"))
    nav = [("ozet", "Özet"), ("secilen", "Seçilen"), ("tarama", "Tarama"), ("tarifler", "Tarifler"), ("yontem", "Yöntem")]
    return H.page(f"Değişken üretimi ve seçimi — {fe['sector']}", f"{fe['period']['first']} – {fe['period']['last']}",
                  sections, "Veri: TCMB EVDS ve katalogdaki diğer kaynaklar; değişkenler mlbooster features ile üretildi.", nav)  # fmt: skip


METHOD = """
<ul>
<li><strong>Üretim:</strong> gecikmeler (1, 2, 3, 6, 12), hareketli ortalama ve standart sapma (3, 6, 12), 3 ve 12 dönemlik
momentum, ivme, geçmiş 24 döneme göre standart skor, üstel ortalama; sektör tarifleri (reel faiz, faiz farkı, kur sepeti vb.);
takvim (iş günü, bayram, Ramazan ayı ve bayram öncesi günleri); bilinen olay ve sektör düzenlemesi göstergeleri. Bütün
değişkenler nedenseldir: t anındaki değer yalnızca t ve öncesindeki gözlemlerden hesaplanır.</li>
<li><strong>Etiket:</strong> hedef kalıcıysa (1. gecikme otokorelasyonu ≥ 0,9) h dönemlik değişim, değilse h dönem sonraki
düzey. Düzeyler arası korelasyon, ortak trend nedeniyle sahte ilişki üretebilir.</li>
<li><strong>Seçim:</strong> yalnızca etiketi eğitim sonuna kadar gözlenmiş dönemlerde Spearman korelasyonu; otokorelasyona göre
etkin örneklem; Benjamini–Hochberg ile yanlış keşif oranı (q ≤ 0,10); üç alt dönemin en az ikisinde aynı işaret; |r| ≥ 0,95
olan çiftlerde fazlalık eleme; seçilen değişken sayısı etkin örneklem / 15 ile sınırlı (Harrell).</li>
<li><strong>Sınırlar:</strong> tarama ilişkiyi gösterir, nedenselliği göstermez. Seçimin değeri, seçim penceresinden sonraki
dönemlerde yapılan backtest ile ölçülür.</li>
</ul>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--features", required=True, type=Path, help="mlbooster features --json çıktısı")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--bt-base", type=Path, help="Sektör paneliyle backtest JSON")
    ap.add_argument("--bt-features", type=Path, help="Seçilen değişkenlerle backtest JSON")
    ap.add_argument("--notes", type=Path, help="Yorum (Markdown)")
    ap.add_argument("--assumptions", type=Path, help="Girdi/varsayım kaydı (assumptions.json)")
    args = ap.parse_args()
    try:
        assumptions = A.load(args.assumptions) if args.assumptions else None
    except A.AssumptionsError as exc:
        sys.exit(f"Hata: {exc}")
    bt_base = read_json(args.bt_base) if args.bt_base else None
    bt_feat = read_json(args.bt_features) if args.bt_features else None
    notes = args.notes.read_text(encoding="utf-8") if args.notes else None
    html = build(read_json(args.features), bt_base, bt_feat, notes, assumptions)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"Rapor → {args.out}")


if __name__ == "__main__":
    main()
