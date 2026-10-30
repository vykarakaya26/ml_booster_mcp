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
from mlb_report.data import read_json  # noqa: E402
from mlb_report.svg import LineChart, Series, bar_chart, line_chart  # noqa: E402

MODEL_LABELS = {
    "naive": "Naive (son değer)",
    "seasonal_naive": "Mevsimsel naive (12 ay önce)",
    "drift": "Drift (doğrusal eğilim)",
    "ridge": "Ridge (gecikmeli, değişim)",
    "anchor_naive": "Çapa naive (ay sonu değeri)",
}


def build(bt: dict, notes: str | None) -> str:
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
    if notes:
        sections.append(H.section("Yorum", f'<div class="narrative">{H.markdown(notes)}</div>', "yorum"))

    rows = [
        (MODEL_LABELS.get(m["model"], m["model"]), m["n"], f"{m['mae']:,.4g}", f"{m['rmse']:,.4g}",
         "—" if m["smape"] is None else f"{m['smape']:.1f}", "—" if m["mase"] is None else f"{m['mase']:.2f}")
        for m in metrics
    ]  # fmt: skip
    body = H.table(["Model", "n", "MAE", "RMSE", "sMAPE %", "MASE"], rows, numeric=[1, 2, 3, 4, 5])
    body += bar_chart([MODEL_LABELS.get(m["model"], m["model"]) for m in metrics], [m["mae"] for m in metrics], "MAE (düşük daha iyi)", lambda v: f"{v:,.4g}", MODEL_LABELS.get(best))
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
        chart = line_chart(LineChart(dates, series, f"{cfg['target']}: gerçekleşen ve tahmin ({cfg['horizon']} ay ileri)"))
        table_rows = [(d.isoformat()[:7], *(f"{s.values[i]:,.4g}" if s.values[i] is not None else "—" for s in series)) for i, d in enumerate(dates)]
        chart += H.data_table("Tablo görünümü", ["Ay", *[s.label for s in series]], table_rows, numeric=list(range(1, len(series) + 1)))
        sections.append(H.section("Tahminler (son dönem)", chart, "tahminler"))

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
        nav=[("ozet", "Özet"), ("metrikler", "Modeller"), ("tahminler", "Tahminler"), ("metodoloji", "Metodoloji")],
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backtest", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--notes", type=Path, help="Rapora eklenecek yorum (Markdown)")
    args = ap.parse_args()
    notes = args.notes.read_text(encoding="utf-8") if args.notes else None
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build(read_json(args.backtest), notes), encoding="utf-8")
    print(f"Rapor → {args.out}")


if __name__ == "__main__":
    main()
