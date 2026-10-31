#!/usr/bin/env python3
"""`mlbooster backtest --json` çıktısından HTML backtest raporu üretir.

Kullanım:
  python3 render_backtest_report.py --backtest bt.json --out backtest.html [--notes notes.md]
Yalnızca standart kütüphane gerekir.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))

from mlb_report import html as H  # noqa: E402
from mlb_report import assumptions as A  # noqa: E402
from mlb_report import external as X  # noqa: E402
from mlb_report.data import read_json  # noqa: E402
from mlb_report.svg import Band, LineChart, Series, bar_chart, line_chart  # noqa: E402

MODEL_LABELS = {
    "naive": "Naive (son değer)",
    "seasonal_naive": "Mevsimsel naive (12 ay önce)",
    "drift": "Drift (doğrusal eğilim)",
    "mean12": "Son 12 ay ortalaması (Atkeson–Ohanian)",
    "median12": "Son 12 ay medyanı",
    "drift_recent": "Son dönem eğimi (6 ay)",
    "qr": "Kantil regresyon (medyan)",
    "combo_mean": "Kombinasyon: ortalama",
    "combo_median": "Kombinasyon: medyan",
    "auto_robust": "Otomatik dayanıklı (sapmaya göre ortalama/medyan)",
    "ridge": "Ridge (gecikmeli, değişim)",
    "anchor_naive": "Çapa naive (ay sonu değeri)",
}


def build(bt: dict, notes: str | None, assumptions: dict | None = None, external: dict | None = None, latest: dict | None = None) -> str:
    cfg, metrics = bt["config"], bt["metrics"]
    by_model = {m["model"]: m for m in metrics}
    best = bt["best_model"]
    # Çıta: en güçlü naive (çapa varsa çapa naive, yoksa naive).
    ref_name = "anchor_naive" if "anchor_naive" in by_model else "naive"
    naive = by_model.get(ref_name)
    gain = None
    if naive and best and best != ref_name and naive["mae"]:
        gain = (1 - by_model[best]["mae"] / naive["mae"]) * 100
    sections = []
    sections.append(
        H.section(
            "Özet",
            H.kpis(
                [
                    ("Hedef", cfg["target"], f"{cfg['horizon']} ay ileri"),
                    ("En iyi model", MODEL_LABELS.get(best, best or "—"), None),
                    ("MAE", f"{by_model[best]['mae']:,.4g}" if best else "—", f"{bt['common_origins']} ortak nokta"),
                    ("Çıtaya göre", f"%{gain:+.1f}" if gain is not None else "—",
                     f"MAE iyileşmesi ({MODEL_LABELS[ref_name]})" if gain is not None else f"en iyi model: {MODEL_LABELS[ref_name]}"),
                ]
            ),
            "ozet",
        )
    )
    if assumptions:
        sections.insert(1, A.section(assumptions))

    if notes:
        sections.append(H.section("Yorum", f'<div class="narrative">{H.markdown(notes)}</div>', "yorum"))

    def opt(v, fmt):
        return "—" if v is None else fmt.format(v)

    rows = [
        (MODEL_LABELS.get(m["model"], m["model"]), m["n"], f"{m['mae']:,.4g}", f"{m['rmse']:,.4g}",
         opt(m.get("mase"), "{:.2f}"), opt(m.get("coverage80"), "{:.0%}"), opt(m.get("width80"), "{:,.4g}"),
         opt(m.get("pinball"), "{:,.4g}"))
        for m in metrics
    ]  # fmt: skip
    body = H.table(["Model", "n", "MAE", "RMSE", "MASE", "%80 kapsama", "Aralık genişliği", "Pinball"], rows, numeric=[1, 2, 3, 4, 5, 6, 7])
    covered = [m for m in metrics if m.get("coverage80") is not None]
    if covered:
        best_p = min(covered, key=lambda m: m["pinball"] if m.get("pinball") is not None else float("inf"))
        low = [MODEL_LABELS.get(m["model"], m["model"]) for m in covered if m["coverage80"] < 0.7]
        body += (
            f'<p class="muted">Aralıklar: kantil regresyon kendi %10/%90 kantillerini, diğer modeller yalnızca geçmiş hatalarından '
            f"kurulan ampirik %80 aralığı kullanır. Olasılıksal olarak en iyi model (en düşük pinball): "
            f"<strong>{H.esc(MODEL_LABELS.get(best_p['model'], best_p['model']))}</strong>."
            + (f" Kapsaması %70'in altında kalanlar ({H.esc(', '.join(low))}) belirsizliği olduğundan dar gösteriyor; "
               "rejim değişimlerinde aralıkları genişletin." if low else "")
            + "</p>"
        )
    if bt.get("robust_share") is not None:
        body += f'<p class="muted">Otomatik dayanıklı kural, başlangıçların %{bt["robust_share"] * 100:.0f}\'inde geçmiş sapma yüksek olduğu için medyanı seçti.</p>'

    body += bar_chart([MODEL_LABELS.get(m["model"], m["model"]) for m in metrics], [m["mae"] for m in metrics], "MAE (düşük daha iyi)", lambda v: f"{v:,.4g}", MODEL_LABELS.get(best))
    regimes = bt.get("regime_metrics") or []
    if regimes:
        names = list(dict.fromkeys(r["regime"] for r in regimes))
        models = [m["model"] for m in metrics]
        cell = {(r["regime"], r["model"]): r for r in regimes}
        rrows = []
        for m in models:
            vals = [f"{cell[(n, m)]['mae']:,.4g} (n={cell[(n, m)]['n']})" if (n, m) in cell else "—" for n in names]
            rrows.append((MODEL_LABELS.get(m, m), *vals))
        body += "<h3>Rejimlere göre MAE</h3>" + H.table(["Model", *names], rrows, numeric=list(range(1, len(names) + 1)))
        body += '<p class="muted">Sıralama rejimden rejime değişiyorsa tek bir "en iyi model" iddiası zayıftır; raporda bunu belirtin.</p>'
    sections.append(H.section("Model karşılaştırması", body, "metrikler"))

    preds = bt["predictions"]
    if preds:
        dates = sorted({date.fromisoformat(p["date"]) for p in preds})
        idx = {d: i for i, d in enumerate(dates)}
        actual: list[float | None] = [None] * len(dates)
        shown = [best] + [m for m in (ref_name, "naive", "drift") if m != best][:1]
        lines = {m: [None] * len(dates) for m in shown}
        for p in preds:
            i = idx[date.fromisoformat(p["date"])]
            actual[i] = p["y_true"]
            if p["model"] in lines:
                lines[p["model"]][i] = p["y_pred"]
        series = [Series("Gerçekleşen", actual, 1)] + [
            Series(MODEL_LABELS.get(m, m), lines[m], slot) for slot, m in zip((2, 3), shown)
        ]
        bands = []
        if any(p["model"] == "qr" for p in preds):
            lo = [None] * len(dates)
            hi = [None] * len(dates)
            for p in preds:
                if p["model"] == "qr" and p.get("lower") is not None and p.get("upper") is not None:
                    i = idx[date.fromisoformat(p["date"])]
                    lo[i], hi[i] = p["lower"], p["upper"]
            bands.append(Band("Kantil regresyon %10–%90 aralığı", lo, hi, slot=3))
        chart = line_chart(LineChart(dates, series, f"{cfg['target']}: gerçekleşen ve tahmin ({cfg['horizon']} ay ileri)", bands=bands))
        table_rows = [(d.isoformat()[:7], *(f"{s.values[i]:,.4g}" if s.values[i] is not None else "—" for s in series)) for i, d in enumerate(dates)]
        chart += H.data_table("Tablo görünümü", ["Ay", *[s.label for s in series]], table_rows, numeric=list(range(1, len(series) + 1)))
        sections.append(H.section("Tahminler (son dönem)", chart, "tahminler"))

    if external:
        sections.append(X.section(external, latest=latest))
    method = f"""
