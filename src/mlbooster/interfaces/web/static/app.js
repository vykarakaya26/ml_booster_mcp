// ML Booster yerel paneli. Dış kütüphane yok; DOM yalnızca textContent ile doldurulur (innerHTML kullanılmaz).
import { lineChart, barChart } from "./charts.js";

const TOKEN = document.querySelector('meta[name="mlb-token"]').content;
const $ = (sel) => document.querySelector(sel);

// --- yardımcılar -----------------------------------------------------------------------------------------

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "style") el.style.cssText = v; // CSP satır içi style niteliğini engeller; CSSOM serbest
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === undefined || c === null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const nf = (digits = 2) => new Intl.NumberFormat("tr-TR", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
export function fmt(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const abs = Math.abs(v);
  const d = abs >= 1000 ? 0 : abs >= 100 ? 1 : digits;
  return nf(d).format(v);
}
const pct = (v, digits = 1) => (v === null || v === undefined ? "—" : `%${nf(digits).format(v * 100)}`);
const monthLabel = (iso) => {
  const [y, m] = iso.split("-");
  return `${m}.${y}`;
};

async function api(path, body) {
  const opts = { headers: { "X-MLB-Token": TOKEN } };
  if (body !== undefined) {
    opts.method = "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = `İstek başarısız (${res.status}).`;
    try { msg = (await res.json()).error || msg; } catch { /* gövde JSON değil */ }
    throw new Error(msg);
  }
  return res;
}
const getJSON = async (path) => (await api(path)).json();
const postJSON = async (path, body) => (await api(path, body)).json();

function notice(text, isError = false) {
  const el = $("#notice");
  if (!text) { el.hidden = true; return; }
  el.hidden = false;
  el.className = "notice" + (isError ? " error" : "");
  el.textContent = text;
}

const WAIT_TEXT = "Veriler hazırlanıyor. Önbellekte olmayan seriler kaynaktan çekildiği için ilk çalıştırma birkaç dakika sürebilir.";

async function busy(button, label, fn, out = null) {
  const original = button.textContent;
  if (out) out.replaceChildren(h("p", { class: "empty" }, h("span", { class: "spinner", "aria-hidden": "true" }), WAIT_TEXT));
  button.disabled = true;
  button.replaceChildren(h("span", { class: "spinner", "aria-hidden": "true" }), label);
  notice(null);
  try {
    return await fn();
  } catch (err) {
    if (out) out.replaceChildren();
    notice(err.message, true);
    return null;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function table(columns, rows, { rowClass, scroll = false } = {}) {
  return h("div", { class: scroll ? "table-wrap scroll" : "table-wrap" },
    h("table", {},
      h("thead", {}, h("tr", {}, columns.map((c) => h("th", { class: c.num ? "num" : null, scope: "col" }, c.label)))),
      h("tbody", {}, rows.map((r) =>
        h("tr", { class: rowClass ? rowClass(r) : null },
          columns.map((c) => h("td", { class: c.num ? "num" : null }, c.render ? c.render(r) : r[c.key]))))),
    ));
}

const tile = (label, value, sub) =>
  h("div", { class: "tile" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), sub ? h("div", { class: "sub" }, sub) : null);

const status = (kind, label) => h("span", { class: `status ${kind}` }, label);

function viewHead(title, text) {
  return h("div", { class: "view-head" }, h("h1", {}, title), text ? h("p", {}, text) : null);
}

function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// --- durum -----------------------------------------------------------------------------------------------

const state = {
  meta: null,
  sectors: [],
  maps: new Map(), // sektör → harita
  panels: new Map(), // anahtar → panel yanıtı
  results: { backtest: new Map(), audit: new Map() },
};

const PREFS_KEY = "mlbooster-panel";
function loadPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; } catch { return {}; }
}
function savePrefs(patch) {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify({ ...loadPrefs(), ...patch })); } catch { /* özel pencere */ }
}

function scope() {
  const start = $("#start").value;
  const end = $("#end").value;
  return {
    sector: $("#sector").value,
    start: start ? `${start}-01` : state.meta.default_start,
    end: end ? `${end}-28` : undefined,
  };
}
const scopeKey = (extra = {}) => JSON.stringify({ ...scope(), ...extra });

