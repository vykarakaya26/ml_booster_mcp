"""Kendi kendine yeten HTML rapor sayfası (CSS/JS satır içi, dış kaynak yok)."""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from datetime import datetime

from mlb_report import ASSETS

SEVERITY = {
    "high": ("critical", "●", "Yüksek"),
    "medium": ("serious", "▲", "Orta"),
    "info": ("info", "ℹ", "Bilgi"),
}


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def badge(severity: str) -> str:
    """Durum rengi asla tek başına anlam taşımaz: ikon + etiket + renk."""
    cls, icon, label = SEVERITY.get(severity, ("info", "•", severity))
    return f'<span class="badge {cls}"><span aria-hidden="true">{icon}</span> {esc(label)}</span>'


def kpis(items: Sequence[tuple[str, str, str | None]]) -> str:
    """(etiket, değer, alt not) — tek sayının hikâyesi grafik değil, kutucuktur."""
    cards = "".join(
        f'<div class="kpi"><div class="kpi-label">{esc(label)}</div><div class="kpi-value">{esc(value)}</div>'
        + (f'<div class="kpi-note">{esc(note)}</div>' if note else "")
        + "</div>"
        for label, value, note in items
    )
    return f'<div class="kpis">{cards}</div>'


def table(headers: Sequence[str], rows: Sequence[Sequence[object]], numeric: Sequence[int] = (), raw: Sequence[int] = ()) -> str:
    """`raw` indeksli hücreler önceden güvenli üretilmiş HTML'dir (ör. badge); diğerleri kaçışlanır."""
    head = "".join(f'<th class="{"num" if i in numeric else ""}">{esc(h)}</th>' for i, h in enumerate(headers))
    body = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            content = cell if i in raw else esc(cell)
            cells.append(f'<td class="{"num" if i in numeric else ""}">{content}</td>')
        body.append(f"<tr>{''.join(cells)}</tr>")
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def data_table(summary: str, headers: Sequence[str], rows: Sequence[Sequence[object]], numeric: Sequence[int] = ()) -> str:
    """Grafiğin tablo görünümü (erişilebilirlik): katlanmış <details>."""
    return f'<details class="table-view"><summary>{esc(summary)}</summary>{table(headers, rows, numeric)}</details>'


_INLINE = [(re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"), (re.compile(r"`(.+?)`"), r"<code>\1</code>")]


def markdown(text: str) -> str:
    """Güvenli küçük Markdown alt kümesi: önce kaçış, sonra başlık/madde/kalın/kod. Ham HTML geçmez."""
    out, para, items = [], [], []

    def flush() -> None:
        if para:
            out.append(f"<p>{' '.join(para)}</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
            items.clear()

    for line in text.splitlines():
        s = html.escape(line.strip())
        for pattern, repl in _INLINE:
            s = pattern.sub(repl, s)
        if not s:
            flush()
        elif s.startswith("### "):
            flush()
            out.append(f"<h4>{s[4:]}</h4>")
        elif s.startswith("## "):
            flush()
            out.append(f"<h3>{s[3:]}</h3>")
        elif s.startswith(("- ", "* ")):
            if para:
                flush()
            items.append(s[2:])
        else:
            if items:
                flush()
            para.append(s)
    flush()
    return "\n".join(out)


def section(title: str, body: str, anchor: str | None = None) -> str:
    aid = f' id="{esc(anchor)}"' if anchor else ""
    return f"<section{aid}><h2>{esc(title)}</h2>{body}</section>"


def page(title: str, subtitle: str, sections: Sequence[str], attribution: str, nav: Sequence[tuple[str, str]] = ()) -> str:
    css = (ASSETS / "report.css").read_text(encoding="utf-8")
    js = (ASSETS / "report.js").read_text(encoding="utf-8")
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    nav_html = ""
    if nav:
        nav_html = '<nav class="toc">' + "".join(f'<a href="#{esc(a)}">{esc(t)}</a>' for a, t in nav) + "</nav>"
    return f"""<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{css}</style>
</head>
<body>
<div class="viz-root">
<header class="report-header">
  <div class="eyebrow">ML Booster Map</div>
  <h1>{esc(title)}</h1>
  <p class="subtitle">{esc(subtitle)}</p>
  {nav_html}
</header>
<main>
{"".join(sections)}
</main>
<footer>
  <p>{esc(attribution)}</p>
  <p class="muted">Oluşturulma: {esc(generated)} · mlbooster · Veriler kaynak kurumların kullanım koşullarına tabidir.</p>
</footer>
</div>
<script>{js}</script>
</body>
</html>
"""
