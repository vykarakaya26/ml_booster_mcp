#!/usr/bin/env python3
"""`mlbooster audit --json` çıktısı + panel CSV'sinden HTML kalite raporu üretir.

Kullanım:
  python3 render_audit_report.py --audit audit.json --panel panel.csv --sector otomotiv --notes notes.md --out audit.html
Yalnızca standart kütüphane gerekir.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))

from mlb_report import html as H  # noqa: E402
from mlb_report.data import read_json, read_panel_csv  # noqa: E402
from mlb_report.svg import Flag, LineChart, Marker, Series, line_chart  # noqa: E402


def build(audit: dict, dates: list[date], columns: dict, sector: str, notes: str | None = None) -> str:
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
                    ("Yüksek önem", str(counts["high"]), "önce bunlar"),
                    ("Orta önem", str(counts["medium"]), None),
                    ("Bilgi", str(counts["info"]), "bilinen şoklarla eşleşen"),
                ]
            ),
            "ozet",
        )
    )

    if notes:
        sections.append(H.section("Yorum ve öneriler", f'<div class="narrative">{H.markdown(notes)}</div>', "oneriler"))

    rows = [(H.badge(i["severity"]), i["feature"], i["check"], i["message"]) for i in audit["issues"]]
    body = H.table(["Önem", "Feature", "Kontrol", "Bulgu"], rows, raw=[0]) if rows else '<p>Bulgu yok.</p>'
    sections.append(H.section("Bulgular", body, "bulgular"))

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
                    flags.append(Flag(d, v, f"Aykırı: {d_iso[:7]} = {v:,.4g}"))
        charts.append(line_chart(LineChart(dates, [Series(name, values)], name, markers, flags)))
    legend = (
        '<p class="muted">Dikey çizgi: tespit edilen ortalama kayması · '
        '<span class="badge serious"><span aria-hidden="true">▲</span> nokta</span>: robust z ile aykırı değer. '
        "Grafiklerin üzerine gelince değerler görünür.</p>"
    )
    sections.append(H.section("Seriler", legend + f'<div class="grid-2">{"".join(charts)}</div>', "seriler"))

    srows = [
        (
            s["feature"], s["first"] or "—", s["last"] or "—", s["observations"],
            f"%{s['missing_ratio'] * 100:.1f}", "—" if s["months_since_last"] is None else s["months_since_last"],
            len(s["outliers"]), len(s["breaks"]),
        )
        for s in audit["series"]
    ]  # fmt: skip
    sections.append(
        H.section(
            "Seri özeti",
            H.table(["Feature", "İlk", "Son", "Gözlem", "Eksik", "Son gözlemden beri (ay)", "Aykırı", "Kırılma"], srows, numeric=[3, 4, 5, 6, 7]),
            "seri-ozeti",
        )
    )

    extra = []
    if audit["correlated_pairs"]:
        extra.append("<h3>Aşırı korelasyon</h3>" + H.table(["A", "B", "Korelasyon"], [(p["a"], p["b"], f"{p['corr']:+.3f}") for p in audit["correlated_pairs"]], numeric=[2]))
    if audit["skipped_features"]:
        extra.append("<h3>Panele girmeyen feature'lar</h3>" + H.table(["Feature", "Neden"], list(audit["skipped_features"].items())))
    if extra:
        sections.append(H.section("Ek notlar", "".join(extra), "ek"))

    return H.page(
        f"Veri kalitesi denetimi — {sector}",
        f"Denetim tarihi {audit['as_of']} · nokta-zamanlı aylık panel",
        sections,
        "Kaynak: TCMB EVDS ve katalogdaki diğer kurumlar. Kırılma eşleştirmesi bilinen TR şok tarihlerine göre yapılır; nedensellik iddiası değildir.",
        nav=[("ozet", "Özet")] + ([("oneriler", "Öneriler")] if notes else []) + [("bulgular", "Bulgular"), ("seriler", "Seriler"), ("seri-ozeti", "Seri özeti")],
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--audit", required=True, type=Path)
    ap.add_argument("--panel", required=True, type=Path)
    ap.add_argument("--sector", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--notes", type=Path, help="Yorum ve öneriler (Markdown)")
    args = ap.parse_args()
    dates, columns = read_panel_csv(args.panel)
    notes = args.notes.read_text(encoding="utf-8") if args.notes else None
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build(read_json(args.audit), dates, columns, args.sector, notes), encoding="utf-8")
    print(f"Rapor → {args.out}")


if __name__ == "__main__":
    main()