async function sectorMap(sector) {
  if (!state.maps.has(sector)) state.maps.set(sector, await getJSON(`/api/sectors/${encodeURIComponent(sector)}/map`));
  return state.maps.get(sector);
}

const MODEL_LABELS = {
  naive: ["Naive", "Son gözlenen değer"],
  seasonal_naive: ["Mevsimsel naive", "12 ay önceki değer"],
  drift: ["Drift", "Tüm dönemin ortalama eğilimi"],
  drift_recent: ["Yakın dönem drift", "Son 6 ayın eğilimi"],
  mean12: ["12 ay ortalaması", "Son 12 ayın ortalaması"],
  median12: ["12 ay medyanı", "Son 12 ayın medyanı"],
  ridge: ["Ridge regresyon", "Gecikmeli feature'larla düzenlileştirilmiş regresyon"],
  qr: ["Kantil regresyon", "%10–%90 aralıklı medyan regresyonu"],
  combo_mean: ["Kombinasyon (ortalama)", "Üye modellerin ortalaması"],
  combo_median: ["Kombinasyon (medyan)", "Üye modellerin medyanı"],
  auto_robust: ["Otomatik dayanıklı", "Sapma yüksekse medyana geçen kombinasyon"],
  anchor_naive: ["Çapa naive", "Hedefin daha güncel sürümünün son değeri"],
};
const modelName = (m) => (MODEL_LABELS[m] || [m])[0];

const FEATURE_STATUS = {
  hazir: ["good", "Hazır"],
  kesif_bekliyor: ["warning", "Keşif bekliyor"],
  saglayici_yok: ["", "Sağlayıcı henüz yok"],
};

// --- görünümler ------------------------------------------------------------------------------------------

const views = {};

views.harita = async (root) => {
  const sector = $("#sector").value;
  const m = await sectorMap(sector);
  const ready = m.macro_features.filter((f) => f.status === "hazir").length;
  const pending = m.macro_features.filter((f) => f.status === "kesif_bekliyor").length;
  root.replaceChildren(
    viewHead(`${m.sector.name}: veri kaynağı haritası`,
      "Sektör için tanımlı ücretsiz kaynaklar, makro feature'lar ve pazar payı kaynakları. Hazır feature'lar panelde kullanılır; keşif bekleyenler için EVDS seri kodu henüz bağlanmamıştır."),
    h("div", { class: "tiles" },
      tile("Kaynak", m.sources.length, `${m.sources.filter((s) => s.provider_ready).length} kaynak için sağlayıcı hazır`),
      tile("Hazır feature", ready, `toplam ${m.macro_features.length} makro feature`),
      tile("Keşif bekleyen", pending, pending ? "seri kodu bağlanmalı" : "eksik yok"),
      tile("Pazar payı kaynağı", m.market_share.length, `${m.market_share.filter((s) => s.is_proxy).length} proxy`)),
    m.sector.ml_use_cases.length
      ? h("div", { class: "card" }, h("h2", {}, "ML kullanım alanları"), h("div", { class: "chips" }, m.sector.ml_use_cases.map((u) => h("span", { class: "chip" }, u))))
      : null,
    h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h2", {}, "Makro feature'lar"), h("p", {}, "Panel bu tablodaki hazır feature'lardan üretilir.")),
      table([
        { label: "Feature", render: (f) => h("code", {}, f.name) },
        { label: "Kaynak", key: "source" },
        { label: "Seri", render: (f) => (f.series ? h("code", {}, f.series) : h("span", { class: "muted small" }, f.discover_hint || "—")) },
        { label: "Dönüşüm", key: "transform" },
        { label: "Durum", render: (f) => status(...(FEATURE_STATUS[f.status] || ["", f.status])) },
      ], m.macro_features)),
    h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h2", {}, "Kaynaklar")),
      table([
        { label: "Kaynak", render: (s) => h("span", {}, s.name, " ", h("code", {}, s.id)) },
        { label: "Rol", render: (s) => s.roles.join(", ") },
        { label: "Erişim", key: "access" },
        { label: "Kimlik doğrulama", key: "auth" },
        { label: "Frekans", render: (s) => s.frequency.join(", ") },
        { label: "Yayın gecikmesi (gün)", key: "publication_lag_days", num: true },
        { label: "Doğrulama", key: "verification_status" },
        { label: "Sağlayıcı", render: (s) => (s.provider_ready ? status("good", "Hazır") : status("", "Yok")) },
      ], m.sources)),
    h("div", { class: "grid-2" },
      h("div", { class: "card" }, h("h2", {}, "Pazar payı kaynakları"),
        m.market_share.length
          ? h("ul", {}, m.market_share.map((s) => h("li", {}, h("code", {}, s.source), `: ${s.metric}`, s.is_proxy ? " (proxy)" : "")))
          : h("p", { class: "muted" }, "Tanımlı kaynak yok.")),
      h("div", { class: "card" }, h("h2", {}, "Lisans notları"),
        h("ul", { class: "small" }, m.sources.map((s) => h("li", {}, h("code", {}, s.id), `: ${s.license}`))))),
  );
};

