#!/usr/bin/env python3
"""Renders an HTML backtest report from `mlbooster backtest --json` output.

Usage:
  python3 render_backtest_report.py --backtest bt.json --out backtest.html [--notes notes.md]
Requires only the standard library.
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

DESCRIPTION = "mlbooster backtest --json çıktısından HTML backtest raporu üretir."
MODEL_LABELS = {
    "naive": "Naive (son değer)",
    "seasonal_naive": "Seasonal naive (12 ay önceki değer)",
    "drift": "Drift (doğrusal trend)",
    "mean12": "Son 12 ay ortalaması (Atkeson–Ohanian)",
    "median12": "Son 12 ay medyanı",
    "drift_recent": "Son dönem drift'i (6 ay)",
    "qr": "Quantile regression (medyan)",
    "combo_mean": "Kombinasyon: ortalama",
    "combo_median": "Kombinasyon: medyan",
    "auto_robust": "Otomatik robust kombinasyon (sapmaya göre ortalama/medyan)",
    "ridge": "Ridge (lag'li, değişim)",
    "anchor_naive": "Anchor naive (ay sonu değeri)",
}


def build(bt: dict, notes: str | None, assumptions: dict | None = None, external: dict | None = None, latest: dict | None = None) -> str:
    cfg, metrics = bt["config"], bt["metrics"]
    by_model = {m["model"]: m for m in metrics}
    best = bt["best_model"]
    # Benchmark: the strongest naive (anchor naive when an anchor is given, otherwise naive).
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
                    ("MAE", H.sig(by_model[best]["mae"], 4) if best else "—", f"{bt['common_origins']} ortak origin"),
                    ("Benchmark'a göre", f"%{H.num(gain, 1, sign=True)}" if gain is not None else "—",
                     f"MAE iyileşmesi ({MODEL_LABELS[ref_name]})" if gain is not None else f"en iyi model: {MODEL_LABELS[ref_name]}"),
                ]
            ),
            "summary",
        )
    )
    if assumptions:
        sections.insert(1, A.section(assumptions))

    if notes:
        sections.append(H.section("Yorum", f'<div class="narrative">{H.markdown(notes)}</div>', "commentary"))

    rows = [
        (MODEL_LABELS.get(m["model"], m["model"]), m["n"], H.sig(m["mae"], 4), H.sig(m["rmse"], 4),
         H.num(m.get("mase"), 2), "—" if m.get("coverage80") is None else f"%{H.num(m['coverage80'] * 100, 0)}",
         H.sig(m.get("width80"), 4), H.sig(m.get("pinball"), 4))
        for m in metrics
    ]  # fmt: skip
    body = H.table(["Model", "n", "MAE", "RMSE", "MASE", "%80 coverage", "Aralık genişliği", "Pinball loss"], rows, numeric=[1, 2, 3, 4, 5, 6, 7])
    covered = [m for m in metrics if m.get("coverage80") is not None]
    if covered:
        best_p = min(covered, key=lambda m: m["pinball"] if m.get("pinball") is not None else float("inf"))
        low = [MODEL_LABELS.get(m["model"], m["model"]) for m in covered if m["coverage80"] < 0.7]
        body += (
            f'<p class="muted">Aralıklar: quantile regression kendi %10/%90 quantile\'larını, diğer modeller yalnızca geçmiş hatalarından '
            f"kurulan ampirik %80 aralığı kullanır. Olasılıksal olarak en iyi model (en düşük pinball loss): "
            f"<strong>{H.esc(MODEL_LABELS.get(best_p['model'], best_p['model']))}</strong>."
            + (f" Coverage'ı %70'in altında kalan modeller ({H.esc(', '.join(low))}) belirsizliği olduğundan dar gösteriyor; "
               "rejim değişimlerinde aralıkların genişletilmesi önerilir." if low else "")
            + "</p>"
        )
    if bt.get("robust_share") is not None:
        body += f'<p class="muted">Otomatik robust kural, origin\'lerin %{H.num(bt["robust_share"] * 100, 0)}\'inde geçmiş sapma yüksek olduğu için medyanı seçti.</p>'

    body += bar_chart([MODEL_LABELS.get(m["model"], m["model"]) for m in metrics], [m["mae"] for m in metrics], "MAE (düşük olan daha iyi)", lambda v: H.sig(v, 4), MODEL_LABELS.get(best))
    regimes = bt.get("regime_metrics") or []
    if regimes:
        names = list(dict.fromkeys(r["regime"] for r in regimes))
        models = [m["model"] for m in metrics]
        cell = {(r["regime"], r["model"]): r for r in regimes}
        rrows = []
        for m in models:
            vals = [f"{H.sig(cell[(n, m)]['mae'], 4)} (n={cell[(n, m)]['n']})" if (n, m) in cell else "—" for n in names]
            rrows.append((MODEL_LABELS.get(m, m), *vals))
        body += "<h3>Rejimlere göre MAE</h3>" + H.table(["Model", *names], rrows, numeric=list(range(1, len(names) + 1)))
        body += '<p class="muted">Sıralama rejimden rejime değişiyorsa tek bir "en iyi model" iddiası zayıftır; bu durum raporda belirtilmelidir.</p>'
    sections.append(H.section("Model karşılaştırması", body, "metrics"))

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
            bands.append(Band("Quantile regression %10–%90 aralığı", lo, hi, slot=3))
        chart = line_chart(LineChart(dates, series, f"{cfg['target']}: gerçekleşen ve forecast ({cfg['horizon']} ay ileri)", bands=bands))
        table_rows = [(d.isoformat()[:7], *(H.sig(s.values[i], 4) for s in series)) for i, d in enumerate(dates)]
        chart += H.data_table("Tablo görünümü", ["Ay", *[s.label for s in series]], table_rows, numeric=list(range(1, len(series) + 1)))
        sections.append(H.section("Forecast'lar (son dönem)", chart, "forecasts"))

    if external:
        sections.append(X.section(external, latest=latest))
    method = f"""