- **Yöntem:** genişleyen pencereyle rolling-origin backtest; ilk eğitim penceresi {cfg['min_train']} ay, her ay bir adım ileri.
- **Look-ahead yok:** t başlangıcında yalnızca t ve öncesi kullanılır; panel yayın gecikmesine göre hizalanmıştır.
- **Adil karşılaştırma:** metrikler tüm modellerin tahmin ürettiği {bt['common_origins']} ortak noktada hesaplanır.
- **Ridge:** hedefin {cfg['horizon']} adımlı değişimini {'çapadan (' + cfg['anchor'] + ')' if cfg.get('anchor') else 'son değerden'} öğrenir; gecikmeler {cfg['lags']}, alpha {cfg['alpha']}, dış değişkenler {'açık' if cfg['exogenous'] else 'kapalı'}.
- **Çapa:** {('`' + cfg['anchor'] + '` — aylık ortalama hedefte ay sonu değeri en güçlü bilgidir; çıta çapa naive’dir.') if cfg.get('anchor') else 'kullanılmadı.'}
- **MASE < 1** ilk eğitim penceresindeki naive hatasından iyi demektir; TR'de rejim değişimleri yüzünden 1'in üstü sık görülür.
- Bu modeller referans (baseline) içindir; üretim modelinin bunları geçmesi beklenir.
"""
    sections.append(H.section("Metodoloji", f'<div class="narrative">{H.markdown(method)}</div>', "metodoloji"))
    return H.page(
        f"Tahmin backtest'i — {bt['sector']} / {cfg['target']}",
        f"{cfg['horizon']} ay ileri · {len(metrics)} model",
        sections,
        "Kaynak: TCMB EVDS (kaynak gösterilerek). Geçmiş performans gelecekteki sonuçları garanti etmez.",
        nav=[("ozet", "Özet")] + ([("varsayimlar", "Varsayımlar")] if assumptions else [])
        + [("metrikler", "Modeller"), ("tahminler", "Tahminler")] + ([("dis-tahminler", "Dış tahminler")] if external else [])
        + [("metodoloji", "Metodoloji")],
    )


def _parse_latest(items: list[str]) -> dict[str, tuple[str, float]]:
    out = {}
    for item in items:
        try:
            var, rest = item.split("=", 1)
            month, value = rest.split(":", 1)
            out[var.strip()] = (month.strip(), float(value))
        except ValueError:
            raise SystemExit(f"--latest biçimi değişken=YYYY-AA:değer olmalı: {item}") from None
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backtest", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--notes", type=Path, help="Rapora eklenecek yorum (Markdown)")
    ap.add_argument("--assumptions", type=Path, help="Girdi/varsayım kaydı (assumptions.json)")
    ap.add_argument("--external", type=Path, help="Kurumsal tahminler (external_forecasts.json)")
    ap.add_argument("--latest", action="append", default=[], help="Son gerçekleşen: değişken=YYYY-AA:değer (ör. usdtry=2026-10:49.70)")
    args = ap.parse_args()
    assumptions = A.load(args.assumptions) if args.assumptions else None
    external = X.load(args.external) if args.external else None
    if assumptions is None:
        print("Uyarı: --assumptions verilmedi; raporda girdiler ve varsayımlar bölümü olmayacak.", file=sys.stderr)
    notes = args.notes.read_text(encoding="utf-8") if args.notes else None
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build(read_json(args.backtest), notes, assumptions, external, _parse_latest(args.latest)), encoding="utf-8")
    print(f"Rapor → {args.out}")


if __name__ == "__main__":
    main()