views.panel = async (root) => {
  const sector = $("#sector").value;
  const m = await sectorMap(sector);
  const prefs = loadPrefs();
  const freq = h("select", { "aria-label": "Panel frekansı" },
    [["M", "Aylık"], ["W", "Haftalık (Cuma)"], ["B", "İş günü"]].map(([v, l]) => h("option", { value: v, selected: (prefs.freq || "M") === v }, l)));
  const out = h("div", { class: "stack", style: "display:flex;flex-direction:column;gap:20px" });
  const run = h("button", { type: "button" }, "Paneli üret");
  const csv = h("button", { type: "button", class: "ghost" }, "CSV indir");
  const parquet = h("button", { type: "button", class: "ghost" }, "Parquet indir");

  const exportAs = (format, btn) => busy(btn, "Hazırlanıyor", async () => {
    const res = await api("/api/panel/export", { ...scope(), freq: freq.value, format });
    downloadBlob(await res.blob(), `${sector}_${freq.value.toLowerCase()}_panel.${format}`);
  });
  csv.addEventListener("click", () => exportAs("csv", csv));
  parquet.addEventListener("click", () => exportAs("parquet", parquet));

  const render = (p) => {
    const selected = new Set(loadPrefs()[`features:${sector}`] || p.features);
    const grid = h("div", { class: "multiples" });
    const drawGrid = () => {
      grid.replaceChildren(...p.features.filter((f) => selected.has(f)).map((f) => {
        const values = p.columns[f];
        const last = [...values].reverse().find((v) => v !== null);
        const box = h("div", { class: "multiple" }, h("h3", {}, f, h("span", {}, `son: ${fmt(last)}`)));
        box.append(lineChart({ dates: p.dates, series: [{ name: f, values, color: "var(--series-1)" }], height: 150, compact: true }));
        return box;
      }));
      if (!grid.children.length) grid.append(h("p", { class: "empty" }, "Gösterilecek feature seçilmedi."));
    };
    const picks = h("div", { class: "checks" }, p.features.map((f) =>
      h("label", { class: "check" },
        h("input", { type: "checkbox", checked: selected.has(f), onchange: (e) => {
          e.target.checked ? selected.add(f) : selected.delete(f);
          savePrefs({ [`features:${sector}`]: [...selected] });
          drawGrid();
        } }), f)));
    drawGrid();
    const skipped = Object.entries(p.skipped);
    const lastRows = p.dates.slice(-12).map((d, i, arr) => ({ date: d, idx: p.dates.length - arr.length + i }));
    out.replaceChildren(
      h("div", { class: "tiles" },
        tile("Dönem", p.rows, p.dates.length ? `${monthLabel(p.dates[0])} – ${monthLabel(p.dates.at(-1))}` : ""),
        tile("Feature", p.features.length, `${m.macro_features.length} tanımlı feature'dan`),
        tile("Atlanan", skipped.length, skipped.length ? "ayrıntı aşağıda" : "atlanan yok")),
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Feature seçimi"), h("p", {}, "Her feature kendi ölçeğinde ayrı grafikte gösterilir; seçim tarayıcıda hatırlanır.")),
        picks),
      grid,
      skipped.length
        ? h("div", { class: "card" }, h("h2", {}, "Atlanan feature'lar"),
          table([{ label: "Feature", render: (r) => h("code", {}, r[0]) }, { label: "Neden", render: (r) => r[1] }], skipped))
        : null,
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Son 12 dönem"), h("p", {}, "Tablo görünümü; tüm veri için CSV ya da Parquet indirin.")),
        table([{ label: "Tarih", key: "date" }, ...p.features.map((f) => ({ label: f, num: true, render: (r) => fmt(p.columns[f][r.idx], 3) }))], lastRows)),
    );
  };

  run.addEventListener("click", () => busy(run, "Üretiliyor", async () => {
    savePrefs({ freq: freq.value });
    const key = scopeKey({ freq: freq.value });
    const p = await postJSON("/api/panel", { ...scope(), freq: freq.value });
    state.panels.set(key, p);
    render(p);
  }, out));

  root.replaceChildren(
    viewHead(`${m.sector.name}: feature paneli`,
      "Hazır feature'lardan nokta-zamanlı panel üretilir: her gözlem, yayın gecikmesi eklenerek kullanılabilir olduğu döneme yazılır. Üretilen panel yerel veri dizinine Parquet olarak kaydedilir."),
    h("div", { class: "card" },
      h("div", { class: "controls" }, h("label", { class: "field" }, h("span", {}, "Frekans"), freq), h("div", { class: "button-row" }, run, csv, parquet))),
    out,
  );
  const cached = state.panels.get(scopeKey({ freq: freq.value }));
  if (cached) render(cached);
  else out.append(h("p", { class: "empty" }, "Paneli üretmek için düğmeye basın. İlk çalıştırmada veriler kaynaktan çekilir; sonraki çalıştırmalar yerel önbellekten okunur."));
};

