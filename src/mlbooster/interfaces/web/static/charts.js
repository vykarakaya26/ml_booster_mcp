// Bağımlılıksız SVG grafikleri: çizgi (crosshair + ipucu, aralık bandı, işaretler, olay çizgileri) ve yatay çubuk.
// Tek y ekseni; farklı ölçekteki seriler ayrı grafiklerde (small multiples) gösterilir.
const NS = "http://www.w3.org/2000/svg";
const nf = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 3 });

function s(tag, attrs = {}) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null) continue;
    // CSP satır içi style niteliğini engeller; CSSOM ataması serbesttir.
    if (k === "style") el.style.cssText = v;
    else el.setAttribute(k, v);
  }
  return el;
}
function div(cls, ...children) {
  const el = document.createElement("div");
  if (cls) el.className = cls;
  el.append(...children);
  return el;
}
const fmtNum = (v) => {
  if (v === null || v === undefined) return "—";
  const a = Math.abs(v);
  return new Intl.NumberFormat("tr-TR", { maximumFractionDigits: a >= 1000 ? 0 : a >= 100 ? 1 : a >= 1 ? 2 : 4 }).format(v);
};
const monthLabel = (iso) => `${iso.slice(5, 7)}.${iso.slice(0, 4)}`;

function niceTicks(min, max, count = 4) {
  if (min === max) { min -= 1; max += 1; }
  const span = max - min;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => span / x <= count + 1) || 10 * mag;
  const ticks = [];
  for (let t = Math.ceil(min / step) * step; t <= max + step * 1e-9; t += step) ticks.push(+t.toFixed(10));
  return ticks;
}

const tooltip = () => document.getElementById("tooltip");
function showTip(evt, title, rows) {
  const tip = tooltip();
  tip.replaceChildren(div("t-date", title), ...rows.map(([color, name, value]) => {
    const dot = document.createElement("i");
    dot.style.background = color;
    const left = document.createElement("span");
    left.append(dot, name);
    const right = document.createElement("strong");
    right.textContent = value;
    return div("t-row", left, right);
  }));
  tip.hidden = false;
  const pad = 14;
  const { innerWidth: W, innerHeight: H } = window;
  const r = tip.getBoundingClientRect();
  let x = evt.clientX + pad;
  let y = evt.clientY + pad;
  if (x + r.width > W - 8) x = evt.clientX - r.width - pad;
  if (y + r.height > H - 8) y = evt.clientY - r.height - pad;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}
const hideTip = () => { tooltip().hidden = true; };

function legend(items) {
  const el = div("legend");
  for (const it of items) {
    const sw = document.createElement("i");
    if (it.kind) sw.className = it.kind;
    sw.style.background = it.color;
    const span = document.createElement("span");
    span.append(sw, it.name);
    el.append(span);
  }
  return el;
}

