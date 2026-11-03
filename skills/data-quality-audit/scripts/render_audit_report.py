#!/usr/bin/env python3
"""Renders an HTML quality report from `mlbooster audit --json` output and the panel CSV.

Usage:
  python3 render_audit_report.py --audit audit.json --panel panel.csv --sector otomotiv --notes notes.md --out audit.html
Requires only the standard library.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))

from mlb_report import assumptions as A
from mlb_report import html as H
from mlb_report.data import read_json, read_panel_csv
from mlb_report.svg import Flag, LineChart, Marker, Series, line_chart

DESCRIPTION = "mlbooster audit --json çıktısı ve panel CSV'sinden HTML veri kalitesi raporu üretir."
STATIONARITY = {"stationary": "stationary", "unit_root": "unit root", "break_suspected": "structural break şüphesi", "inconclusive": "belirsiz"}


def build(audit: dict, dates: list[date], columns: dict, sector: str, notes: str | None = None, assumptions: dict | None = None) -> str:
    counts = audit["counts"]
    series = {s["feature"]: s for s in audit["series"]}
    sections = []

    sections.append(
        H.section(
            "Özet",
            H.kpis(
                [
                    ("Dönem", f"{audit['months']} ay", f"{dates[0]:%Y-%m} → {dates[-1]:%Y-%m}" if dates else None),
                    ("Feature", str(len(series)), f"{len(audit['skipped_features'])} atlandı" if audit["skipped_features"] else None),
                    ("Yüksek önem", str(counts["high"]), "öncelikli"),
                    ("Orta önem", str(counts["medium"]), None),
                    ("Bilgi", str(counts["info"]), "bilinen şoklarla eşleşen"),
                ]
            ),
            "summary",
        )
    )
    if assumptions:
        sections.insert(1, A.section(assumptions))


    if notes:
        sections.append(H.section("Yorum ve öneriler", f'<div class="narrative">{H.markdown(notes)}</div>', "recommendations"))

    rows = [(H.badge(i["severity"]), i["feature"], i["check"], i["message"]) for i in audit["issues"]]
    body = H.table(["Önem", "Feature", "Kontrol", "Bulgu"], rows, raw=[0]) if rows else '<p>Bulgu yok.</p>'
    sections.append(H.section("Bulgular", body, "findings"))

    charts = []
    for name, values in columns.items():
        info = series.get(name, {})
        markers = [Marker(date.fromisoformat(b["date"]), b["date"][:7]) for b in info.get("breaks", [])]
        flags = []
        for d_iso in info.get("outliers", [])[:30]:
            d = date.fromisoformat(d_iso)
            if d in dates:
                v = values[dates.index(d)]
                if v is not None:
                    flags.append(Flag(d, v, f"Outlier: {d_iso[:7]} = {H.sig(v, 4)}"))
        charts.append(line_chart(LineChart(dates, [Series(name, values)], name, markers, flags)))
    legend = (
        '<p class="muted">Dikey çizgi: tespit edilen ortalama kayması (structural break) · '
        '<span class="badge serious"><span aria-hidden="true">▲</span> nokta</span>: robust z-score ile tespit edilen outlier. '
        "Değerler, imleç grafiğin üzerine getirildiğinde görüntülenir.</p>"
    )
    sections.append(H.section("Seriler", legend + f'<div class="grid-2">{"".join(charts)}</div>', "series"))

    diag = audit.get("diagnostics") or []
    if diag:
        drows = [
            (d["feature"], d["n"], STATIONARITY.get(d["stationarity"], d["stationarity"]), "—" if d["adf_t"] is None else H.num(d["adf_t"], 2),
             "—" if d["kpss_stat"] is None else H.num(d["kpss_stat"], 3), H.num(d["skew"], 2), H.num(d["excess_kurtosis"], 2),
             "—" if d["volatility_ratio"] is None else H.num(d["volatility_ratio"], 2), "—" if d["acf12"] is None else H.num(d["acf12"], 2))
            for d in diag
        ]  # fmt: skip
        recs = [(d["feature"], r) for d in diag for r in d["recommendations"]]
        body = (
            '<p class="muted">ADF (H0: unit root, %5 kritik değer −2,86) ve KPSS (H0: stationary, %5 kritik değer 0,463) birlikte okunur. '
            "Volatilite oranı: son 36 aydaki değişimlerin std'si / tüm dönemdeki std. ACF12: lag 12'deki otokorelasyon (mevsimsellik).</p>"
            + H.table(["Feature", "n", "Stationarity", "ADF t", "KPSS", "Skewness", "Excess kurtosis", "Volatilite oranı", "ACF12"], drows, numeric=[1, 3, 4, 5, 6, 7, 8])
            + ("<h3>Ön işleme önerileri</h3>" + H.table(["Feature", "Öneri"], recs) if recs else "")
        )
        sections.append(H.section("Dağılım ve stationarity", body, "distribution"))

    srows = [
        (
            s["feature"], s["first"] or "—", s["last"] or "—", s["observations"],
            f"%{H.num(s['missing_ratio'] * 100, 1)}", "—" if s["months_since_last"] is None else s["months_since_last"],
            len(s["outliers"]), len(s["breaks"]),
        )
        for s in audit["series"]
    ]  # fmt: skip
    sections.append(
        H.section(
            "Seri özeti",
            H.table(["Feature", "İlk", "Son", "Gözlem", "Eksik", "Son gözlemden beri (ay)", "Outlier", "Structural break"], srows, numeric=[3, 4, 5, 6, 7]),
            "series-summary",
        )
    )

    extra = []
    if audit.get("notable_pairs"):
        extra.append("<h3>Dikkate değer korelasyonlar</h3>" + H.table(["A", "B", "Korelasyon", "Not"], [(p["a"], p["b"], H.num(p["corr"], 3, sign=True), p["note"]) for p in audit["notable_pairs"]], numeric=[2]))
    if audit["correlated_pairs"]:
        extra.append("<h3>Aşırı korelasyon</h3>" + H.table(["A", "B", "Korelasyon"], [(p["a"], p["b"], H.num(p["corr"], 3, sign=True)) for p in audit["correlated_pairs"]], numeric=[2]))
    if audit["skipped_features"]:
        extra.append("<h3>Panele girmeyen feature'lar</h3>" + H.table(["Feature", "Neden"], list(audit["skipped_features"].items())))
    if extra:
        sections.append(H.section("Ek notlar", "".join(extra), "notes"))

    return H.page(
        f"Veri kalitesi denetimi — {sector}",
        f"Denetim tarihi {audit['as_of']} · point-in-time aylık panel",
        sections,
        "Kaynak: TCMB EVDS ve katalogdaki diğer kurumlar. Structural break'ler Türkiye'deki bilinen şok tarihleriyle eşleştirilir; bu eşleştirme nedensellik iddiası taşımaz.",
        nav=[("summary", "Özet")] + ([("assumptions", "Varsayımlar")] if assumptions else []) + ([("recommendations", "Öneriler")] if notes else [])
        + [("findings", "Bulgular"), ("distribution", "Dağılım"), ("series", "Seriler"), ("series-summary", "Seri özeti")],
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=DESCRIPTION)
    ap.add_argument("--audit", required=True, type=Path)
    ap.add_argument("--panel", required=True, type=Path)
    ap.add_argument("--sector", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--notes", type=Path, help="Yorum ve öneriler (Markdown)")
    ap.add_argument("--assumptions", type=Path, help="Girdi/varsayım kaydı (assumptions.json)")
    args = ap.parse_args()
    assumptions = A.load(args.assumptions) if args.assumptions else None
    if assumptions is None:
        print("Uyarı: --assumptions verilmedi; raporda girdiler ve varsayımlar bölümü yer almayacak.", file=sys.stderr)
    dates, columns = read_panel_csv(args.panel)
    notes = args.notes.read_text(encoding="utf-8") if args.notes else None
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build(read_json(args.audit), dates, columns, args.sector, notes, assumptions), encoding="utf-8")
    print(f"Rapor → {args.out}")


if __name__ == "__main__":
    main()