views.model = async (root) => {
  const sector = $("#sector").value;
  const m = await sectorMap(sector);
  const prefs = loadPrefs();
  const features = m.macro_features.filter((f) => f.status === "hazir").map((f) => f.name);
  if (!features.length) {
    root.replaceChildren(viewHead("Model karşılaştırma"), h("p", { class: "empty" }, "Bu sektörde hazır feature yok."));
    return;
  }
  const targetPref = prefs[`target:${sector}`];
  const target = h("select", { "aria-label": "Hedef" }, features.map((f) => h("option", { value: f, selected: f === targetPref }, f)));
  const anchor = h("select", { "aria-label": "Çapa" }, h("option", { value: "" }, "Yok"), features.map((f) => h("option", { value: f, selected: f === prefs[`anchor:${sector}`] }, f)));
  const horizon = h("input", { type: "number", min: 1, max: 24, value: prefs.horizon || 1, style: "width:90px" });
  const minTrain = h("input", { type: "number", min: 12, max: 240, value: prefs.min_train || 48, style: "width:100px" });
  const exog = h("input", { type: "checkbox", checked: prefs.exogenous !== false });
  const chosen = new Set(prefs.models || state.meta.models);
  const modelChecks = h("div", { class: "checks" }, state.meta.models.map((mm) =>
    h("label", { class: "check" },
      h("input", { type: "checkbox", checked: chosen.has(mm), onchange: (e) => (e.target.checked ? chosen.add(mm) : chosen.delete(mm)) }),
      h("span", {}, modelName(mm), h("small", {}, MODEL_LABELS[mm]?.[1] || "")))));
  const run = h("button", { type: "button" }, "Backtest'i çalıştır");
  const out = h("div", { style: "display:flex;flex-direction:column;gap:20px" });

  const render = (r) => {
    const metrics = [...r.metrics].sort((a, b) => (a.mae ?? Infinity) - (b.mae ?? Infinity));
    const models = metrics.map((x) => x.model);
    const pick = h("select", { "aria-label": "Grafikteki model" }, models.map((mm) => h("option", { value: mm, selected: mm === r.best_model }, modelName(mm))));
    const compare = h("select", { "aria-label": "Karşılaştırma modeli" }, h("option", { value: "" }, "Yok"), models.map((mm) => h("option", { value: mm, selected: mm === "naive" && mm !== r.best_model }, modelName(mm))));
    const chartBox = h("div");
    const draw = () => {
      const byModel = (mm) => {
        const rows = r.predictions.filter((p) => p.model === mm);
        return new Map(rows.map((p) => [p.date, p]));
      };
      const main = byModel(pick.value);
      const histDates = r.history.dates;
      const firstPred = r.predictions.length ? r.predictions.map((p) => p.date).sort()[0] : null;
      const cut = firstPred ? Math.max(0, histDates.findIndex((d) => d >= firstPred) - 36) : 0;
      const dates = histDates.slice(cut);
      const series = [
        { name: "Gerçekleşen", values: r.history.values.slice(cut), color: "var(--ink-2)" },
        { name: `${modelName(pick.value)} tahmini`, values: dates.map((d) => main.get(d)?.y_pred ?? null), color: "var(--series-1)" },
      ];
      if (compare.value && compare.value !== pick.value) {
        const other = byModel(compare.value);
        series.push({ name: `${modelName(compare.value)} tahmini`, values: dates.map((d) => other.get(d)?.y_pred ?? null), color: "var(--series-2)", dash: "5 4" });
      }
      const band = {
        name: "%80 tahmin aralığı",
        lower: dates.map((d) => main.get(d)?.lower ?? null),
        upper: dates.map((d) => main.get(d)?.upper ?? null),
        color: "var(--series-1)",
      };
      chartBox.replaceChildren(lineChart({ dates, series, band: band.lower.some((v) => v !== null) ? band : null, height: 320 }));
    };
    pick.addEventListener("change", draw);
    compare.addEventListener("change", draw);
    draw();
    const best = metrics[0];
    const naive = metrics.find((x) => x.model === "naive");
    const gain = best && naive && naive.mae ? 1 - best.mae / naive.mae : null;
    out.replaceChildren(
      h("div", { class: "tiles" },
        tile("En düşük MAE", modelName(r.best_model), best ? `MAE ${fmt(best.mae, 3)}` : ""),
        tile("Naive'e göre", gain === null ? "—" : `%${nf(1).format(gain * 100)}`, "MAE iyileşmesi (pozitif: daha iyi)"),
        tile("Ortak başlangıç noktası", r.common_origins, "metriklerin hesaplandığı dönem sayısı"),
        tile("Ufuk", `${r.config.horizon} dönem`, `asgari eğitim: ${r.config.min_train} dönem`)),
      h("div", { class: "card" },
        h("div", { class: "card-head" },
          h("h2", {}, `${r.config.target}: gerçekleşen ve tahmin`),
          h("div", { class: "controls" }, h("label", { class: "field" }, h("span", {}, "Model"), pick), h("label", { class: "field" }, h("span", {}, "Karşılaştır"), compare))),
        chartBox,
        h("p", { class: "small muted" }, "Her tahmin yalnızca başlangıç noktasına kadar bilinen veriyle üretilmiştir (rolling-origin, look-ahead korumalı). Gölgeli alan, modelin geçmiş hatalarından kurulan %80 aralıktır.")),
      h("div", { class: "grid-2" },
        h("div", { class: "card" }, h("h2", {}, "Ortalama mutlak hata (MAE)"),
          barChart(metrics.filter((x) => x.mae !== null).map((x) => ({ label: modelName(x.model), value: x.mae, highlight: x.model === r.best_model })), { format: (v) => fmt(v, 3) }),
          h("p", { class: "small muted" }, "Düşük değer daha iyidir. Vurgulanan çubuk en düşük MAE'ye sahip modeldir.")),
        h("div", { class: "card" }, h("h2", {}, "Rejimlere göre MAE"),
          r.regime_metrics.length
            ? table([
              { label: "Dönem", key: "regime" },
              { label: "Model", render: (x) => modelName(x.model) },
              { label: "n", key: "n", num: true },
              { label: "MAE", num: true, render: (x) => fmt(x.mae, 3) },
            ], r.regime_metrics.filter((x) => x.model === r.best_model || x.model === "naive"))
            : h("p", { class: "muted" }, "Rejim ayrımı için yeterli gözlem yok."),
          h("p", { class: "small muted" }, "En iyi model ile naive karşılaştırması; sonuçların tek bir döneme bağlı olup olmadığını gösterir."))),
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Tüm metrikler"), h("p", {}, "MASE < 1: naive'den iyi. Kapsama hedefi %80.")),
        table([
          { label: "Model", render: (x) => h("span", {}, modelName(x.model), " ", h("code", {}, x.model)) },
          { label: "n", key: "n", num: true },
          { label: "MAE", num: true, render: (x) => fmt(x.mae, 3) },
          { label: "RMSE", num: true, render: (x) => fmt(x.rmse, 3) },
          { label: "sMAPE", num: true, render: (x) => fmt(x.smape, 2) },
          { label: "MASE", num: true, render: (x) => fmt(x.mase, 3) },
          { label: "%80 kapsama", num: true, render: (x) => pct(x.coverage80) },
          { label: "Aralık genişliği", num: true, render: (x) => fmt(x.width80, 3) },
          { label: "Pinball", num: true, render: (x) => fmt(x.pinball, 3) },
        ], metrics, { rowClass: (x) => (x.model === r.best_model ? "best" : null) })),
      r.features_used.length
        ? h("details", { class: "card" }, h("summary", {}, `Ridge/kantil modellerinde kullanılan feature'lar (${r.features_used.length})`),
          h("div", { class: "chips" }, r.features_used.map((f) => h("span", { class: "chip" }, f))))
        : null,
    );
  };

  run.addEventListener("click", () => busy(run, "Çalışıyor", async () => {
    const body = {
      ...scope(), target: target.value, anchor: anchor.value || undefined,
      horizon: Number(horizon.value), min_train: Number(minTrain.value), exogenous: exog.checked,
      models: state.meta.models.filter((mm) => chosen.has(mm)),
    };
    if (!body.models.length) throw new Error("En az bir model seçin.");
    savePrefs({ [`target:${sector}`]: body.target, [`anchor:${sector}`]: anchor.value, horizon: body.horizon, min_train: body.min_train, exogenous: body.exogenous, models: body.models });
    const r = await postJSON("/api/backtest", body);
    state.results.backtest.set(sector, r);
    render(r);
  }, out));

  root.replaceChildren(
    viewHead(`${m.sector.name}: model karşılaştırma`,
      "Hedef seriyi ve modelleri seçin. Backtest, her başlangıç noktasında yalnızca o güne kadar bilinen veriyle tahmin üretir. Sonuç, bir ML modelinin aşması gereken referans çıtayı gösterir."),
    h("div", { class: "card" },
      h("div", { class: "controls" },
        h("label", { class: "field" }, h("span", {}, "Hedef"), target),
        h("label", { class: "field" }, h("span", {}, "Çapa (isteğe bağlı)"), anchor),
        h("label", { class: "field" }, h("span", {}, "Ufuk (dönem)"), horizon),
        h("label", { class: "field" }, h("span", {}, "Asgari eğitim (dönem)"), minTrain),
        h("label", { class: "check", style: "align-self:center" }, exog, "Diğer feature'ları ridge/kantil modelinde kullan")),
      h("h3", {}, "Modeller"),
      modelChecks,
      h("div", { class: "button-row" }, run,
        h("button", { type: "button", class: "ghost", onclick: () => { state.meta.models.forEach((mm) => chosen.add(mm)); modelChecks.querySelectorAll("input").forEach((i) => (i.checked = true)); } }, "Tümünü seç"))),
    out,
  );
  const cached = state.results.backtest.get(sector);
  if (cached) render(cached);
};

