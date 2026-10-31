"""Bağımlılıksız SVG grafikler (dataviz yöntemi: tek eksen, ince işaretler, silik ızgara).

Renkler CSS değişkenleriyle verilir (`var(--series-1)` vb.); açık/koyu tema `report.css`'te.
Hover katmanı `report.js` içindedir: her grafik `data-chart` özniteliğinde verisini taşır.
"""

from __future__ import annotations

import html
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date

W, H = 640, 220
PAD_L, PAD_R, PAD_T, PAD_B = 52, 16, 22, 28


@dataclass
class Series:
    label: str
    values: Sequence[float | None]
    slot: int = 1  # kategorik slot (1–3); sabit sırada atanır, sıralamaya göre değil


@dataclass
class Marker:
    when: date
    label: str


@dataclass
class Flag:
    when: date
    value: float
    label: str


@dataclass
class Band:
    """Tahmin aralığı (ör. %10–%90): alt ve üst sınır arasında yarı saydam alan, ilgili serinin renginde."""
    label: str
    lower: Sequence[float | None]
    upper: Sequence[float | None]
    slot: int = 2


@dataclass
class LineChart:
    dates: Sequence[date]
    series: list[Series]
    title: str
    markers: list[Marker] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    bands: list[Band] = field(default_factory=list)
    y_format: Callable[[float], str] | None = None  # None: ondalık sayısı tick aralığından
    height: int = H


def _nice_step(span: float, target: int = 4) -> float:
    if span <= 0 or not math.isfinite(span):
        return 1.0
    raw = span / target
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _ticks(lo: float, hi: float) -> list[float]:
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    step = _nice_step(hi - lo)
    start = math.floor(lo / step) * step
    ticks, v = [], start
    while v <= hi + step * 0.5:
        ticks.append(round(v, 10))
        v += step
    return ticks


def _ordinal(d: date) -> int:
    return d.toordinal()