- **Yöntem:** genişleyen pencereyle (expanding window) rolling-origin backtest; ilk eğitim penceresi {cfg['min_train']} ay, her ay bir adım ileri.
- **Look-ahead yok:** t origin'inde yalnızca t ve öncesi kullanılır; panel publication lag'e göre hizalanmıştır.
- **Adil karşılaştırma:** metrikler, tüm modellerin forecast ürettiği {bt['common_origins']} ortak origin'de hesaplanır.
- **Ridge:** hedefin {cfg['horizon']} adımlı değişimini {'anchor\'dan (' + cfg['anchor'] + ')' if cfg.get('anchor') else 'son değerden'} öğrenir; lag'ler {cfg['lags']}, alpha {cfg['alpha']}, dışsal feature'lar {'açık' if cfg['exogenous'] else 'kapalı'}.
- **Anchor:** {('`' + cfg['anchor'] + '` — aylık ortalama hedefte ay sonu değeri en güncel bilgiyi taşır; benchmark anchor naive’dir.') if cfg.get('anchor') else 'kullanılmadı.'}
- **MASE < 1**, ilk eğitim penceresindeki naive hatasından daha düşük hata anlamına gelir; Türkiye verisinde rejim değişimleri nedeniyle 1'in üzerindeki değerler sık görülür.
- Bu modeller baseline'dır; üretimdeki modelin bunları geçmesi beklenir.
"""
    sections.append(H.section("Metodoloji", f'<div class="narrative">{H.markdown(method)}</div>', "methodology"))
    return H.page(
        f"Tahmin backtest'i — {bt['sector']} / {cfg['target']}",
        f"{cfg['horizon']} ay ileri · {len(metrics)} model",
        sections,
        "Kaynak: TCMB EVDS (kaynak gösterilerek). Geçmiş performans gelecekteki sonuçları garanti etmez.",
        nav=[("summary", "Özet")] + ([("assumptions", "Varsayımlar")] if assumptions else [])
        + [("metrics", "Modeller"), ("forecasts", "Forecast'lar")] + ([("external-forecasts", "Dış tahminler")] if external else [])
        + [("methodology", "Metodoloji")],
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
    ap = argparse.ArgumentParser(description=DESCRIPTION)
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
        print("Uyarı: --assumptions verilmedi; raporda girdiler ve varsayımlar bölümü yer almayacak.", file=sys.stderr)
    notes = args.notes.read_text(encoding="utf-8") if args.notes else None
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build(read_json(args.backtest), notes, assumptions, external, _parse_latest(args.latest)), encoding="utf-8")
    print(f"Rapor → {args.out}")


if __name__ == "__main__":
    main()