const CHECKS = {
  stale: "Güncellik", missing: "Eksik dönem", outliers: "Aykırı değer", structural_break: "Yapısal kırılma",
  collinearity: "Aşırı korelasyon", constant: "Sabit seri", empty: "Boş seri", heavy_tails: "Kalın kuyruk",
  nonstationary: "Durağan değil", managed_path: "Yönetilen patika",
};
const SEVERITY = { high: ["critical", "Yüksek"], medium: ["warning", "Orta"], info: ["", "Bilgi"] };
const STATIONARITY = { "durağan": "Durağan", "birim_kök": "Birim kök", "kırılma_şüphesi": "Kırılma şüphesi", "kararsız": "Kararsız" };

views.kalite = async (root) => {
  const sector = $("#sector").value;
  const m = await sectorMap(sector);
  const run = h("button", { type: "button" }, "Denetimi çalıştır");
  const out = h("div", { style: "display:flex;flex-direction:column;gap:20px" });

  const render = (q) => {
    let filter = "all";
    const issueBox = h("div");
    const drawIssues = () => {
      const rows = q.issues.filter((i) => filter === "all" || i.severity === filter);
      issueBox.replaceChildren(rows.length
        ? table([
          { label: "Önem", render: (i) => status(...(SEVERITY[i.severity] || ["", i.severity])) },
          { label: "Feature", render: (i) => h("code", {}, i.feature) },
          { label: "Denetim", render: (i) => CHECKS[i.check] || i.check },
          { label: "Bulgu", key: "message" },
        ], rows, { scroll: true })
        : h("p", { class: "muted" }, "Bu filtrede bulgu yok."));
    };
    const filters = h("div", { class: "button-row" }, [["all", "Tümü"], ["high", "Yüksek"], ["medium", "Orta"], ["info", "Bilgi"]].map(([v, l]) =>
      h("button", { type: "button", class: v === "all" ? null : "ghost", onclick: (e) => {
        filter = v;
        filters.querySelectorAll("button").forEach((b) => (b.className = "ghost"));
        e.currentTarget.className = "";
        drawIssues();
      } }, l)));
    drawIssues();

    const seriesBox = h("div");
    const seriesPick = h("select", { "aria-label": "Seri" }, q.series.map((s) => h("option", { value: s.feature }, s.feature)));
    const drawSeries = () => {
      const s = q.series.find((x) => x.feature === seriesPick.value);
      if (!s || !q.columns[s.feature]) { seriesBox.replaceChildren(h("p", { class: "muted" }, "Seri verisi yok.")); return; }
      seriesBox.replaceChildren(lineChart({
        dates: q.dates,
        series: [{ name: s.feature, values: q.columns[s.feature], color: "var(--series-1)" }],
        markers: { name: "Aykırı değer", dates: s.outliers, color: "var(--series-2)" },
        events: s.breaks.map((b) => ({ date: b.date, label: b.event || "kırılma" })),
        height: 260,
      }));
    };
    seriesPick.addEventListener("change", drawSeries);
    drawSeries();

    const diag = new Map((q.diagnostics || []).map((d) => [d.feature, d]));
    out.replaceChildren(
      h("div", { class: "tiles" },
        tile("Yüksek önemli bulgu", q.counts.high ?? 0, "modellemeden önce giderilmeli"),
        tile("Orta önemli bulgu", q.counts.medium ?? 0, "incelenmeli"),
        tile("Bilgi", q.counts.info ?? 0, ""),
        tile("Panel uzunluğu", q.months, `değerlendirme tarihi: ${q.as_of}`)),
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Bulgular"), filters), issueBox),
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Seri incelemesi"), h("label", { class: "field" }, h("span", {}, "Seri"), seriesPick)),
        seriesBox,
        h("p", { class: "small muted" }, "Noktalar robust z ölçütüne göre aykırı gözlemleri, kesikli dikey çizgiler yapısal kırılmaları gösterir (bilinen bir şokla eşleşiyorsa adıyla).")),
      h("div", { class: "card" }, h("h2", {}, "Seri özeti"),
        table([
          { label: "Feature", render: (s) => h("code", {}, s.feature) },
          { label: "İlk", key: "first" },
          { label: "Son", key: "last" },
          { label: "Gözlem", key: "observations", num: true },
          { label: "Eksik oranı", num: true, render: (s) => pct(s.missing_ratio) },
          { label: "Son gözlemden bu yana (ay)", key: "months_since_last", num: true },
          { label: "Aykırı", num: true, render: (s) => s.outliers.length },
          { label: "Kırılma", num: true, render: (s) => s.breaks.length },
          { label: "Durağanlık", render: (s) => STATIONARITY[diag.get(s.feature)?.stationarity] || "—" },
        ], q.series)),
      q.correlated_pairs.length || q.notable_pairs.length
        ? h("div", { class: "card" }, h("h2", {}, "Yüksek korelasyonlu çiftler"),
          table([
            { label: "Feature A", render: (p) => h("code", {}, p.a) },
            { label: "Feature B", render: (p) => h("code", {}, p.b) },
            { label: "Korelasyon", num: true, render: (p) => fmt(p.corr, 3) },
            { label: "Not", render: (p) => p.note || "Aşırı korelasyon: birini çıkarmayı değerlendirin" },
          ], [...q.correlated_pairs, ...q.notable_pairs]))
        : null,
      Object.keys(q.skipped_features).length
        ? h("div", { class: "card" }, h("h2", {}, "Panele girmeyen feature'lar"),
          table([{ label: "Feature", render: (r) => h("code", {}, r[0]) }, { label: "Neden", render: (r) => r[1] }], Object.entries(q.skipped_features)))
        : null,
    );
  };

  run.addEventListener("click", () => busy(run, "Denetleniyor", async () => {
    const q = await postJSON("/api/audit", scope());
    state.results.audit.set(scopeKey(), q);
    render(q);
  }, out));

  root.replaceChildren(
    viewHead(`${m.sector.name}: veri kalitesi`,
      "Güncelliğini yitirmiş ya da durmuş seriler, eksik dönemler, aykırı değerler, yapısal kırılmalar ve aşırı korelasyonlu feature çiftleri denetlenir."),
    h("div", { class: "card" }, h("div", { class: "button-row" }, run)),
    out,
  );
  const cached = state.results.audit.get(scopeKey());
  if (cached) render(cached);
};

