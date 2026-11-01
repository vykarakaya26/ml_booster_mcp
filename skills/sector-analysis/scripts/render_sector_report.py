#!/usr/bin/env python3
"""Sector analysis report: source map + panel + quality summary + (optional) backtests + commentary.

Usage:
  python3 render_sector_report.py --map map.json --panel panel.csv --audit audit.json \
      --narrative narrative.md [--backtest bt1.json --backtest bt2.json] --out report.html
The inputs are the outputs of `mlbooster map show --format json`, `build-panel --out x.csv`, `audit --json` and
`backtest --json`. Requires only the standard library.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))

from mlb_report import assumptions as A
from mlb_report import external as X
from mlb_report import html as H
from mlb_report.data import read_json, read_panel_csv
from mlb_report.svg import LineChart, Series, line_chart

ASSETS = Path(__file__).resolve().parents[1] / "assets"
DESCRIPTION = "Sektör analizi raporu: kaynak haritası, panel, kalite özeti, (opsiyonel) backtest'ler ve yorum."
STATUS = {"ready": "Panelde", "pending_discovery": "Seri kodu bekleniyor", "no_provider": "Provider yok"}
TRANSFORM = {"level": "seviye", "log_diff": "aylık log değişim", "yoy_pct": "yıllık % değişim", "diff": "fark"}


def _last(values: list, dates: list[date]) -> tuple[date | None, float | None, float | None]:
    """Latest valid value and the value 12 months earlier."""
    for i in range(len(values) - 1, -1, -1):
        if values[i] is not None:
            prev = values[i - 12] if i >= 12 else None
            return dates[i], values[i], prev
    return None, None, None


def _fmt(v: float | None, transform: str | None = None) -> str:
    if v is None:
        return "—"
    if transform == "log_diff":  # ≈ monthly % change
        return f"%{H.num(v * 100, 2)}"
    a = abs(v)
    return H.num(v, 1 if a >= 100 else 2 if a >= 1 else 4)


def build(m: dict, dates: list[date], columns: dict, audit: dict, backtests: list[dict], narrative: str, assumptions: dict | None = None, external: dict | None = None, latest: dict | None = None) -> str:
    sector = m["sector"]
    features = {f["name"]: f for f in m["macro_features"]}
    sections = [H.section("Yönetici özeti", f'<div class="narrative">{H.markdown(narrative)}</div>', "summary")]
    if assumptions:
        sections.append(A.section(assumptions))

    tiles = []
    for name, values in list(columns.items())[:8]:
        when, last, prev = _last(values, dates)
        tr = features.get(name, {}).get("transform")
        note = f"{when:%Y-%m}" + (f" · 12 ay önce {_fmt(prev, tr)}" if prev is not None else "") if when else None
        tiles.append((f"{name} ({TRANSFORM.get(tr, '')})", _fmt(last, tr), note))
    charts = "".join(line_chart(LineChart(dates, [Series(n, v)], f"{n} — {TRANSFORM.get(features.get(n, {}).get('transform'), '')}")) for n, v in columns.items())
    sections.append(
        H.section(
            "Güncel görünüm",
            H.kpis(tiles) + '<h3>Feature grafikleri</h3><p class="muted">Her grafik tek eksenlidir; her feature kendi ölçeğinde gösterilir. Değerler, imleç grafiğin üzerine getirildiğinde görüntülenir.</p>'
            + f'<div class="grid-2">{charts}</div>',
            "outlook",
        )
    )

    src_rows = [
        (s["name"], ", ".join(s["roles"]), s["access"], ", ".join(s["frequency"]), s["publication_lag_days"],
         s["verification_status"], "✓" if s["provider_ready"] else "—")
        for s in m["sources"]
    ]  # fmt: skip
    feat_rows = [
        (f["name"], f["source"], f["series"] or "—", TRANSFORM.get(f["transform"], f["transform"]), STATUS.get(f["status"], f["status"]), "" if f["series"] else (f["discover_hint"] or ""))
        for f in m["macro_features"]
    ]
    share_rows = [(s["source"], s["metric"], "evet" if s["is_proxy"] else "hayır") for s in m["market_share"]]
    body = (
        H.table(["Kaynak", "Rol", "Erişim", "Frekans", "Publication lag (gün)", "Doğrulama", "Provider"], src_rows, numeric=[4])
        + "<h3>Makro feature'lar</h3>" + H.table(["Feature", "Kaynak", "Seri", "Dönüşüm", "Durum", "Not"], feat_rows)
        + ("<h3>Pazar payı kaynakları</h3>" + H.table(["Kaynak", "Metrik", "Proxy"], share_rows) if share_rows else "")
    )
    sections.append(H.section("Veri kaynağı haritası", body, "source-map"))

    c = audit["counts"]
    top = [(H.badge(i["severity"]), i["feature"], i["message"]) for i in audit["issues"] if i["severity"] != "info"][:8]
    body = H.kpis([("Yüksek", str(c["high"]), None), ("Orta", str(c["medium"]), None), ("Bilgi", str(c["info"]), "bilinen şoklar")])
    body += H.table(["Önem", "Feature", "Bulgu"], top, raw=[0]) if top else "<p>Yüksek veya orta önemde bulgu yok.</p>"
    sections.append(H.section("Veri kalitesi", body, "quality"))

    if backtests:
        rows = []
        for bt in backtests:
            by = {x["model"]: x for x in bt["metrics"]}
            best, naive = bt["best_model"], by.get("naive")
            gain = (1 - by[best]["mae"] / naive["mae"]) * 100 if naive and best != "naive" and naive["mae"] else None
            rows.append((bt["config"]["target"], bt["config"]["horizon"], best, _fmt(by[best]["mae"]), "—" if gain is None else f"%{H.num(gain, 1, sign=True)}", bt["common_origins"]))
        body = H.table(["Hedef", "Ufuk (ay)", "En iyi model", "MAE", "Naive'e göre", "n"], rows, numeric=[1, 3, 4, 5])
        for bt in backtests:
            reg = [r for r in bt.get("regime_metrics") or [] if r["model"] == bt["best_model"]]
            if reg:
                body += f"<h3>{H.esc(bt['config']['target'])}: en iyi modelin rejimlere göre MAE'si</h3>" + H.table(
                    ["Rejim", "n", "MAE"], [(r["regime"], r["n"], _fmt(r["mae"])) for r in reg], numeric=[1, 2]
                )
        body += '<p class="muted">Baseline modeller: naive, seasonal naive, drift, ridge (değişim). Üretimdeki modelin bunları geçmesi beklenir.</p>'
        sections.append(H.section("Forecast baseline'ları", body, "forecast"))

    if external:
        sections.append(X.section(external, latest=latest))
    method = (ASSETS / "methodology.md").read_text(encoding="utf-8")
    sections.append(H.section("Metodoloji ve sınırlar", f'<div class="narrative">{H.markdown(method)}</div>', "methodology"))
    licenses = [(s["name"], s["license"] or "—") for s in m["sources"]]
    sections.append(H.section("Kaynaklar ve kullanım koşulları", H.table(["Kaynak", "Koşul"], licenses), "sources"))

    nav = [("summary", "Özet")] + ([("assumptions", "Varsayımlar")] if assumptions else [])
    nav += [("outlook", "Görünüm"), ("source-map", "Kaynak haritası"), ("quality", "Kalite")]
    nav += [("forecast", "Forecast")] if backtests else []
    nav += [("external-forecasts", "Dış tahminler")] if external else []
    nav += [("methodology", "Metodoloji")]
    period = f"{dates[0]:%Y-%m} → {dates[-1]:%Y-%m}" if dates else ""
    return H.page(
        f"{sector['name']} sektörü — veri ve feature analizi",
        f"{period} · {len(columns)} feature · ML kullanımları: {', '.join(sector['ml_use_cases'])}",
        sections,
        "Kaynak: TCMB EVDS ve katalogdaki kurumlar (kaynak gösterilerek). Veriler kullanıcılara ek ücret olarak yansıtılamaz (EVDS koşulları).",
        nav,
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
    ap.add_argument("--map", required=True, type=Path)
    ap.add_argument("--panel", required=True, type=Path)
    ap.add_argument("--audit", required=True, type=Path)
    ap.add_argument("--narrative", required=True, type=Path, help="Yönetici özeti (Markdown)")
    ap.add_argument("--backtest", type=Path, action="append", default=[])
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--assumptions", type=Path, help="Girdi/varsayım kaydı (assumptions.json)")
    ap.add_argument("--external", type=Path, help="Kurumsal tahminler (external_forecasts.json)")
    ap.add_argument("--latest", action="append", default=[], help="Son gerçekleşen: değişken=YYYY-AA:değer (ör. usdtry=2026-10:49.70)")
    args = ap.parse_args()
    assumptions = A.load(args.assumptions) if args.assumptions else None
    external = X.load(args.external) if args.external else None
    if assumptions is None:
        print("Uyarı: --assumptions verilmedi; raporda girdiler ve varsayımlar bölümü yer almayacak.", file=sys.stderr)
    dates, columns = read_panel_csv(args.panel)
    html = build(
        read_json(args.map), dates, columns, read_json(args.audit),
        [read_json(p) for p in args.backtest], args.narrative.read_text(encoding="utf-8"), assumptions, external, _parse_latest(args.latest),
    )  # fmt: skip
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"Rapor → {args.out}")


if __name__ == "__main__":
    main()
