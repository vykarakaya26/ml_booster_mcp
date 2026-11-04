#!/usr/bin/env python3
"""Renders a brand report (HTML) from `mlbooster brand --json` output: brand summary first, then sector, competitors,
share history, concentration, features, sources.

Usage:
  python3 render_brand_report.py --analysis brand.json --out report.html \
      [--competitors competitors.json] [--narrative narrative.md] [--assumptions assumptions.json] [--title "Togg"]
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
from mlb_report.data import read_json
from mlb_report.share import (
    CONFIDENCE,
    HHI_CLASS,
    TIERS,
    CompetitorsError,
    load_competitors,
    month,
    num,
    pct,
    pp,
    safe_link,
    segment_section,
    signed,
    trend_badge,
)
from mlb_report.svg import LineChart, Series, line_chart

DESCRIPTION = "mlbooster brand --json çıktısından marka raporu (HTML) üretir."
BASIS = {"ttm": "son 12 ay toplamı", "period": "dönemlik değer"}
MAX_REASONS = 4
FAMILIES = {"brand": "marka", "base": "panel değişkeni", "lag": "lag", "rolling_mean": "rolling mean", "rolling_std": "rolling std",
            "momentum": "momentum", "acceleration": "ivme", "zscore": "z-score", "ewm": "EWM", "sector_recipe": "sektör tarifi",
            "calendar": "takvim", "event": "olay"}  # fmt: skip


def _chart(history: dict, focus: str) -> str:
    dates = [date.fromisoformat(d) for d in history["dates"]]
    series = history["series"]
    names = [focus, *[n for n in series if n != focus and n not in {"Diğer oyuncular", "Listelenmeyen"}]][:3]
    return line_chart(LineChart(dates, [Series(n, series[n], i + 1) for i, n in enumerate(names) if n in series],
                                f"Pazar payı (%): {focus} ve en büyük oyuncular", y_format=lambda v: f"%{num(v, 0)}"))  # fmt: skip


def summary_section(r: dict, narrative: str | None) -> str:
    p, conc = r["position"], r["concentration"]
    gap = "Lider" if p["rank"] == 1 else f"{num(p['gap_to_leader_pp'], 1)} puan"
    gap_note = f"en yakın rakip: {p['next_below'] or '—'}" if p["rank"] == 1 else f"lider: {p['leader']} ({pct(p['leader_share'])})"
    body = f"<p><strong>{H.esc(r['headline'])}</strong></p>"
    if r["is_proxy"]:
        body += '<p class="muted">Sonuçlar proxy veriye dayanır; paylar yaklaşık olarak yorumlanmalıdır.</p>'
    body += H.kpis([
        ("Pay", pct(p["share"]), f"{BASIS.get(p['basis'], p['basis'])} · {month(p['last'])}"),
        ("Sıra", f"{p['rank']} / {p['players']}", f"bir yıl önce {p['rank_year_ago']}. sıra" if p.get("rank_year_ago") else "aktif oyuncular arasında"),
        ("Bir yıllık değişim", pp(p.get("d_year_pp")), None),
        ("Lidere uzaklık", gap, gap_note),
        ("HHI", num(conc["hhi"], 0), f"yoğunlaşma {HHI_CLASS.get(conc['class'], conc['class'])}"),
    ])  # fmt: skip
    body += f"<p>Trend: {trend_badge(p['trend'])}</p>"
    if narrative:
        body += H.markdown(narrative)
    if r.get("others"):
        names = ", ".join(f"{m['name']} ({m['id']})" for m in r["others"])
        body += f'<p class="muted">Adı benzeyen diğer oyuncular: {H.esc(names)}.</p>'
    return H.section("Marka özeti", body, "summary")


def group_section(g: dict) -> str:
    """A group share is a computed sum: its members, their dates and the documents stay next to it."""
    rows = []
    for m in g["members"]:
        name = m["name"] if m["included"] else f"{m['name']} (bu dönemde üye değil)"
        since = f"{m['from']} tarihinden itibaren" if m["from"] else "başlangıcı doğrulanamadı"
        basis = safe_link(m["url"], "belge") + H.esc(f" · {m['checked']}" + (f" · {m['note']}" if m["note"] else ""))
        rows.append((H.esc(name), pct(m["share"], 2), "—" if m["rank"] is None else m["rank"], signed(m["d_year_pp"]), H.esc(since), basis))
    body = f"<p><strong>Grup payı {pct(g['share'], 2)}: {H.esc(g['label'])}.</strong> Kaynak bu grubu ayrı bir oyuncu olarak yayımlamaz.</p>"
    body += H.table(["Marka", "Pay", "Sıra", "Yıllık değişim (puan)", "Üyelik", "Dayanak"], rows, numeric=[1, 2, 3], raw=range(6))
    body += "<ul>" + "".join(f"<li>{H.esc(n)}</li>" for n in g["notes"]) + "</ul>"
    return H.section(f"{g['name']}: grup bileşimi", body, "group")


def sector_section(r: dict) -> str:
    names = r["series_names"]
    rows = [(H.esc(s["name"]), pct(s["share"], 2), f"{s['rank']} / {s['players']}", signed(s.get("d_year_pp")), trend_badge(s["trend"]),
             H.esc(f"{s['leader']} ({pct(s['leader_share'])})" if s["rank"] > 1 else "—"), month(s["last"]))
            for s in r["series"]]  # fmt: skip
    body = f"<p><strong>{H.esc(r['sector']['name'])}.</strong> Kapsam: {H.esc(r['sector']['coverage'])}.</p>"
    if r["sector"].get("notes"):
        body += f'<p class="muted">{H.esc(r["sector"]["notes"])}</p>'
    body += H.table(["Seri", "Pay", "Sıra", "Yıllık değişim (puan)", "Trend", "Lider", "Son dönem"], rows, numeric=[1, 2, 3], raw=[0, 4, 5])
    skipped = [f"{names.get(c, c)}: {why}" for c, why in r["skipped_series"].items()]
    if skipped:
        body += f'<p class="muted">Rapora girmeyen seriler. {H.esc("; ".join(skipped))}.</p>'
    return H.section("Sektör ve konum", body, "sector")


def competitor_section(r: dict, documented: dict | None) -> str:
    names = {**r["series_names"], "segment": "Segment karması"}
    docs = {c["entity"]: c for c in (documented or {}).get("competitors", [])}
    rows = []
    for c in r["competitors"]:
        grouped: dict[str, list[str]] = {}
        for x in c["reasons"]:
            grouped.setdefault(x["reason"], []).append(x["series"])
        lines = [f"{reason}: {len(codes)} seride" if len(codes) > 1 else f"{reason}: {names.get(codes[0], codes[0])}"
                 for reason, codes in grouped.items()]  # fmt: skip
        evidence = "; ".join(lines[:MAX_REASONS]) + (f"; {len(lines) - MAX_REASONS} gerekçe daha" if len(lines) > MAX_REASONS else "")
        d = docs.get(c["entity"])
        qual = "—"
        if d:
            qual = H.esc(" · ".join(str(v) for v in (TIERS[d["tier"]], d.get("evidence")) if v))
            if d.get("source_url"):
                qual += f' <a href="{H.esc(d["source_url"])}" rel="noopener noreferrer">kaynak</a>'
        rows.append((H.esc(c["entity"]), pct(c["share"]), num(c["r"], 2), len(c["series"]), H.esc(evidence), qual,
                     H.esc(CONFIDENCE.get((d or {}).get("confidence", ""), "—"))))  # fmt: skip
    body = H.table(["Oyuncu", "Pay", "Substitution correlation", "Seri sayısı", "Veriye dayalı kanıt", "Nitel değerlendirme", "Güven"],
                   rows, numeric=[1, 2, 3], raw=[0, 4, 5, 6])  # fmt: skip
    if not documented:
        body += '<p class="muted">Nitel değerlendirme eklenmemiştir; rakip seti yalnızca pay verisine dayanan bir ön değerlendirmedir.</p>'

    def movers(title: str, items: list[dict]) -> str:
        cells = [(H.esc(m["entity"]), pct(m["share"]), signed(m["d_year_pp"])) for m in items]
        return f"<div><h3>{H.esc(title)}</h3>" + (H.table(["Oyuncu", "Pay", "Yıllık değişim (puan)"], cells, numeric=[1, 2], raw=[0]) if cells else "<p>Yok.</p>") + "</div>"

    body += '<div class="grid-2">' + movers("Yakın sıralarda pay kazananlar", r["movers"]["gainers"]) + movers("Yakın sıralarda pay kaybedenler", r["movers"]["losers"]) + "</div>"
    return H.section("Rakipler", body, "competitors")


def history_section(r: dict) -> str:
    p = r["position"]
    body = _chart(r["share_history"], p["focus"])
    body += f'<p class="muted">{H.esc(p["name"])} · pay tabanı: {H.esc(BASIS.get(p["basis"], p["basis"]))}.</p>'
    return H.section("Pay geçmişi", body, "history")


def concentration_section(r: dict) -> str:
    rows = []
    for s in r["series"]:
        c = s["concentration"]
        rows.append((H.esc(s["name"]), num(c["hhi"], 0), H.esc(HHI_CLASS.get(c["class"], c["class"])), signed(c["d_hhi_year"], 0), pct(c["cr3"]), pct(c["cr5"]), num(c["equivalent_players"], 1)))
    body = H.table(["Seri", "HHI", "Yoğunlaşma", "HHI yıllık değişim", "CR3", "CR5", "Eşdeğer oyuncu"], rows, numeric=[1, 3, 4, 5, 6], raw=[0, 2])
    body += '<p class="muted">Eşikler AB Yatay Birleşme Kılavuzu\'na göredir: 1.000\'in altı düşük, 1.000–2.000 orta, 2.000\'in üstü yüksek.</p>'
    return H.section("Yoğunlaşma", body, "concentration")


def features_section(f: dict) -> str:
    body = f"<p>{H.esc(month(f['first']))} – {H.esc(month(f['last']))} · {f['months']} ay · {f['columns']} kolon. Panel, ML'e hazır biçimde yerel veri dizinine yazıldı.</p>"
    rows = [(H.esc(c["name"]), H.esc(c["description"])) for c in f["brand_columns"]]
    body += H.table(["Kolon", "Tanım"], rows, raw=[0, 1])
    families = ", ".join(f"{FAMILIES.get(k, k)}: {v}" for k, v in sorted(f["families"].items()))
    body += f'<p class="muted">Kolon aileleri. {H.esc(families)}.</p>'
    for note in f["notes"]:
        body += f'<p class="muted">{H.esc(note)}</p>'
    if f["withheld"]:
        body += f'<p class="muted">Kullanım koşulları saklamaya izin vermediği için {len(f["withheld"])} kolon dosyaya yazılmadı.</p>'
    if f["skipped"]:
        body += f'<p class="muted">Panele alınamayan değişkenler: {H.esc(", ".join(sorted(f["skipped"])))}.</p>'
    return H.section("Feature'lar", body, "features")


def sources_section(r: dict) -> str:
    rows = [(H.esc(i.get("message")), H.esc(", ".join(i.get("series", [])))) for i in r["issues"] if i["severity"] != "info"]
    body = ""
    for s in r["sources"]:
        body += f"<p><strong>{H.esc(s['attribution'])}.</strong> {H.esc(s['license'])}</p>"
    if rows:
        body += "<h3>Veri notları</h3>" + H.table(["Bulgu", "Seri"], rows, raw=[0, 1])
    return H.section("Kaynaklar ve lisans", body, "sources")


def _evidence_rows(items: list[dict]) -> str:
    rows = []
    for e in items:
        scope = " · ".join(x for x in (e["basis_label"], e["segment"], None if e["geography"] == "Türkiye" else e["geography"]) if x)
        tier = f"{e['tier_label']} · ikinci el" if e["second_hand"] else e["tier_label"]
        publisher = safe_link(e["url"], e["publisher"])
        if e["original_source"]:
            publisher += H.esc(f" (özgün kaynak: {e['original_source']}, {e['original_date'] or 'tarih yok'})")
        rows.append((H.esc(e["claim_text"]), H.esc(e["period"]), H.esc(scope), H.esc(tier), publisher, H.esc(e["quote"] or "—"),
                     H.esc(e["published"]), H.esc(CONFIDENCE.get(e["confidence"], e["confidence"]))))  # fmt: skip
    return H.table(["Bulgu", "Dönem", "Ölçü ve kapsam", "Kaynak türü", "Yayıncı", "Alıntı", "Yayım", "Güven"], rows, raw=range(8))


def evidence_section(ev: dict) -> str:
    """Findings copied from web pages. They are shown beside the computed shares, never merged with them."""
    body = f"<p><strong>Bu bölüm {H.esc(ev['label'])}.</strong> Rakamlar web sayfalarından aktarılmıştır; hesaplanmış paylarla birleştirilmez, feature'lara girmez.</p>"
    if ev["current"]:
        body += _evidence_rows(ev["current"])
    else:
        body += "<p>Güncel bulgu yok.</p>"
    if ev["single_source"]:
        body += '<p class="muted">Bulgular tek bir bağımsız kaynağa dayanıyor.</p>'
    if ev["contradictions"]:
        body += "<h3>Çelişen bulgular</h3><ul>" + "".join(f"<li>{H.esc(c['message'])}</li>" for c in ev["contradictions"]) + "</ul>"
    if ev["comparison"]:
        rows = [(H.esc(f"{c['computed_text']} · {c['computed_period']}"), H.esc(c["series_name"]),
                 H.esc(f"{c['evidence_text']} · {c['evidence_period']} · {c['evidence_basis']}"), safe_link(c["url"], c["publisher"]),
                 pp(c["difference"], 1) if c["claim"] == "share" else f"{signed(c['difference'], 0)} sıra", H.esc(" ".join(c["reasons"])))
                for c in ev["comparison"]]  # fmt: skip
        body += "<h3>Hesaplanan pay ile karşılaştırma</h3>"
        body += H.table(["Hesaplanan", "Seri", "Bulgu", "Yayıncı", "Fark", "Olası neden"], rows, numeric=[4], raw=range(6))
    if ev["historical"]:
        body += f"<h3>Eski bulgular</h3><p class=\"muted\">Dönemi {num(ev['max_age_days'], 0)} günden eski; güncel sayılmaz.</p>" + _evidence_rows(ev["historical"])
    if ev["next_step"]:
        body += f'<p class="muted">{H.esc(ev["next_step"])}</p>'
    return H.section("Yayımlanmış pay bulguları", body, "evidence")


def search_section(s: dict) -> str:
    """Google Trends block: search interest, apart from computed shares and published findings."""
    body = f"<p><strong>{H.esc(s['note'])}</strong></p>"
    if s["status"] != "done":
        return H.section("Arama ilgisi", body + f"<p>{H.esc(s['message'])}</p>", "search")
    c = s["coverage"]
    body += f"<p>Google Trends Türkiye Top 25 ve Rising listeleri, {H.esc(c['first_day'])} – {H.esc(c['last_day'])} ({num(c['list_days'], 0)} günlük liste). {H.esc(c['note'])}</p>"
    rows = []
    for t in s["terms"]:
        interest = t.get("interest") or {}
        terms = ", ".join((t["terms"] + t["related_terms"])[:3]) or "—"
        rows.append((H.esc(t["mode_label"]), H.esc(t["label"]), H.esc(t["status_label"]), num(t["list_days"], 0),
                     "—" if t["best_rank"] is None else num(t["best_rank"], 0), H.esc(terms),
                     "—" if interest.get("momentum") is None else f"{num(interest['momentum'], 2)} kat"))  # fmt: skip
    body += H.table(["Tür", "Terim", "Durum", "Liste günü", "En iyi sıra", "Listedeki terimler", "Kendi geçmişine göre"], rows, numeric=[3, 4, 6], raw=range(7))
    body += "<p class=\"muted\">Son sütun: son dört hafta ÷ önceki bir yıl; yalnızca terimin kendi geçmişiyle karşılaştırılır. Listede olmamak terimin aranmadığı anlamına gelmez.</p>"
    ranked = [x for x in s["ranking"] if x["rank"] is not None]
    if ranked:
        body += "<h3>Rakipler arasında listelerde görünme</h3><ol>" + "".join(f"<li>{H.esc(x['label'])}: {num(x['list_days'], 0)} gün, en iyi sıra {num(x['best_rank'], 0)}</li>" for x in ranked) + "</ol>"
    if s["provinces"]:
        p = s["provinces"]
        body += f"<h3>İllere yayılım</h3><p>{num(p['provinces_with_data'], 0)} ilde veri var. {H.esc(p['note'])}</p>"
    body += f"<h3>Arama payı neden yok?</h3><p>{H.esc(s['withheld']['arama_payi'])}</p>"
    body += f"<p class=\"muted\">{H.esc(s['attribution'])}. BigQuery: {num(s['cost']['gib'], 2)} GiB, {num(s['cost']['queries'], 0)} sorgu.</p>"
    return H.section("Arama ilgisi", body, "search")


METHOD = """
<ul>
<li><strong>Marka eşleştirme:</strong> ad, oyuncu düzeyinde veri veren kaynaklardaki adlarla karşılaştırılır. Yalnızca katalogdaki bir
takma ad ya da tam ad eşleşmesi markayı doğrudan çözer; kısmi ve yakın eşleşmeler aday olarak sunulur.</li>
<li><strong>Pay:</strong> oyuncu değeri ÷ pazar toplamı. Aylık akım verisinde pay son 12 ayın toplamıyla hesaplanır; stok ve birikimli
seriler dönemliktir. Yüzde olarak yayımlanan seriler olduğu gibi kullanılır.</li>
<li><strong>Sıra:</strong> son dönemde hacmi olan oyuncular arasındadır; kaynağın "Diğer" satırı oyuncu sayılmaz.</li>
<li><strong>Rakip seti:</strong> her seride pay sıralamasında komşuluk, pazar liderliği, anlamlı ters yönlü pay değişimi ve segment
karması örtüşmesiyle çıkarılır; korelasyon nedensellik göstermez.</li>
<li><strong>Feature'lar:</strong> her gözlem yayımlandığı aya yazılır (point-in-time). Rakip payı her dönemin kendi sıralamasından hesaplanır;
sonraki revizyonlar geçmiş satırlarda görünür.</li>
<li><strong>Yayımlanmış pay bulguları:</strong> kaynak politikasına uyan sayfalardan elle doğrulanarak derlenir. Hesaplanmış pay değildir; rakamlar
ortalanmaz, çelişkiler olduğu gibi gösterilir ve hiçbir hesaba ya da feature'a girmez.</li>
<li><strong>Arama ilgisi:</strong> Google Trends'in Türkiye listelerine giren terimlerden gelir ve pazar payı değildir. Skorlar her terim ve il için ayrı
ölçeklendiğinden arama payı hesaplanmaz; liste günü, liste sırası ve terimin kendi geçmişiyle karşılaştırması verilir. Eksik terim doldurulmaz.</li>
</ul>
"""


def limits_page(r: dict) -> str:
    body = f"<p><strong>{H.esc(r['message'])}</strong></p>"
    if r["sector_candidates"]:
        body += "<ul>" + "".join(f"<li>{H.esc(s['name'])}: {H.esc(s['reason'])}.</li>" for s in r["sector_candidates"]) + "</ul>"
    ev = r.get("evidence")
    found = bool(ev and (ev["current"] or ev["historical"]))
    sections = [evidence_section(ev)] if found else []  # what was published comes before what could not be computed
    sections.append(H.section("Sonuç", body, "result"))
    if r.get("search_interest"):
        sections.append(search_section(r["search_interest"]))
    items = "".join(f"<li>{H.esc(a['message'])}" + (f" Seri kodu: <code>{H.esc(a['series'])}</code>." if a.get("series") else "") + "</li>" for a in r["available"])
    sections.append(H.section("Bunun yerine kullanılabilecekler", f"<ul>{items}</ul>", "available"))
    subtitle = "Oyuncu düzeyinde veri yok; pay hesaplanmadı, yayımlanmış bulgular derlendi" if found else "Oyuncu düzeyinde veri yok; pazar payı hesaplanmadı"
    return H.page(f"{r['query']}: marka raporu", subtitle, sections, "ML Booster kaynak kataloğu.")


def build(r: dict, documented: dict | None, narrative: str | None, assumptions: dict | None, title: str | None) -> str:
    if r["status"] == "ambiguous":
        ids = ", ".join(f"{m['name']} ({m['id']})" for m in r["matches"])
        sys.exit(f"Hata: ad birden fazla adayla eşleşiyor; --choice ile seçin: {ids}")
    if r["status"] == "not_found":
        return limits_page(r)
    if r["status"] != "resolved":
        sys.exit(f"Hata: {r.get('message', 'rapor üretilecek veri yok')}")
    sections = [summary_section(r, narrative), sector_section(r), competitor_section(r, documented), history_section(r), concentration_section(r)]
    nav = [("summary", "Özet"), ("sector", "Sektör"), ("competitors", "Rakipler"), ("history", "Pay geçmişi"), ("concentration", "Yoğunlaşma")]
    if r.get("group"):
        sections.insert(1, group_section(r["group"]))
        nav.insert(1, ("group", "Grup"))
    if r.get("segments"):
        sections.append(H.section("Segmentler", segment_section(r["segments"], r["position"]["focus"], r["series_names"]), "segments"))
        nav.append(("segments", "Segmentler"))
    if r.get("features"):
        sections.append(features_section(r["features"]))
        nav.append(("features", "Feature'lar"))
    if r.get("evidence"):
        sections.append(evidence_section(r["evidence"]))
        nav.append(("evidence", "Bulgular"))
    if r.get("search_interest"):
        sections.append(search_section(r["search_interest"]))
        nav.append(("search", "Arama ilgisi"))
    sections.append(sources_section(r))
    nav.append(("sources", "Kaynaklar"))
    if assumptions:
        sections.append(A.section(assumptions))
        nav.append(("assumptions", "Varsayımlar"))
    sections.append(H.section("Yöntem", METHOD, "method"))
    nav.append(("method", "Yöntem"))
    heading = title or f"{r['brand']['name']}: pazar payı ve rakip raporu"
    subtitle = f"{r['sector']['name']} · {month(r['period']['first'])} – {month(r['period']['last'])}"
    return H.page(heading, subtitle, sections, "Veri: " + "; ".join(s["attribution"] for s in r["sources"]) + ".", nav)


def main() -> None:
    ap = argparse.ArgumentParser(description=DESCRIPTION)
    ap.add_argument("--analysis", required=True, type=Path, help="mlbooster brand --json çıktısı")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--competitors", type=Path, help="Nitel rakip değerlendirmesi (competitors.json)")
    ap.add_argument("--narrative", type=Path, help="Yönetici özeti (Markdown)")
    ap.add_argument("--assumptions", type=Path, help="Girdi/varsayım kaydı (assumptions.json)")
    ap.add_argument("--title", help="Rapor başlığı")
    args = ap.parse_args()
    try:
        competitors = load_competitors(args.competitors) if args.competitors else None
        assumptions = A.load(args.assumptions) if args.assumptions else None
    except (CompetitorsError, A.AssumptionsError) as exc:
        sys.exit(f"Hata: {exc}")
    narrative = args.narrative.read_text(encoding="utf-8") if args.narrative else None
    html = build(read_json(args.analysis), competitors, narrative, assumptions, args.title)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"Rapor → {args.out}")


if __name__ == "__main__":
    main()