views.saglik = async (root) => {
  const run = h("button", { type: "button" }, "Kaynakları denetle");
  const out = h("div");
  run.addEventListener("click", () => busy(run, "Denetleniyor", async () => {
    const rows = await getJSON("/api/health");
    const kind = { ok: ["good", "Çalışıyor"], fail: ["critical", "Hata"], skipped: ["", "Atlandı"] };
    out.replaceChildren(h("div", { class: "card" }, table([
      { label: "Kaynak", render: (r) => h("code", {}, r.source_id) },
      { label: "Durum", render: (r) => status(...(kind[r.status] || ["", r.status])) },
      { label: "Ayrıntı", key: "detail" },
      { label: "Denetlenen seriler", render: (r) => r.checked.join(", ") || "—" },
    ], rows)));
  }));
  root.replaceChildren(
    viewHead("Kaynak durumu", "Her sağlayıcının kritik serilerinden son gözlemin çekilip çekilemediğini denetler. API anahtarı tanımlı değilse ilgili kaynak atlanır; anahtar .env dosyasında EVDS_API_KEY olarak tanımlanır."),
    h("div", { class: "card" }, h("div", { class: "button-row" }, run)),
    out,
  );
};

// --- yönlendirme -----------------------------------------------------------------------------------------

async function route() {
  const name = (location.hash || "#harita").slice(1);
  const view = views[name] ? name : "harita";
  document.querySelectorAll("nav a").forEach((a) => (a.dataset.view === view ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  const root = $("#view");
  $("#tooltip").hidden = true; // grafik değişirken mouseleave gelmez
  root.replaceChildren(h("p", { class: "muted" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Yükleniyor"));
  notice(null);
  try {
    await views[view](root);
  } catch (err) {
    root.replaceChildren();
    notice(err.message, true);
  }
}

function applyTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
}

async function init() {
  const prefs = loadPrefs();
  applyTheme(prefs.theme);
  $("#theme").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    applyTheme(next);
    savePrefs({ theme: next });
  });
  try {
    [state.meta, state.sectors] = await Promise.all([getJSON("/api/meta"), getJSON("/api/sectors")]);
  } catch (err) {
    notice(err.message, true);
    return;
  }
  const sel = $("#sector");
  sel.replaceChildren(...state.sectors.map((s) => h("option", { value: s.id, selected: s.id === prefs.sector }, s.name)));
  $("#start").value = prefs.start || state.meta.default_start.slice(0, 7);
  $("#end").value = prefs.end || "";
  sel.addEventListener("change", () => { savePrefs({ sector: sel.value }); route(); });
  for (const id of ["start", "end"]) $(`#${id}`).addEventListener("change", (e) => { savePrefs({ [id]: e.target.value }); route(); });
  addEventListener("hashchange", route);
  route();
}

init();