export function lineChart({ dates, series, band = null, markers = null, events = [], height = 260, compact = false }) {
  const W = compact ? 360 : 860;
  const H = height;
  const m = compact ? { t: 8, r: 8, b: 20, l: 44 } : { t: events.length ? 34 : 12, r: 16, b: 26, l: 56 };
  const iw = W - m.l - m.r;
  const ih = H - m.t - m.b;
  const n = dates.length;

  const all = [];
  for (const sr of series) for (const v of sr.values) if (v !== null && Number.isFinite(v)) all.push(v);
  if (band) for (const v of [...band.lower, ...band.upper]) if (v !== null && Number.isFinite(v)) all.push(v);
  const wrap = div("chart-wrap");
  if (!n || !all.length) {
    wrap.append(div("muted small", "Veri yok."));
    return wrap;
  }
  let lo = Math.min(...all);
  let hi = Math.max(...all);
  const padY = (hi - lo || Math.abs(hi) || 1) * 0.06;
  lo -= padY; hi += padY;
  const ticks = niceTicks(lo, hi, compact ? 3 : 5);
  lo = Math.min(lo, ticks[0]); hi = Math.max(hi, ticks.at(-1));
  const x = (i) => m.l + (n === 1 ? iw / 2 : (i / (n - 1)) * iw);
  const y = (v) => m.t + ih - ((v - lo) / (hi - lo)) * ih;

  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": series.map((x) => x.name).join(", ") });
  for (const t of ticks) {
    svg.append(s("line", { class: "gridline", x1: m.l, x2: W - m.r, y1: y(t), y2: y(t) }));
    const label = s("text", { x: m.l - 6, y: y(t) + 4, "text-anchor": "end" });
    label.textContent = fmtNum(t);
    svg.append(label);
  }
  if (lo < 0 && hi > 0) svg.append(s("line", { class: "baseline", x1: m.l, x2: W - m.r, y1: y(0), y2: y(0) }));

  // x etiketleri: yıl başları, en çok ~8 etiket
  const years = [];
  dates.forEach((d, i) => { if (i === 0 || d.slice(0, 4) !== dates[i - 1].slice(0, 4)) years.push(i); });
  const maxLabels = compact ? 4 : 9;
  const every = Math.max(1, Math.ceil(years.length / maxLabels));
  years.filter((_, k) => k % every === 0).forEach((i) => {
    const t = s("text", { x: x(i), y: H - 6, "text-anchor": "middle" });
    t.textContent = years.length > 1 ? dates[i].slice(0, 4) : monthLabel(dates[i]);
    svg.append(t);
  });
  svg.append(s("line", { class: "baseline", x1: m.l, x2: W - m.r, y1: m.t + ih, y2: m.t + ih }));

  if (band) {
    // boşluklarda kesilen aralık bandı
    let seg = [];
    const flush = () => {
      if (seg.length > 1) {
        const top = seg.map((i) => `${x(i)},${y(band.upper[i])}`);
        const bottom = seg.slice().reverse().map((i) => `${x(i)},${y(band.lower[i])}`);
        svg.append(s("polygon", { class: "band", points: [...top, ...bottom].join(" "), style: `fill:${band.color}` }));
      }
      seg = [];
    };
    for (let i = 0; i < n; i++) (band.lower[i] !== null && band.upper[i] !== null ? seg.push(i) : flush());
    flush();
  }

  // olay etiketleri iki satıra dağıtılır, uzunlar kısaltılır (tam ad ipucunda)
  events.forEach((ev, k) => {
    const i = dates.findIndex((d) => d >= ev.date);
    if (i < 0) return;
    svg.append(s("line", { class: "event", x1: x(i), x2: x(i), y1: m.t, y2: m.t + ih }));
    const right = x(i) > W - m.r - 140;
    const t = s("text", { x: x(i) + (right ? -4 : 4), y: k % 2 ? m.t - 4 : m.t - 18, "text-anchor": right ? "end" : "start" });
    t.textContent = ev.label.length > 28 ? `${ev.label.slice(0, 27)}…` : ev.label;
    const title = s("title");
    title.textContent = `${monthLabel(ev.date)}: ${ev.label}`;
    t.append(title);
    svg.append(t);
  });

  for (const sr of series) {
    let d = "";
    let pen = false;
    sr.values.forEach((v, i) => {
      if (v === null || !Number.isFinite(v)) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    svg.append(s("path", { class: "line", d, style: `stroke:${sr.color}`, "stroke-dasharray": sr.dash }));
    // tek başına kalan noktalar (iki yanı boş) çizgide görünmez; küçük nokta çiz
    sr.values.forEach((v, i) => {
      if (v === null) return;
      const prev = sr.values[i - 1];
      const next = sr.values[i + 1];
      if ((prev === null || prev === undefined) && (next === null || next === undefined)) {
        svg.append(s("circle", { cx: x(i), cy: y(v), r: 2.5, style: `fill:${sr.color}` }));
      }
    });
  }

  if (markers) {
    const base = series[0];
    const set = new Set(markers.dates);
    dates.forEach((d, i) => {
      if (!set.has(d) || base.values[i] === null) return;
      svg.append(s("circle", { cx: x(i), cy: y(base.values[i]), r: 4.5, style: `fill:${markers.color};stroke:var(--surface);stroke-width:2` }));
    });
  }

  const cross = s("line", { class: "crosshair", y1: m.t, y2: m.t + ih, visibility: "hidden" });
  const dots = series.map((sr) => s("circle", { r: 4, visibility: "hidden", style: `fill:${sr.color};stroke:var(--surface);stroke-width:2` }));
  svg.append(cross, ...dots);
  const hit = s("rect", { class: "hit", x: m.l, y: m.t, width: iw, height: ih });
  svg.append(hit);
  hit.addEventListener("mousemove", (evt) => {
    const box = svg.getBoundingClientRect();
    const px = ((evt.clientX - box.left) / box.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - m.l) / iw) * (n - 1))));
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    const rows = [];
    series.forEach((sr, k) => {
      const v = sr.values[i];
      if (v === null || v === undefined) { dots[k].setAttribute("visibility", "hidden"); return; }
      dots[k].setAttribute("cx", x(i)); dots[k].setAttribute("cy", y(v)); dots[k].setAttribute("visibility", "visible");
      rows.push([sr.color, sr.name, fmtNum(v)]);
    });
    if (band && band.lower[i] !== null) rows.push([band.color, band.name, `${fmtNum(band.lower[i])} – ${fmtNum(band.upper[i])}`]);
    if (markers && markers.dates.includes(dates[i])) rows.push([markers.color, markers.name, "evet"]);
    showTip(evt, monthLabel(dates[i]), rows);
  });
  hit.addEventListener("mouseleave", () => {
    cross.setAttribute("visibility", "hidden");
    dots.forEach((d) => d.setAttribute("visibility", "hidden"));
    hideTip();
  });

  const items = series.length > 1 ? series.map((sr) => ({ name: sr.name, color: sr.color })) : [];
  if (band) items.push({ name: band.name, color: band.color, kind: "band" });
  if (markers) items.push({ name: markers.name, color: markers.color, kind: "dot" });
  if (items.length) wrap.append(legend(items));
  wrap.append(svg);
  return wrap;
}

export function barChart(items, { format = fmtNum } = {}) {
  // HTML çubuklar: dar kartlarda yazı küçülmesin diye SVG yerine akışkan düzen
  const wrap = div("bars");
  if (!items.length) { wrap.append(div("muted small", "Veri yok.")); return wrap; }
  const max = Math.max(...items.map((d) => d.value), 0) || 1;
  for (const d of items) {
    const fill = div("bar-fill" + (d.highlight ? " highlight" : ""));
    fill.style.width = `${Math.max(1, (d.value / max) * 100)}%`;
    const row = div("bar-row", div("bar-label", d.label), div("bar-track", fill), div("bar-value", format(d.value)));
    const color = d.highlight ? "var(--series-1)" : "var(--axis)";
    row.addEventListener("mousemove", (evt) => showTip(evt, d.label, [[color, "Değer", format(d.value)]]));
    row.addEventListener("mouseleave", hideTip);
    wrap.append(row);
  }
  return wrap;
}
