#!/usr/bin/env python3
"""Renders a market share and competition analysis report (HTML) from `mlbooster market-share --json` output.

Usage:
  python3 render_share_report.py --analysis share.json --out report.html \
      [--competitors competitors.json] [--narrative narrative.md] [--assumptions assumptions.json] \
      [--title "Otomotiv pazarı"] [--source "ODMD aylık basın bültenleri"]
Requires only the standard library.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))

from mlb_report import assumptions as A  # noqa: E402
from mlb_report import html as H  # noqa: E402
from mlb_report.data import read_json  # noqa: E402
from mlb_report.svg import LineChart, Series, line_chart  # noqa: E402

DESCRIPTION = "mlbooster market-share --json çıktısından pazar payı ve rekabet analizi raporu (HTML) üretir."
TIERS = {"direct": "Doğrudan rakip", "indirect": "Dolaylı rakip", "potential": "Potansiyel rakip / yeni giren"}
CONFIDENCE = {"high": "Yüksek", "medium": "Orta", "low": "Düşük"}
TREND = {"gaining": ("good", "▲", "Pay kazanıyor"), "losing": ("critical", "▼", "Pay kaybediyor"),
         "flat": ("info", "■", "Yatay"), "insufficient_data": ("info", "·", "Yetersiz gözlem")}  # fmt: skip
HHI_CLASS = {"low": "düşük", "moderate": "orta", "high": "yüksek"}
BASIS = {"ttm": "son 12 ay toplamı (mevsimsellikten arındırılmış)", "period": "dönemlik değer"}
FREQ = {52: "haftalık", 12: "aylık", 4: "çeyreklik", 2: "altı aylık", 1: "yıllık"}
SEVERITY = {"high": "high", "medium": "medium", "info": "info"}


class CompetitorsError(ValueError):
    pass


# ------------------------------------------------------------------------------------------ format


def num(v: float | None, d: int = 1) -> str:
    """Turkish number format: period as thousands separator, comma as decimal mark."""
    return H.num(v, d)


def pct(v: float | None, d: int = 1) -> str:
    return "—" if v is None else f"%{num(v, d)}"


def signed(v: float | None, d: int = 2) -> str:
    return "—" if v is None else f"{'+' if v > 0 else ''}{num(v, d)}"


def pp(v: float | None, d: int = 2) -> str:
    return "—" if v is None else f"{signed(v, d)} puan"


def month(iso: str | None) -> str:
    if not iso:
        return "—"
    y, m = iso[:4], int(iso[5:7])
    names = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
    return f"{names[m - 1]} {y}"


def trend_badge(label: str) -> str:
    cls, icon, text = TREND.get(label, ("info", "·", label))
    return f'<span class="badge {cls}"><span aria-hidden="true">{icon}</span> {H.esc(text)}</span>'


# -------------------------------------------------------------------------------- competitor input


def load_competitors(path: Path) -> dict:
    data = read_json(path)
    validate_competitors(data)
    return data


def validate_competitors(data: dict) -> None:
    items = data.get("competitors") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise CompetitorsError("competitors.json: 'competitors' listesi gereklidir")
    for c in items:
        if not isinstance(c, dict) or not str(c.get("entity", "")).strip():
            raise CompetitorsError(f"Her rakip kaydında 'entity' bulunmalıdır: {c}")
        if c.get("tier") not in TIERS:
            raise CompetitorsError(f"tier şu değerlerden biri olmalıdır: {', '.join(TIERS)} ({c.get('entity')})")
        if c.get("confidence", "medium") not in CONFIDENCE:
            raise CompetitorsError(f"confidence şu değerlerden biri olmalıdır: {', '.join(CONFIDENCE)} ({c.get('entity')})")
        url = c.get("source_url")
        if url and urlsplit(str(url)).scheme not in {"https", "http"}:
            raise CompetitorsError(f"source_url http(s) olmalıdır: {url}")
    if data.get("as_of"):
        try:
            date.fromisoformat(str(data["as_of"]))
        except ValueError:
            raise CompetitorsError("as_of YYYY-AA-GG biçiminde olmalıdır") from None


# -------------------------------------------------------------------------------------------- chart


def change_bars(players: list[dict], title: str, highlight: str | None) -> str:
    """Annual share change (percentage points): horizontal bars in both directions from the zero axis."""
    rows = [p for p in players if p.get("d_year_pp") is not None]
    if not rows:
        return ""
    rows = sorted(rows, key=lambda p: p["d_year_pp"], reverse=True)
    w, row_h, label_w, value_w = 640, 28, 170, 86
    h = row_h * len(rows) + 10
    span = max(abs(p["d_year_pp"]) for p in rows) or 1.0
    plot = w - label_w - 2 * value_w
    zero = label_w + value_w + plot / 2
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{H.esc(title)}" class="chart">']
    out.append(f'<line class="zero" x1="{zero:.1f}" x2="{zero:.1f}" y1="0" y2="{h}"/>')
    for i, p in enumerate(rows):
        y = 5 + i * row_h
        v = p["d_year_pp"]
        bw = max(1.5, abs(v) / span * plot / 2)
        x = zero if v >= 0 else zero - bw
        cls = "bar" + ("" if v >= 0 else " neg")
        lab_cls = "bar-label" + (" focus" if p["entity"] == highlight else "")
        out.append(f'<text class="{lab_cls}" x="{label_w - 8}" y="{y + 16}" text-anchor="end">{H.esc(p["entity"])}</text>')
        out.append(f'<rect class="{cls}" x="{x:.1f}" y="{y + 4}" width="{bw:.1f}" height="{row_h - 10}" rx="3">'
                   f'<title>{H.esc(p["entity"])}: {H.esc(pp(v))}</title></rect>')  # fmt: skip
        tx, anchor = (x + bw + 6, "start") if v >= 0 else (x - 6, "end")
        out.append(f'<text class="bar-value" x="{tx:.1f}" y="{y + 16}" text-anchor="{anchor}">{H.esc(pp(v))}</text>')
    out.append("</svg>")
    return f'<figure class="chart-card"><figcaption>{H.esc(title)}</figcaption>{"".join(out)}</figure>'


def _dates(isos: list[str]) -> list[date]:
    return [date.fromisoformat(d) for d in isos]


# ------------------------------------------------------------------------------------------- report


def build(an: dict, competitors: dict | None, narrative: str | None, assumptions: dict | None, title: str | None, source: str | None) -> str:
    meta, conc, market = an["meta"], an["concentration"], an["market"]
    players = an["players"]
    focus = meta.get("focus")
    by_entity = {p["entity"]: p for p in players}
    fp = by_entity.get(focus) if focus else None
    last = meta["last"]
    freq = FREQ.get(meta["periods_per_year"], "dönemlik")
    unit = meta.get("unit") or ("pay" if meta["measure"] == "share" else "değer")
    latest = conc["latest"]
    growth = next((g for g in reversed(market.get("growth_yoy_pct") or []) if g is not None), None)
    coverage = (market.get("coverage") or [None])[-1]

    sections, nav = [], [("summary", "Özet")]
    kpi = []
    if fp:
        rank_note = f"önceki yıl {fp['rank_year_ago']}." if fp.get("rank_year_ago") else None
        kpi += [(f"{focus} pazar payı", pct(fp["share"]), f"yıllık {pp(fp.get('d_year_pp'))}"),
                ("Sıralama", f"{fp['rank']}.", rank_note)]  # fmt: skip
    hhi_class = HHI_CLASS.get(latest["class"], latest["class"])
    hhi_note = f"{hhi_class} yoğunlaşma" + (f"; üst sınır {num(latest['hhi_upper'], 0)}" if latest.get("hhi_upper") and latest["hhi_upper"] - latest["hhi"] > 1 else "")
    kpi += [("HHI", num(latest["hhi"], 0), hhi_note), ("İlk üç oyuncunun payı (CR3)", pct(latest["cr3"]), None)]
    if growth is not None:
        kpi.append(("Pazar büyümesi, yıllık", pct(growth), f"{unit}, {BASIS.get(meta['basis'], meta['basis'])}"))
    if meta.get("has_total") or meta["measure"] == "share":
        kpi.append(("Coverage", pct(coverage), "listelenen oyuncuların pazardaki toplam payı"))
    summary = H.kpis(kpi)
    summary += (f'<p class="muted">Dönem: {H.esc(month(meta["first"]))} – {H.esc(month(last))} · {H.esc(freq)} veri · '
                f'pay tabanı: {H.esc(BASIS.get(meta["basis"], meta["basis"]))} · {meta["entities"]} oyuncu.</p>')  # fmt: skip
    if narrative:
        summary += f'<div class="narrative">{H.markdown(narrative)}</div>'
    sections.append(H.section("Özet", summary, "summary"))

    # Market structure
    dates = _dates(conc["dates"])
    body = '<div class="grid-2">'
    if meta["measure"] == "value" and any(g is not None for g in market.get("growth_yoy_pct") or []):
        body += line_chart(LineChart(_dates(market["dates"]), [Series("Yıllık büyüme (%)", market["growth_yoy_pct"])],
                                     "Pazar büyüklüğü, yıllık değişim (%)", y_format=lambda v: f"%{num(v, 0)}"))  # fmt: skip
    body += line_chart(LineChart(dates, [Series("HHI", conc["hhi"])],
                                 f"Herfindahl–Hirschman endeksi (eşikler: {num(meta['thresholds']['hhi_low'], 0)} / {num(meta['thresholds']['hhi_high'], 0)})",
                                 y_format=lambda v: num(v, 0)))  # fmt: skip
    body += line_chart(LineChart(dates, [Series("CR3", conc["cr3"], 1), Series("CR5", conc["cr5"], 2)],
                                 "Yoğunlaşma oranları (%)", y_format=lambda v: f"%{num(v, 0)}"))  # fmt: skip
    if any(v is not None for v in conc.get("instability") or []):
        body += line_chart(LineChart(dates, [Series("İstikrarsızlık endeksi", conc["instability"])],
                                     "Pay istikrarsızlığı (Hymer–Pashigian, yüzde puan)", y_format=lambda v: num(v, 1)))  # fmt: skip
    body += "</div>"
    ago = conc.get("year_ago") or {}
    body += (
        f"<p>Son dönemde HHI {num(latest['hhi'], 0)} düzeyindedir ve AB Yatay Birleşme Kılavuzu eşiklerine göre "
        f"<strong>{H.esc(hhi_class)}</strong> yoğunlaşmaya karşılık gelir"
        + (f"; bir yıl önce {num(ago.get('hhi'), 0)} idi" if ago.get("hhi") is not None else "")
        + f". Eşdeğer oyuncu sayısı (10.000 / HHI) {num(latest['equivalent_players'], 1)} olarak hesaplanmıştır.</p>"
    )
    if latest.get("hhi_upper") and latest["hhi_upper"] - latest["hhi"] > 1:
        body += ('<p class="muted">Listelenmeyen oyuncuların payı bilinmediği için HHI bir aralık olarak verilir: alt sınır '
                 "listelenmeyen kısmın çok sayıda küçük oyuncudan, üst sınır tek bir oyuncudan oluştuğunu varsayar.</p>")  # fmt: skip
    sections.append(H.section("Pazar yapısı ve yoğunlaşma", body, "structure"))
    nav.append(("structure", "Pazar yapısı"))

    # Players
    share_dates = _dates(an["shares"]["dates"])
    series_map = an["shares"]["series"]
    cands = [c["entity"] for c in (an.get("competitors") or {}).get("candidates", [])]
    lead = [focus] if focus else []
    lead += [e for e in cands if e in series_map and e not in lead][: 3 - len(lead)]
    lead += [p["entity"] for p in players if p["entity"] in series_map and p["entity"] not in lead][: 3 - len(lead)]
    chart = line_chart(LineChart(share_dates, [Series(e, series_map[e], i + 1) for i, e in enumerate(lead[:3])],
                                 "Pazar payı (%)" + (f": {focus} ve en yakın rakipler" if focus else ": ilk üç oyuncu"),
                                 y_format=lambda v: f"%{num(v, 0)}"))  # fmt: skip
    rows = []
    for p in players:
        rows.append((
            p["rank"], H.esc(p["entity"]) + (" <strong>(odak)</strong>" if p["entity"] == focus else ""), pct(p["share"], 2),
            signed(p.get("d_year_pp")), signed(p.get("d_prev_pp")),
            f"{p['rank_year_ago']}." if p.get("rank_year_ago") else "—",
            signed(p.get("trend_pp_per_year")), trend_badge(p["trend"]), num(p.get("relative_share"), 2),
            signed(p.get("growth_vs_market_pp")) if "growth_vs_market_pp" in p else "—",
        ))  # fmt: skip
    body = '<div class="grid-2">' + chart + change_bars(players, "Yıllık pay değişimi (yüzde puan)", focus) + "</div>"
    body += H.table(
        ["Sıra", "Oyuncu", "Pay", "Yıllık değişim (puan)", "Önceki döneme göre (puan)", "Bir yıl önceki sıra",
         "Eğim (puan/yıl)", "Eğilim", "Göreli pay", "Pazara göre büyüme farkı (puan)"],
        rows, numeric=[0, 2, 3, 4, 5, 6, 8, 9], raw=[1, 7],
    )  # fmt: skip
    body += ('<p class="muted">Eğilim, son dönemlerin dönemlik paylarına doğrusal regresyonla tahmin edilir; |t| ≥ 2 ve '
             "yıllık eğim en az 0,2 puan olduğunda kazanma ya da kaybetme olarak sınıflandırılır. Göreli pay, oyuncunun payının "
             "en büyük rakibinin payına oranıdır (liderde 1'in üstü).</p>")  # fmt: skip
    sections.append(H.section("Oyuncular ve pay dinamikleri", body, "players"))
    nav.append(("players", "Oyuncular"))

    # Competitor set
    comp = an.get("competitors")
    if comp or competitors:
        sections.append(H.section("Rakip seti", competitor_section(comp, competitors, by_entity), "competitors"))
        nav.append(("competitors", "Rakipler"))

    # Segments
    seg = an.get("segments")
    if seg:
        sections.append(H.section("Segment kırılımı", segment_section(seg, focus), "segments"))
        nav.append(("segments", "Segmentler"))

    # Macro drivers
    drivers = an.get("drivers")
    if drivers:
        rows = [(H.esc(d["feature"]), d["lag"], num(d["r"], 2), d["n"]) for d in drivers[:10]]
        body = H.table(["Feature", "Lag (dönem)", "Korelasyon (r)", "Gözlem"], rows, numeric=[1, 2, 3])
        body += ('<p class="muted">Pazarın yıllık büyümesi ile sektör panelindeki makro değişkenler arasındaki en güçlü gecikmeli '
                 "korelasyonlar. Korelasyon nedensellik göstermez; nominal büyüme enflasyonla birlikte hareket ettiğinden fiyat "
                 "kaynaklı feature'larla yüksek korelasyon beklenir. Sonuçlar, model kurulmadan önce aday açıklayıcı feature "
                 "olarak değerlendirilmelidir.</p>")  # fmt: skip
        skipped = meta.get("panel_skipped") or {}
        if skipped:
            body += f'<p class="muted">Panele alınamayan feature\'lar: {H.esc(", ".join(sorted(skipped)))}.</p>'
        sections.append(H.section("Pazar büyümesinin makro sürücüleri", body, "drivers"))
        nav.append(("drivers", "Makro sürücüler"))

    # Data
    issue_rows = [(H.badge(SEVERITY.get(i["severity"], "info")), H.esc(i["message"])) for i in an.get("issues", [])]
    body = (H.table(["Önem", "Bulgu"], issue_rows, raw=[0, 1]) if issue_rows
            else "<p>Girdi tablosunda coverage, takvim ya da biçim sorunu tespit edilmedi.</p>")  # fmt: skip
    if source:
        body += f"<p>Kaynak: {H.esc(source)}.</p>"
    sections.append(H.section("Coverage ve veri kalitesi", body, "data"))
    nav.append(("data", "Veri"))

    if assumptions:
        sections.append(A.section(assumptions))
        nav.append(("assumptions", "Varsayımlar"))
    sections.append(H.section("Yöntem", METHOD, "method"))
    nav.append(("method", "Yöntem"))

    heading = title or (f"{focus}: pazar payı ve rekabet analizi" if focus else "Pazar payı ve rekabet analizi")
    subtitle = f"{month(meta['first'])} – {month(last)} · {freq} · {meta['entities']} oyuncu"
    attribution = f"Veri: {source}." if source else "Veri: kullanıcının sağladığı pazar payı tablosu."
    return H.page(heading, subtitle, sections, attribution, nav)


def competitor_section(comp: dict | None, documented: dict | None, by_entity: dict) -> str:
    cands = {c["entity"]: c for c in (comp or {}).get("candidates", [])}
    subst = {s["entity"]: s for s in (comp or {}).get("substitution", [])}
    docs = {c["entity"]: c for c in (documented or {}).get("competitors", [])}
    order = list(dict.fromkeys([*docs, *cands]))
    rows = []
    for e in order:
        p, s, d = by_entity.get(e, {}), subst.get(e, {}), docs.get(e)
        r = f"{num(s['r'], 2)} (n={s['n']})" if s.get("r") is not None else "—"
        evidence = "; ".join(cands[e]["reasons"]) if e in cands else "—"
        qual = "—"
        if d:
            parts = [TIERS[d["tier"]]]
            if d.get("basis"):
                parts.append(", ".join(map(str, d["basis"])))
            if d.get("evidence"):
                parts.append(str(d["evidence"]))
            qual = H.esc(" · ".join(parts))
            if d.get("source_url"):
                qual += f' <a href="{H.esc(d["source_url"])}" rel="noopener noreferrer">kaynak</a>'
        rows.append((H.esc(e), pct(p.get("share"), 2), signed(p.get("d_year_pp")), trend_badge(p["trend"]) if p.get("trend") else "—",
                     r, H.esc(evidence), qual, H.esc(CONFIDENCE.get((d or {}).get("confidence", ""), "—"))))  # fmt: skip
    body = ""
    focus = (comp or {}).get("focus") or (documented or {}).get("focus")
    if focus:
        kinds = "veriye dayalı kanıt ile nitel değerlendirme birleştirilerek" if documented else "yalnızca pay verisinden elde edilen kanıtla"
        body += f"<p>Rakip seti <strong>{H.esc(focus)}</strong> için {kinds} oluşturulmuştur.</p>"
        if not documented:
            body += ('<p class="muted">Nitel değerlendirme (ürün, fiyat bandı, kanal, coğrafya) eklenmemiştir; '
                     "rakip seti bu nedenle ön değerlendirme niteliğindedir.</p>")
    body += H.table(["Oyuncu", "Pay", "Yıllık değişim (puan)", "Eğilim", "İkame korelasyonu", "Veriye dayalı kanıt",
                     "Nitel değerlendirme", "Güven"], rows, numeric=[1, 2, 4], raw=[0, 3, 5, 6, 7])  # fmt: skip
    body += ('<p class="muted"><strong>Veriye dayalı kanıt</strong> pay tablosundan otomatik çıkarılır: pay sıralamasında '
             "komşuluk, pazar liderliği, dönemlik pay değişimlerinin ters yönlü korelasyonu (ikame) ve segment karmasının "
             "örtüşmesi. İkame korelasyonu, odak oyuncunun pay kazandığı dönemlerde ilgili oyuncunun pay kaybedip "
             "kaybetmediğini ölçer; anlamlılık için en az 12 gözlem ve |r| ≥ 2/√n aranır. <strong>Nitel değerlendirme</strong> "
             "ürün, fiyat bandı, kanal ve coğrafya örtüşmesine dayanır ve kaynağıyla birlikte verilir.</p>")  # fmt: skip
    if documented and documented.get("as_of"):
        body += f'<p class="muted">Nitel değerlendirme tarihi: {H.esc(documented["as_of"])}.</p>'
    if documented and documented.get("notes"):
        body += f"<p>{H.esc(documented['notes'])}</p>"
    return body


def segment_section(seg: dict, focus: str | None) -> str:
    rows = []
    for s in seg["segments"]:
        rows.append((H.esc(s["segment"]), pct(s["weight"]), signed(s.get("weight_d_pp")), num(s["hhi"], 0),
                     H.esc(s.get("leader") or "—"), pct(s.get("leader_share")),
                     pct(s.get("focus_share")) if focus else "—", signed(s.get("focus_share_d_pp")) if focus else "—"))  # fmt: skip
    head = ["Segment", "Pazardaki ağırlık", "Ağırlık değişimi (puan)", "HHI", "Lider", "Lider payı"]
    head += [f"{focus} payı", f"{focus} yıllık değişim (puan)"] if focus else ["Odak payı", "Odak değişimi (puan)"]
    body = H.table(head, rows, numeric=[1, 2, 3, 5, 6, 7], raw=[0, 4])
    ss = seg.get("shift_share")
    if ss and focus:
        body += (
            f"<h3>{H.esc(focus)} pay değişiminin kaynağı ({H.esc(month(ss['from']))} → {H.esc(month(ss['to']))})</h3>"
            f"<p>Toplam pay değişimi <strong>{pp(ss['total_pp'])}</strong>: segment içi pay etkisi {pp(ss['share_effect_pp'])}, "
            f"segment karması etkisi {pp(ss['mix_effect_pp'])}. Pay etkisi oyuncunun segmentlerde rakiplerine karşı "
            "konumundaki değişimi, karma etkisi ise oyuncunun güçlü olduğu segmentlerin büyüyüp küçülmesini gösterir.</p>"
        )
        body += H.table(["Segment", "Pay etkisi (puan)", "Karma etkisi (puan)"],
                        [(H.esc(r["segment"]), signed(r["share_effect_pp"]), signed(r["mix_effect_pp"])) for r in ss["by_segment"]],
                        numeric=[1, 2], raw=[0])  # fmt: skip
    return body


METHOD = """
<ul>
<li><strong>Pay:</strong> oyuncu değeri ÷ pazar toplamı. Pazar toplamı verilmediyse listelenen oyuncuların toplamı kullanılır ve
paylar yalnızca bu küme içinde yorumlanmalıdır. Aylık akım verisinde (satış, prim) pay, mevsimselliği gidermek için son 12 ayın
toplamıyla hesaplanır.</li>
<li><strong>HHI:</strong> yüzde payların karelerinin toplamı (0–10.000). AB Yatay Birleşme Kılavuzu (2004/C 31/03): 1.000'in altı
düşük, 1.000–2.000 orta, 2.000'in üstü yüksek yoğunlaşma. <strong>CR-k:</strong> en büyük k oyuncunun pay toplamı.</li>
<li><strong>İstikrarsızlık endeksi (Hymer–Pashigian):</strong> bir yıl içindeki mutlak pay değişimlerinin toplamının yarısı; pazarda el
değiştiren pay miktarını gösterir.</li>
<li><strong>Shift-share ayrıştırması:</strong> ΔS = Σ w̄·Δs + Σ s̄·Δw (orta nokta ağırlıkları). Birinci terim segment içi pay
değişimini, ikinci terim segment ağırlıklarındaki değişimi ölçer; toplam, odak oyuncunun pay değişimine tam eşittir.</li>
<li><strong>Sınırlar:</strong> Paylar kaynak kurumun coverage'ına bağlıdır (ör. birliğe üye olmayan şirketler). Korelasyon ve ikame
ölçütleri nedensellik göstermez. Tutar verisi nominal ise büyüme oranları enflasyon içerir.</li>
</ul>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=DESCRIPTION)
    ap.add_argument("--analysis", required=True, type=Path, help="mlbooster market-share --json çıktısı")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--competitors", type=Path, help="Nitel rakip değerlendirmesi (competitors.json)")
    ap.add_argument("--narrative", type=Path, help="Yönetici özeti (Markdown)")
    ap.add_argument("--assumptions", type=Path, help="Girdi/varsayım kaydı (assumptions.json)")
    ap.add_argument("--title", help="Rapor başlığı")
    ap.add_argument("--source", help="Veri kaynağı atfı (ör. 'ODMD aylık basın bültenleri')")
    args = ap.parse_args()
    try:
        competitors = load_competitors(args.competitors) if args.competitors else None
        assumptions = A.load(args.assumptions) if args.assumptions else None
    except (CompetitorsError, A.AssumptionsError) as exc:
        sys.exit(f"Hata: {exc}")
    narrative = args.narrative.read_text(encoding="utf-8") if args.narrative else None
    html = build(read_json(args.analysis), competitors, narrative, assumptions, args.title, args.source)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"Rapor → {args.out}")


if __name__ == "__main__":
    main()