def line_chart(chart: LineChart) -> str:
    """Bir ya da birkaç seriyi aynı (tek) y-ekseninde çizer. Farklı ölçekler için küçük çoklu kullanın."""
    dates = list(chart.dates)
    h = chart.height
    values = [v for s in chart.series for v in s.values if v is not None] + [f.value for f in chart.flags]
    values += [v for b in chart.bands for v in (*b.lower, *b.upper) if v is not None]
    if not dates or not values:
        return f'<p class="muted">{html.escape(chart.title)}: veri yok.</p>'
    yt = _ticks(min(values), max(values))
    y_lo, y_hi = yt[0], yt[-1]
    step = yt[1] - yt[0] if len(yt) > 1 else 1.0
    decimals = max(0, -math.floor(math.log10(step))) if step > 0 else 0
    y_format = chart.y_format or (lambda v: f"{v:,.{decimals}f}")
    x_lo, x_hi = _ordinal(dates[0]), _ordinal(dates[-1]) or 1
    plot_w, plot_h = W - PAD_L - PAD_R, h - PAD_T - PAD_B

    def sx(d: date) -> float:
        return PAD_L + (0 if x_hi == x_lo else (_ordinal(d) - x_lo) / (x_hi - x_lo) * plot_w)

    def sy(v: float) -> float:
        return PAD_T + (1 - (v - y_lo) / ((y_hi - y_lo) or 1)) * plot_h

    out = [
        f'<svg viewBox="0 0 {W} {h}" role="img" aria-label="{html.escape(chart.title)}" class="chart" '
        f'preserveAspectRatio="xMidYMid meet">'
    ]
    # Izgara + y etiketleri (silik, düz çizgi)
    for t in yt:
        y = sy(t)
        out.append(f'<line class="grid" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="tick" x="{PAD_L - 6}" y="{y + 3.5:.1f}" text-anchor="end">{html.escape(y_format(t))}</text>')
    # x etiketleri: yıllar, en fazla ~8 etiket
    years = sorted({d.year for d in dates})
    step = max(1, math.ceil(len(years) / 8))
    for yr in years[::step]:
        d = date(yr, 1, 1)
        if dates[0] <= d <= dates[-1] or yr == years[0]:
            x = sx(max(d, dates[0]))
            out.append(f'<text class="tick" x="{x:.1f}" y="{h - 8}" text-anchor="middle">{yr}</text>')
    out.append(f'<line class="axis" x1="{PAD_L}" x2="{W - PAD_R}" y1="{PAD_T + plot_h}" y2="{PAD_T + plot_h}"/>')
    if y_lo < 0 < y_hi:
        out.append(f'<line class="zero" x1="{PAD_L}" x2="{W - PAD_R}" y1="{sy(0):.1f}" y2="{sy(0):.1f}"/>')
    # Olay işaretleri (kırılma tarihleri vb.)
    for m in chart.markers:
        if dates[0] <= m.when <= dates[-1]:
            x = sx(m.when)
            out.append(f'<line class="marker" x1="{x:.1f}" x2="{x:.1f}" y1="{PAD_T}" y2="{PAD_T + plot_h}"/>')
            out.append(f'<text class="marker-label" x="{x + 3:.1f}" y="{PAD_T - 6}">{html.escape(m.label)}</text>')
    # Bantlar (çizgilerin altında): kesintisiz parçalar için ayrı çokgenler
    for b in chart.bands:
        segment: list[tuple[date, float, float]] = []
        for d, lo, hi in [*zip(dates, b.lower, b.upper), (None, None, None)]:
            if d is not None and lo is not None and hi is not None:
                segment.append((d, lo, hi))
                continue
            if len(segment) >= 2:
                top = " ".join(f"{sx(x):.1f},{sy(h):.1f}" for x, _, h in segment)
                bottom = " ".join(f"{sx(x):.1f},{sy(lo_):.1f}" for x, lo_, _ in reversed(segment))
                out.append(f'<polygon class="band s{b.slot}" points="{top} {bottom}"><title>{html.escape(b.label)}</title></polygon>')
            segment = []
    # Seriler: boşluklarda çizgi kesilir
    for s in chart.series:
        path, pen = [], False
        for d, v in zip(dates, s.values):
            if v is None:
                pen = False
                continue
            path.append(f"{'L' if pen else 'M'}{sx(d):.1f},{sy(v):.1f}")
            pen = True
        if path:
            out.append(f'<path class="line s{s.slot}" d="{" ".join(path)}"/>')
    # Uyarı noktaları (aykırı değerler): halka + durum rengi
    for f in chart.flags:
        out.append(
            f'<circle class="flag" cx="{sx(f.when):.1f}" cy="{sy(f.value):.1f}" r="4.5">'
            f"<title>{html.escape(f.label)}</title></circle>"
        )
    out.append(f'<line class="crosshair" x1="0" x2="0" y1="{PAD_T}" y2="{PAD_T + plot_h}" visibility="hidden"/>')
    out.append("</svg>")
    payload = {
        "x": [d.isoformat() for d in dates],
        "px": [round(sx(d), 1) for d in dates],
        "w": W,
        "series": [{"label": s.label, "slot": s.slot, "v": list(s.values)} for s in chart.series],
    }
    data_attr = html.escape(json.dumps(payload, ensure_ascii=False, allow_nan=False), quote=True)
    legend = ""
    if len(chart.series) >= 2 or chart.bands:
        items = "".join(
            f'<span class="legend-item"><span class="swatch s{s.slot}"></span>{html.escape(s.label)}</span>'
            for s in chart.series
        )
        items += "".join(
            f'<span class="legend-item"><span class="swatch band-swatch s{b.slot}"></span>{html.escape(b.label)}</span>'
            for b in chart.bands
        )
        legend = f'<div class="legend">{items}</div>'
    return (
        f'<figure class="chart-card" data-chart="{data_attr}">'
        f'<figcaption>{html.escape(chart.title)}</figcaption>{legend}'
        f'<div class="chart-wrap">{"".join(out)}<div class="tooltip" hidden></div></div></figure>'
    )


def bar_chart(labels: Sequence[str], values: Sequence[float], title: str, fmt: Callable[[float], str] = lambda v: f"{v:,.3f}", highlight: str | None = None) -> str:
    """Yatay çubuk: tek seri, tek renk; vurgulanan çubuk tam renk, diğerleri silik."""
    if not labels:
        return ""
    row_h, label_w = 30, 150
    h = row_h * len(labels) + 12
    vmax = max(max(values), 0) or 1
    plot_w = W - label_w - 80
    out = [f'<svg viewBox="0 0 {W} {h}" role="img" aria-label="{html.escape(title)}" class="chart">']
    for i, (lab, val) in enumerate(zip(labels, values)):
        y = 6 + i * row_h
        bw = max(2.0, val / vmax * plot_w)
        cls = "bar" + (" muted-bar" if highlight and lab != highlight else "")
        out.append(f'<text class="bar-label" x="{label_w - 8}" y="{y + 17}" text-anchor="end">{html.escape(lab)}</text>')
        out.append(
            f'<rect class="{cls}" x="{label_w}" y="{y + 4}" width="{bw:.1f}" height="{row_h - 10}" rx="4">'
            f"<title>{html.escape(lab)}: {html.escape(fmt(val))}</title></rect>"
        )
        out.append(f'<text class="bar-value" x="{label_w + bw + 6:.1f}" y="{y + 17}">{html.escape(fmt(val))}</text>')
    out.append("</svg>")
    return f'<figure class="chart-card"><figcaption>{html.escape(title)}</figcaption>{"".join(out)}</figure>'
