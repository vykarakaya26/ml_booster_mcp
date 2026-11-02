// ML Booster local panel. No external libraries; the DOM is filled via textContent only (never innerHTML).
// Layout rule: summary first (finding sentence + KPI cards), then details; every view ends with a source/period stamp.
import { lineChart, barChart } from "./charts.js";
import { GLOSSARY, MODEL_INFO } from "./glossary.js";

const TOKEN = document.querySelector('meta[name="mlb-token"]').content;
const $ = (sel) => document.querySelector(sel);

// --- DOM helpers --------------------------------------------------------------------------------------

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") el.style.cssText = v; // the CSP blocks inline style attributes; CSSOM is allowed
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === undefined || c === null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

// replaceChildren() turns null into the text "null"; optional sections (todo(), cards built with `cond ? … : null`)
// are dropped here instead.
function fill(el, ...children) {
  el.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
}

// --- formatting (Turkish) ---------------------------------------------------------------------------------

const MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
const nf = (d) => new Intl.NumberFormat("tr-TR", { maximumFractionDigits: d, minimumFractionDigits: 0 });

function num(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  return nf(a >= 1000 ? 0 : a >= 100 ? 1 : digits).format(v);
}
const pctText = (v, d = 1) => (v === null || v === undefined ? "—" : `%${nf(d).format(v)}`);
const ratioPct = (v, d = 1) => (v === null || v === undefined ? "—" : pctText(v * 100, d));
const monthName = (iso) => (iso ? `${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(0, 4)}` : "—");
const dateTR = (iso) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}` : "—");
const stampTime = (iso) => (iso ? `${dateTR(iso.slice(0, 10))} ${iso.slice(11, 16)}` : "—");

// Formats a feature value by its unit: log change ≈ percent, percent series with %.
function valueText(info, v) {
  if (v === null || v === undefined) return "—";
  if (!info) return num(v, 3);
  if (info.transform === "log_diff") return `${v >= 0 ? "+" : "−"}%${nf(1).format(Math.abs(v) * 100)}`;
  if (info.transform === "yoy_pct" || info.unit.startsWith("yüzde")) return pctText(v, 2);
  return num(v, 2);
}
// Formats the change between two periods by unit (percentage points for percent series).
function deltaText(info, a, b) {
  if (a === null || b === null || a === undefined || b === undefined) return null;
  const d = a - b;
  const sign = d > 0 ? "+" : d < 0 ? "−" : "±";
  if (!info) return `${sign}${num(Math.abs(d), 3)}`;
  if (info.transform === "log_diff") return `${sign}${nf(1).format(Math.abs(d) * 100)} puan`;
  if (info.transform === "yoy_pct" || info.unit.startsWith("yüzde")) return `${sign}${nf(2).format(Math.abs(d))} puan`;
  return `${sign}${num(Math.abs(d), 2)}`;
}

// --- API ---------------------------------------------------------------------------------------------------

async function api(path, body) {
  const opts = { headers: { "X-MLB-Token": TOKEN } };
  if (body !== undefined) {
    opts.method = "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = `İstek başarısız oldu (HTTP ${res.status}).`;
    try { msg = (await res.json()).error || msg; } catch { /* gövde JSON değil */ }
    throw new Error(msg);
  }
  return res;
}
const getJSON = async (path) => (await api(path)).json();
const postJSON = async (path, body) => (await api(path, body)).json();

function notice(text) {
  const el = $("#notice");
  el.hidden = !text;
  el.textContent = text ? `Hata: ${text}` : "";
}

const WAIT_TEXT = "Veriler hazırlanıyor. Önbellekte olmayan seriler kaynaktan çekildiği için ilk çalıştırma birkaç dakika sürebilir.";

async function busy(button, label, fn, out = null) {
  const original = button.textContent;
  button.disabled = true;
  button.replaceChildren(h("span", { class: "spinner", "aria-hidden": "true" }), label);
  if (out) fill(out, h("p", { class: "empty" }, h("span", { class: "spinner", "aria-hidden": "true" }), WAIT_TEXT));
  notice(null);
  try {
    return await fn();
  } catch (err) {
    if (out) fill(out);
    notice(err.message);
    return null;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

// --- components --------------------------------------------------------------------------------------------

function table(columns, rows, { rowClass, scroll = false, onRow } = {}) {
  return h("div", { class: scroll ? "table-wrap scroll" : "table-wrap" },
    h("table", {},
      h("thead", {}, h("tr", {}, columns.map((c) => h("th", { class: c.num ? "num" : null, scope: "col" }, c.label)))),
      h("tbody", {}, rows.map((r) => {
        const tr = h("tr", { class: [rowClass ? rowClass(r) : "", onRow ? "clickable" : ""].join(" ").trim() || null },
          columns.map((c) => h("td", { class: c.num ? "num" : null }, c.render ? c.render(r) : r[c.key])));
        if (onRow) {
          tr.tabIndex = 0;
          tr.addEventListener("click", () => onRow(r, tr));
          tr.addEventListener("keydown", (e) => { if (e.key === "Enter") onRow(r, tr); });
        }
        return tr;
      }))));
}

const tile = (label, value, sub, tag) => h("div", { class: "tile" },
  h("div", { class: "label" }, h("span", {}, label), tag ? h("span", { class: "tag", title: TAGS[tag] }, tag) : null),
  h("div", { class: String(value).length > 12 ? "value text" : "value" }, value),
  sub ? h("div", { class: "sub" }, sub) : null);
const TAGS = { "Ölçüm": "Kaynaktan doğrudan gelen değer", "Hesap": "Panelin hesapladığı değer" };

const STATUS = {
  ok: ["ok", "Hazır"], warn: ["warn", "Takip"], crit: ["crit", "Aksiyon"], none: ["none", "Veri yok"],
};
const status = (kind, label) => h("span", { class: `status ${kind}` }, label || STATUS[kind][1]);

function term(key, label) {
  const g = GLOSSARY[key];
  return h("span", { class: "term" }, label ?? g?.term ?? key,
    g ? h("button", { type: "button", class: "help", "aria-label": `${g.term}: açıklama`, title: g.text, onclick: (e) => { e.preventDefault(); openGlossary(key); } }, "?") : null);
}

function viewHead(eyebrow, title, text) {
  return h("div", { class: "view-head" }, eyebrow ? h("span", { class: "eyebrow" }, eyebrow) : null, h("h1", {}, title), text ? h("p", {}, text) : null);
}

function summary(...sentences) {
  return h("section", { class: "summary", "aria-label": "Kısaca" }, h("h2", {}, "Kısaca"), sentences.filter(Boolean).map((s) => h("p", {}, ...[].concat(s))));
}

function stampBar({ sources, generated, period, missing, note }) {
  const item = (k, v) => (v ? h("span", {}, h("b", {}, `${k}: `), v) : null);
  return h("footer", { class: "stamp" },
    item("Kaynaklar", sources), item("Veri çekim", generated), item("Kapsanan dönem", period), item("Eksik veri", missing),
    item("Durum", note || "Taslak: otomatik üretildi, karar öncesinde uzman incelemesi gerekir"));
}

function todo(title, items) {
  if (!items.length) return null;
  return h("section", { class: "todo" }, h("h2", {}, title), h("ul", {}, items.map((i) => h("li", {}, ...[].concat(i)))));
}

function emptyState(title, text) {
  return h("div", { class: "empty" }, h("strong", {}, title), text);
}

function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// --- glossary drawer ----------------------------------------------------------------------------------------

function renderGlossary(filter = "") {
  const q = filter.toLocaleLowerCase("tr");
  const entries = [
    ...Object.entries(GLOSSARY).map(([k, g]) => [k, g.term, g.text]),
    ...Object.entries(MODEL_INFO).map(([k, [name, text]]) => [`model:${k}`, `Model: ${name}`, text]),
  ].filter(([, t, x]) => !q || t.toLocaleLowerCase("tr").includes(q) || x.toLocaleLowerCase("tr").includes(q));
  $("#glossary-list").replaceChildren(...entries.map(([k, t, x]) => h("div", { id: `g-${k.replace(":", "-")}` }, h("dt", {}, t), h("dd", {}, x))));
}

function openGlossary(key) {
  const drawer = $("#glossary");
  drawer.hidden = false;
  $("#glossary-search").value = "";
  renderGlossary();
  if (key) {
    const el = document.getElementById(`g-${key}`);
    if (el) { el.classList.add("hit-term"); el.scrollIntoView({ block: "center" }); }
  } else $("#glossary-search").focus();
}

// --- durum ------------------------------------------------------------------------------------------------

const state = { meta: null, sectors: [], maps: new Map(), panels: new Map(), backtests: new Map(), audits: new Map(), share: null };

const PREFS_KEY = "mlbooster-panel";
function loadPrefs() { try { return JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; } catch { return {}; } }
function savePrefs(patch) { try { localStorage.setItem(PREFS_KEY, JSON.stringify({ ...loadPrefs(), ...patch })); } catch { /* özel pencere */ } }

function scope() {
  const start = $("#start").value;
  const end = $("#end").value;
  return { sector: $("#sector").value, start: start ? `${start}-01` : state.meta.default_start, end: end ? `${end}-28` : undefined };
}
const scopeKey = (extra = {}) => JSON.stringify({ ...scope(), ...extra });
const sectorName = () => state.sectors.find((s) => s.id === $("#sector").value)?.name || $("#sector").value;

async function sectorMap(sector) {
  if (!state.maps.has(sector)) state.maps.set(sector, await getJSON(`/api/sectors/${encodeURIComponent(sector)}/map`));
  return state.maps.get(sector);
}
const labelOf = (m, name) => m?.feature_info?.[name]?.label || name;
const sourcesOf = (m) => [...new Set(Object.values(m.feature_info).map((f) => f.source_owner || f.source_name))].filter(Boolean).join(", ");

function featureTitle(m, name) {
  return h("span", {}, labelOf(m, name), h("span", { class: "code" }, name));
}

const FEATURE_STATUS = { ready: "ok", pending_discovery: "warn", no_provider: "none" };
const FEATURE_STATUS_LABEL = { ready: "Hazır", pending_discovery: "Seri kodu bekliyor", no_provider: "Kaynak bağlantısı yok" };

// What the user needs to do for a skipped feature.
function skippedAdvice(m, name, reason) {
  const label = labelOf(m, name);
  if (/keşif bekliyor|seri kodu bekleniyor/.test(reason)) return [h("strong", {}, label), ": EVDS seri kodu henüz bağlanmadı. Claude Code'da ", h("em", {}, `"${label.toLocaleLowerCase("tr")} serisini bul"`), " deyin ya da ", h("code", { class: "pill" }, "mlbooster catalog search"), " ile arayıp ", h("code", { class: "pill" }, "catalog set-feature"), " ile bağlayın."];
  if (/sağlayıcısı henüz yok|henüz provider yok/.test(reason)) return [h("strong", {}, label), ": bu kaynaktan otomatik veri çekme henüz geliştirilmedi (yol haritasında)."];
  if (/Credentials|anahtar/i.test(reason)) return [h("strong", {}, label), ": API anahtarı tanımlı değil. EVDS anahtarını .env dosyasına EVDS_API_KEY olarak ekleyin."];
  return [h("strong", {}, label), `: ${reason}`];
}

// --- views -------------------------------------------------------------------------------------------

const views = {};

views.genel = async (root) => {
  const go = (id, view = "harita") => { $("#sector").value = id; savePrefs({ sector: id }); location.hash = `#${view}`; };
  const total = state.sectors.reduce((a, s) => a + s.ready, 0);
  fill(root,
    h("section", { class: "hero" },
      h("span", { class: "eyebrow" }, "ML Booster Map"),
      h("h1", {}, "Türkiye'de bir sektör seçin; makine öğrenmesine hazır veri setini kaynağıyla birlikte oluşturun"),
      h("p", {}, "Panel; TCMB EVDS, BIS ve OECD gibi ücretsiz kamu kaynaklarından sektörle ilgili makro feature'ları çeker, aynı aylık takvimde hizalar, verinin kalitesini denetler ve bir ML modelinin geçmesi gereken tahmin çıtasını hesaplar. Tüm işlem bu bilgisayarda yapılır."),
      h("div", { class: "button-row" },
        h("button", { type: "button", onclick: () => go($("#sector").value) }, `${sectorName()} ile başla`),
        h("button", { type: "button", class: "ghost", onclick: () => openGlossary() }, "Terimler sözlüğü"))),
    h("div", { class: "steps" },
      [["1", "Sektör ve kaynaklar", "Sektör için hangi ücretsiz verilerin olduğunu ve hangilerinin hazır olduğunu görün.", "Çıktı: veri kaynağı haritası", "harita"],
        ["2", "Feature paneli", "Feature'lar aylık takvimde, yayımlandıkları aya göre hizalanır.", "Çıktı: CSV / Parquet tablo", "panel"],
        ["3", "Veri kalitesi", "Durmuş seriler, eksik aylar, outlier'lar ve kırılmalar denetlenir.", "Çıktı: önceliklendirilmiş bulgu listesi", "kalite"],
        ["4", "Tahmin çıtası", "Basit yöntemler geçmişte sınanır; ML modelinin aşması gereken hata düzeyi belirlenir.", "Çıktı: model karşılaştırması", "model"]]
        .map(([n, t, p, o, v]) => h("a", { class: "step", href: `#${v}` }, h("span", { class: "num" }, n), h("h3", {}, t), h("p", {}, p), h("span", { class: "out" }, o)))),
    h("div", { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Sektörler"), h("p", {}, `${state.sectors.length} sektörde toplam ${total} feature hazır. Bir sektör seçerek başlayın.`))),
      h("div", { class: "sector-grid" }, state.sectors.map((s) => {
        const bar = h("i");
        bar.style.width = `${s.features ? (s.ready / s.features) * 100 : 0}%`;
        return h("button", { type: "button", class: "sector-card", onclick: () => go(s.id) },
          h("h3", {}, s.name),
          s.ml_use_cases.length ? h("div", { class: "chips" }, s.ml_use_cases.slice(0, 3).map((u) => h("span", { class: "chip" }, u))) : null,
          h("div", { class: "bar", "aria-hidden": "true" }, bar),
          h("small", {}, `${s.ready} / ${s.features} feature hazır · ${s.sources} kaynak`));
      }))),
    h("div", { class: "trust" },
      h("div", {}, h("strong", {}, "Geleceği görmeyen veri"), h("p", {}, "Her değer, gerçekte yayımlandığı aya yazılır. Model, o tarihte bilinmeyen bilgiyle eğitilmez.")),
      h("div", {}, h("strong", {}, "Kaynağı belli"), h("p", {}, "Her feature kurum, seri kodu ve yayın gecikmesiyle birlikte gösterilir; lisans notları haritada yer alır.")),
      h("div", {}, h("strong", {}, "Yerel ve açık kaynak"), h("p", {}, "Veriler bu bilgisayarda işlenir, API anahtarı dışarı gönderilmez. Kod GitHub'da açıktır."))),
  );
};

views.harita = async (root) => {
  const m = await sectorMap($("#sector").value);
  const info = m.feature_info;
  const groups = { ready: [], pending_discovery: [], no_provider: [] };
  m.macro_features.forEach((f) => (groups[f.status] || (groups[f.status] = [])).push(f));
  const ready = groups.ready.length;
  const sourceNames = [...new Set(groups.ready.map((f) => info[f.name]?.source_owner).filter(Boolean))];

  const featureRow = (f) => {
    const i = info[f.name];
    return h("div", { class: "feature-row" },
      h("div", {}, h("h3", {}, i.label), h("span", { class: "code" }, `${f.name}${f.series ? ` · ${f.series}` : ""}`)),
      status(FEATURE_STATUS[f.status], FEATURE_STATUS_LABEL[f.status]),
      h("div", { class: "chips", style: "grid-column:1/-1" },
        h("span", { class: "chip accent" }, i.transform_label),
        h("span", { class: "chip" }, `Birim: ${i.unit}`),
        i.frequency ? h("span", { class: "chip" }, `Kaynak frekansı: ${i.frequency}`) : null,
        h("span", { class: "chip" }, i.source_name),
        i.publication_lag_days ? h("span", { class: "chip" }, `Yayın gecikmesi: ${i.publication_lag_days} gün`) : null),
      h("p", {}, f.status === "pending_discovery" ? `Seri kodu bekleniyor. İpucu: ${f.discover_hint}` : i.description));
  };

  fill(root,
    viewHead("Adım 1", `${m.sector.name}: hangi veriler var?`, "Sektör için tanımlı ücretsiz kaynaklar ve modelde kullanılacak feature'lar. Hazır feature'lar bir sonraki adımda panele dönüştürülür."),
    summary(
      [h("strong", {}, `${m.sector.name} için ${m.macro_features.length} feature tanımlı; ${ready} tanesi kullanıma hazır.`),
        sourceNames.length ? ` Hazır feature'lar ${sourceNames.join(", ")} kaynaklı.` : ""],
      groups.pending_discovery.length ? `${groups.pending_discovery.length} feature'ın EVDS seri kodu henüz bağlanmadı; aşağıdaki "Sizden gerekenler" bölümü nasıl tamamlanacağını açıklar.` : "Seri kodu eksik feature yok.",
      m.market_share.length ? `Pazar payı için ${m.market_share.length} kaynak tanımlı (${m.market_share.map((s) => s.source.toUpperCase()).join(", ")}); bu kaynaklardan otomatik veri çekme yol haritasında.` : null),
    h("div", { class: "tiles" },
      tile("Hazır feature", ready, `toplam ${m.macro_features.length} feature'dan`, "Ölçüm"),
      tile("Seri kodu bekleyen", groups.pending_discovery.length, groups.pending_discovery.length ? "EVDS'de aranıp bağlanmalı" : "eksik yok"),
      tile("Veri kaynağı", m.sources.length, `${m.sources.filter((s) => s.provider_ready).length} kaynaktan otomatik çekim yapılıyor`),
      tile("ML kullanım alanı", m.sector.ml_use_cases.length, m.sector.ml_use_cases.join(", ") || "tanımlı değil")),
    todo("Sizden gerekenler", [...groups.pending_discovery, ...groups.no_provider].map((f) =>
      skippedAdvice(m, f.name, f.status === "pending_discovery" ? "keşif bekliyor" : "sağlayıcısı henüz yok"))),
    h("section", { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Feature'lar"), h("p", {}, "Her feature'ın ne olduğu, hangi dönüşümle panele girdiği ve kaynağı. Mavi etiket, modelde kullanılan biçimi gösterir."))),
      h("div", { class: "feature-list" }, [...groups.ready, ...groups.pending_discovery, ...groups.no_provider].map(featureRow))),
    h("section", { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Kaynaklar"), h("p", {}, "Rol: makro feature, sektör verisi ya da pazar payı. Doğrulama: kaynağın canlı olarak test edilip edilmediği."))),
      table([
        { label: "Kurum", render: (s) => h("span", {}, s.name, h("span", { class: "code" }, s.owner)) },
        { label: "Rol", render: (s) => s.roles.join(", ") },
        { label: "Erişim", key: "access" },
        { label: "Frekans", render: (s) => s.frequency.join(", ") },
        { label: "Yayın gecikmesi (gün)", key: "publication_lag_days", num: true },
        { label: "Otomatik çekim", render: (s) => (s.provider_ready ? status("ok", "Var") : status("none", "Henüz yok")) },
      ], m.sources),
      h("details", { class: "about" }, h("summary", {}, "Lisans ve kullanım koşulları"),
        h("div", {}, m.sources.map((s) => h("p", {}, h("strong", {}, `${s.name}: `), s.license))))),
    stampBar({ sources: m.sources.map((s) => s.owner).join(", "), note: "Katalog bilgisi; veri çekilmedi" }),
  );
};

views.panel = async (root) => {
  const sector = $("#sector").value;
  const m = await sectorMap(sector);
  const info = m.feature_info;
  const prefs = loadPrefs();
  const freq = h("select", { "aria-label": "Panel frekansı" },
    [["M", "Aylık"], ["W", "Haftalık (Cuma)"], ["B", "İş günü"]].map(([v, l]) => h("option", { value: v, selected: (prefs.freq || "M") === v }, l)));
  const out = h("div", { class: "stack" });
  const run = h("button", { type: "button" }, "Paneli oluştur");
  const csv = h("button", { type: "button", class: "ghost" }, "CSV indir");
  const parquet = h("button", { type: "button", class: "ghost" }, "Parquet indir");
  const exportAs = (format, btn) => busy(btn, "Hazırlanıyor", async () => {
    const res = await api("/api/panel/export", { ...scope(), freq: freq.value, format });
    downloadBlob(await res.blob(), `${sector}_${freq.value.toLowerCase()}_panel.${format}`);
  });
  csv.addEventListener("click", () => exportAs("csv", csv));
  parquet.addEventListener("click", () => exportAs("parquet", parquet));

  const render = (p) => {
    const last = (f) => {
      const vals = p.columns[f];
      for (let i = vals.length - 1; i >= 0; i--) if (vals[i] !== null) return [i, vals[i]];
      return [null, null];
    };
    const selected = new Set(loadPrefs()[`features:${sector}`] || p.features);
    const grid = h("div", { class: "series-grid" });
    const card = (f) => {
      const i = info[f];
      const [idx, v] = last(f);
      const prev = idx !== null ? p.columns[f].slice(0, idx).reverse().find((x) => x !== null) ?? null : null;
      const d = deltaText(i, v, prev);
      return h("article", { class: "series" },
        h("div", { class: "meta" }, h("h3", {}, i?.label || f), i ? h("span", { class: "chip accent" }, i.transform_label) : null),
        h("p", { class: "headline" }, h("strong", {}, valueText(i, v)), idx !== null ? `${monthName(p.dates[idx])}` : "", d ? ` · önceki döneme göre ${d}` : ""),
        lineChart({ dates: p.dates, series: [{ name: i?.label || f, values: p.columns[f], color: "var(--series-1)" }], height: 140, compact: true, format: (x) => valueText(i, x) }),
        h("details", { class: "about" }, h("summary", {}, "Bu veri hakkında"),
          h("div", {}, h("p", {}, i?.description || ""),
            h("p", {}, h("strong", {}, "Kaynak: "), `${i?.source_name || ""}${i?.series ? ` · seri kodu ${i.series}` : ""}`),
            h("p", {}, h("strong", {}, "Birim: "), i?.unit || "—", h("span", { class: "code" }, `Panel sütunu: ${f}`)))));
    };
    const draw = () => {
      const shown = p.features.filter((f) => selected.has(f));
      grid.replaceChildren(...(shown.length ? shown.map(card) : [emptyState("Feature seçilmedi", "Yukarıdan en az bir feature seçin.")]));
    };
    const picks = h("div", { class: "check-grid" }, p.features.map((f) =>
      h("label", { class: "check" },
        h("input", { type: "checkbox", checked: selected.has(f), onchange: (e) => {
          e.target.checked ? selected.add(f) : selected.delete(f);
          savePrefs({ [`features:${sector}`]: [...selected] });
          draw();
        } }),
        h("span", {}, labelOf(m, f), h("small", {}, info[f]?.transform_label || f)))));
    draw();
    const skipped = Object.entries(p.skipped);
    const rows = p.dates.slice(-12).map((d, k, arr) => ({ date: d, idx: p.dates.length - arr.length + k }));
    const period = p.dates.length ? `${monthName(p.dates[0])} – ${monthName(p.dates.at(-1))}` : "—";
    fill(out,
      summary(
        [h("strong", {}, `Panel ${period} dönemini kapsıyor: ${p.rows} dönem, ${p.features.length} feature.`), " Her değer, kaynağın yayımladığı döneme yazıldı; tablo doğrudan model eğitiminde kullanılabilir."],
        skipped.length ? `${skipped.length} feature panele eklenemedi; nedenleri ve yapılması gerekenler aşağıda.` : "Tanımlı tüm hazır feature'lar panele eklendi."),
      h("div", { class: "tiles" },
        tile("Kapsanan dönem", p.rows, period, "Hesap"),
        tile("Feature'lar", p.features.length, `${m.macro_features.length} tanımlı feature'dan`, "Ölçüm"),
        tile("Panele girmeyen", skipped.length, skipped.length ? "aşağıda açıklandı" : "yok"),
        tile("Frekans", { M: "Aylık", W: "Haftalık", B: "İş günü" }[p.freq], "dönem sonu takvimi")),
      todo("Sizden gerekenler", skipped.map(([f, r]) => skippedAdvice(m, f, r))),
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Gösterilecek feature'lar"), h("p", {}, "Her feature kendi ölçeğinde ayrı grafikte yer alır. Seçiminiz bu tarayıcıda hatırlanır."))),
        picks),
      grid,
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Tablo görünümü: son 12 dönem"), h("p", {}, "Modelin göreceği tablo. Tamamı için CSV ya da Parquet indirin."))),
        table([{ label: "Dönem", render: (r) => monthName(r.date) },
          ...p.features.map((f) => ({ label: h("span", {}, labelOf(m, f), h("span", { class: "code" }, f)), num: true, render: (r) => valueText(info[f], p.columns[f][r.idx]) }))], rows)),
      stampBar({ sources: sourcesOf(m), generated: stampTime(p.generated_at), period, missing: skipped.length ? `${skipped.length} feature panele girmedi` : "yok" }),
    );
  };

  run.addEventListener("click", () => busy(run, "Oluşturuluyor", async () => {
    savePrefs({ freq: freq.value });
    const p = await postJSON("/api/panel", { ...scope(), freq: freq.value });
    state.panels.set(scopeKey({ freq: freq.value }), p);
    render(p);
  }, out));

  fill(root,
    viewHead("Adım 2", `${m.sector.name}: feature paneli`, "Hazır feature'lar aynı takvimde hizalanır. Günlük ve haftalık veriler aylığa indirgenir, çeyreklik veriler sonraki aylara taşınır."),
    h("section", { class: "card" },
      h("div", { class: "controls" },
        h("label", { class: "field" }, h("span", {}, "Frekans"), freq),
        h("div", { class: "button-row" }, run, csv, parquet)),
      h("p", { class: "caption" }, term("point_in_time", "Point-in-time hizalama"), ": Mart enflasyonu Nisan başında açıklandığı için panelde Nisan satırında yer alır. Böylece model geçmişte henüz bilinmeyen veriyi kullanamaz.")),
    out,
  );
  const cached = state.panels.get(scopeKey({ freq: freq.value }));
  if (cached) render(cached);
  else out.append(emptyState("Panel henüz oluşturulmadı", "\"Paneli oluştur\" düğmesine basın. İlk çalıştırmada veriler kaynaktan çekilir, sonrakiler yerel önbellekten okunur."));
};

const CHECKS = {
  stale: ["Stale seri", "stale", "Kaynakta serinin güncel kodunu arayın; seri arşive taşınmış ya da kodu değişmiş olabilir."],
  missing: ["Eksik dönem", "missing", "Başlangıç tarihini ileri alın ya da eksik dönemlerin nedenini (yayın gecikmesi, seri başlangıcı) kontrol edin."],
  outliers: ["Outlier", "outlier", "Bilinen bir şokla açıklanıyorsa koruyun ve bir dummy feature ekleyin; açıklanamıyorsa kaynakla karşılaştırın."],
  structural_break: ["Structural break", "structural_break", "Kırılma sonrası dönemi ayrı değerlendirin ya da modele rejim dummy'si ekleyin."],
  collinearity: ["Aşırı korelasyon", "collinearity", "İki feature'dan birini çıkarmayı ya da yalnızca birini kullanmayı değerlendirin."],
  constant: ["Sabit seri", null, "Seri kodunu kontrol edin; sabit seri modele bilgi taşımaz."],
  empty: ["Boş seri", null, "Seri kodunu ve tarih aralığını kontrol edin."],
  heavy_tails: ["Kalın kuyruk", null, "Şok dönemleri belirgin; medyan tabanlı ölçüler ya da kırpma (winsorize) düşünülebilir."],
  nonstationary: ["Durağan değil", "stationarity", "Modelde düzey yerine fark ya da yıllık değişim kullanmayı değerlendirin."],
  managed_path: ["Kontrollü patika", null, "Seri istikrarlı ve yönetilen bir seyir izliyor; geçmiş oynaklık ani kırılma riskini olduğundan düşük gösterebilir."],
};
const SEVERITY = { high: ["crit", "Kritik"], medium: ["warn", "Uyarı"], info: ["none", "Bilgi"] };
const STATIONARITY = { "durağan": "Durağan", "birim_kök": "Durağan değil", "kırılma_şüphesi": "Kırılma şüphesi", "kararsız": "Belirsiz" };

views.kalite = async (root) => {
  const sector = $("#sector").value;
  const m = await sectorMap(sector);
  const info = m.feature_info;
  const run = h("button", { type: "button" }, "Denetimi başlat");
  const out = h("div", { class: "stack" });

  const render = (q) => {
    const byFeature = new Map(q.series.map((s) => [s.feature, []]));
    q.issues.forEach((i) => i.feature.split(" ~ ").forEach((f) => byFeature.get(f)?.push(i)));
    const worst = (list) => (list.some((i) => i.severity === "high") ? "crit" : list.some((i) => i.severity === "medium") ? "warn" : "ok");
    const diag = new Map((q.diagnostics || []).map((d) => [d.feature, d]));
    const checkCounts = {};
    q.issues.filter((i) => i.severity !== "info").forEach((i) => (checkCounts[i.check] = (checkCounts[i.check] || 0) + 1));
    const top = Object.entries(checkCounts).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([c, n]) => `${n} ${CHECKS[c]?.[0].toLocaleLowerCase("tr") || c}`);
    const clean = q.series.filter((s) => worst(byFeature.get(s.feature) || []) === "ok").length;

    const detail = h("div", { class: "stack" });
    const showFeature = (f) => {
      const s = q.series.find((x) => x.feature === f);
      const i = info[f];
      const list = (byFeature.get(f) || []);
      fill(detail,
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, i?.label || f), h("span", { class: "code" }, f)), status(worst(list), { crit: "Aksiyon", warn: "Takip", ok: "Sorun yok" }[worst(list)])),
        q.columns[f] ? lineChart({
          dates: q.dates, series: [{ name: i?.label || f, values: q.columns[f], color: "var(--series-1)" }],
          markers: { name: "Outlier", dates: s.outliers, color: "var(--series-2)" },
          events: s.breaks.map((b) => ({ date: b.date, label: b.event || "Structural break" })), height: 240, format: (x) => valueText(i, x),
        }) : null,
        list.length
          ? h("div", { class: "feature-list" }, list.map((it) => h("div", { class: "feature-row" },
            h("h3", {}, CHECKS[it.check]?.[0] || it.check), status(...SEVERITY[it.severity]),
            h("p", {}, it.message), h("p", {}, h("strong", {}, "Ne yapmalı? "), CHECKS[it.check]?.[2] || "İnceleyin."))))
          : h("p", { class: "muted" }, "Bu feature'da bulgu yok."),
        h("p", { class: "caption" }, "Turuncu noktalar outlier'ları, kesikli dikey çizgiler structural break'leri gösterir. Bilinen bir şokla eşleşen kırılmalar adıyla yazılır."));
    };

    let filter = "all";
    const issueBox = h("div");
    const drawIssues = () => {
      const rows = q.issues.filter((i) => filter === "all" || i.severity === filter);
      issueBox.replaceChildren(rows.length ? table([
        { label: "Önem", render: (i) => status(...SEVERITY[i.severity]) },
        { label: "Feature", render: (i) => h("span", {}, i.feature.split(" ~ ").map((f) => labelOf(m, f)).join(" ve "), h("span", { class: "code" }, i.feature)) },
        { label: "Denetim", render: (i) => CHECKS[i.check]?.[0] || i.check },
        { label: "Bulgu", key: "message" },
        { label: "Ne yapmalı?", render: (i) => CHECKS[i.check]?.[2] || "İnceleyin." },
      ], rows, { scroll: true }) : h("p", { class: "muted" }, "Bu filtrede bulgu yok."));
    };
    const filters = h("div", { class: "button-row" }, [["all", "Tümü"], ["high", "Kritik"], ["medium", "Uyarı"], ["info", "Bilgi"]].map(([v, l]) =>
      h("button", { type: "button", class: `ghost small${v === "all" ? " active" : ""}`, onclick: (e) => {
        filter = v;
        filters.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
        e.currentTarget.classList.add("active");
        drawIssues();
      } }, l)));
    drawIssues();

    const featureTable = table([
      { label: "Feature", render: (s) => featureTitle(m, s.feature) },
      { label: "Durum", render: (s) => { const w = worst(byFeature.get(s.feature) || []); return status(w, { crit: "Aksiyon", warn: "Takip", ok: "Sorun yok" }[w]); } },
      { label: "Son gözlem", render: (s) => monthName(s.last) },
      { label: "Eksik", num: true, render: (s) => ratioPct(s.missing_ratio) },
      { label: "Aykırı", num: true, render: (s) => s.outliers.length },
      { label: "Kırılma", num: true, render: (s) => s.breaks.length },
      { label: term("stationarity", "Durağanlık"), render: (s) => STATIONARITY[diag.get(s.feature)?.stationarity] || "—" },
    ], q.series, { scroll: true, onRow: (s, tr) => {
      tr.parentElement.querySelectorAll("tr").forEach((x) => x.classList.remove("selected"));
      tr.classList.add("selected");
      showFeature(s.feature);
    } });
    const firstBad = q.series.find((s) => worst(byFeature.get(s.feature) || []) !== "ok") || q.series[0];
    if (firstBad) {
      showFeature(firstBad.feature);
      featureTable.querySelectorAll("tbody tr")[q.series.indexOf(firstBad)]?.classList.add("selected");
    }

    const pairs = [...q.correlated_pairs, ...q.notable_pairs];
    fill(out,
      summary(
        [h("strong", {}, `Denetim ${q.series.length} feature'da ${q.issues.length} bulgu üretti: ${q.counts.high ?? 0} kritik, ${q.counts.medium ?? 0} uyarı, ${q.counts.info ?? 0} bilgi.`)],
        q.counts.high ? "Kritik bulgular modellemeden önce giderilmelidir." : "Modellemeyi engelleyen kritik bulgu yok.",
        top.length ? `Uyarıların çoğu ${top.join(" ve ")} kaynaklı; Türkiye'de 2018 ve 2021 kur şokları ile 2023 sonrası sıkılaştırma bu tür bulguların sık görülen nedenleri arasındadır; eşleşmeyi aşağıdaki grafiklerden kontrol edin. ${clean} feature'da sorun bulunmadı.` : `${clean} feature'da sorun bulunmadı.`),
      h("div", { class: "tiles" },
        tile("Kritik", q.counts.high ?? 0, "modellemeden önce giderilmeli"),
        tile("Uyarı", q.counts.medium ?? 0, "incelenmeli"),
        tile("Bilgi", q.counts.info ?? 0, "modelleme tercihlerini etkiler"),
        tile("Sorunsuz feature", `${clean} / ${q.series.length}`, `değerlendirme tarihi ${dateTR(q.as_of)}`, "Hesap")),
      h("div", { class: "split" },
        h("section", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Feature bazında durum"), h("p", {}, "Ayrıntı için bir satıra tıklayın."))), featureTable),
        h("section", { class: "card" }, detail)),
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Tüm bulgular"), h("p", {}, "Önem derecesine göre sıralıdır."))), filters, issueBox),
      pairs.length ? h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, term("collinearity", "Birbirine çok benzeyen feature'lar")), h("p", {}, "Korelasyonu yüksek çiftler. Aynı bilgiyi taşıyorlarsa modelde birini kullanmak yeterlidir."))),
        table([
          { label: "Feature A", render: (p) => featureTitle(m, p.a) },
          { label: "Feature B", render: (p) => featureTitle(m, p.b) },
          { label: "Korelasyon", num: true, render: (p) => num(p.corr, 2) },
          { label: "Not", render: (p) => p.note || "Birini çıkarmayı değerlendirin." },
        ], pairs)) : null,
      todo("Panele girmeyen feature'lar", Object.entries(q.skipped_features).map(([f, r]) => skippedAdvice(m, f, r))),
      stampBar({ sources: sourcesOf(m), generated: stampTime(q.generated_at), period: q.dates.length ? `${monthName(q.dates[0])} – ${monthName(q.dates.at(-1))}` : "—", missing: Object.keys(q.skipped_features).length ? `${Object.keys(q.skipped_features).length} feature denetime girmedi` : "yok" }),
    );
  };

  run.addEventListener("click", () => busy(run, "Denetleniyor", async () => {
    const q = await postJSON("/api/audit", scope());
    state.audits.set(scopeKey(), q);
    render(q);
  }, out));

  fill(root,
    viewHead("Adım 3", `${m.sector.name}: veri kalitesi`, "Feature'lar modellemeden önce denetlenir: durmuş seriler, eksik dönemler, outlier'lar, structural break'ler ve birbirinin kopyası olan feature'lar."),
    h("section", { class: "card" }, h("div", { class: "button-row" }, run,
      h("span", { class: "caption" }, "Bulgular üç düzeydedir: Kritik (modellemeden önce giderilmeli), Uyarı (incelenmeli), Bilgi (model tercihlerini etkiler)."))),
    out,
  );
  const cached = state.audits.get(scopeKey());
  if (cached) render(cached);
  else out.append(emptyState("Denetim henüz çalıştırılmadı", "\"Denetimi başlat\" düğmesine basın. Panel gerekirse otomatik oluşturulur."));
};

views.model = async (root) => {
  const sector = $("#sector").value;
  const m = await sectorMap(sector);
  const info = m.feature_info;
  const prefs = loadPrefs();
  const features = m.macro_features.filter((f) => f.status === "ready").map((f) => f.name);
  if (!features.length) {
    fill(root, viewHead("Adım 4", "Tahmin çıtası"), emptyState("Bu sektörde hazır feature yok", "Önce 1. adımda eksik seri kodlarını tamamlayın."));
    return;
  }
  const option = (f, sel) => h("option", { value: f, selected: f === sel }, `${labelOf(m, f)} · ${info[f]?.transform_label || ""}`);
  const target = h("select", { "aria-label": "Tahmin edilecek feature" }, features.map((f) => option(f, prefs[`target:${sector}`])));
  const anchor = h("select", { "aria-label": "Çapa" }, h("option", { value: "" }, "Kullanma"), features.map((f) => option(f, prefs[`anchor:${sector}`])));
  const horizon = h("input", { type: "number", min: 1, max: 24, value: prefs.horizon || 1, style: "width:90px" });
  const minTrain = h("input", { type: "number", min: 12, max: 240, value: prefs.min_train || 48, style: "width:100px" });
  const exog = h("input", { type: "checkbox", checked: prefs.exogenous !== false });
  const chosen = new Set(prefs.models || state.meta.models);
  const modelChecks = h("div", { class: "check-grid" }, state.meta.models.map((mm) =>
    h("label", { class: "check" },
      h("input", { type: "checkbox", checked: chosen.has(mm), onchange: (e) => (e.target.checked ? chosen.add(mm) : chosen.delete(mm)) }),
      h("span", {}, MODEL_INFO[mm]?.[0] || mm, h("small", {}, MODEL_INFO[mm]?.[1] || "")))));
  const run = h("button", { type: "button" }, "Sınamayı başlat");
  const out = h("div", { class: "stack" });
  const mname = (mm) => MODEL_INFO[mm]?.[0] || mm;

  const render = (r) => {
    const ti = info[r.config.target];
    const unitWord = ti?.transform === "log_diff" ? "puan (log değişim × 100)" : ti?.unit?.startsWith("yüzde") || ti?.transform === "yoy_pct" ? "puan" : ti?.unit || "";
    const errText = (v) => (ti?.transform === "log_diff" ? num(v * 100, 2) : num(v, 3));
    const metrics = [...r.metrics].sort((a, b) => (a.mae ?? Infinity) - (b.mae ?? Infinity));
    const best = metrics.find((x) => x.model === r.best_model) || metrics[0];
    const naive = metrics.find((x) => x.model === "naive");
    const gain = best && naive && naive.mae ? 1 - best.mae / naive.mae : null;
    const pick = h("select", { "aria-label": "Grafikteki model" }, metrics.map((x) => h("option", { value: x.model, selected: x.model === r.best_model }, mname(x.model))));
    const compare = h("select", { "aria-label": "Karşılaştırma modeli" }, h("option", { value: "" }, "Yok"), metrics.map((x) => h("option", { value: x.model, selected: x.model === "naive" && x.model !== r.best_model }, mname(x.model))));
    const chartBox = h("div");
    const draw = () => {
      const byModel = (mm) => new Map(r.predictions.filter((p) => p.model === mm).map((p) => [p.date, p]));
      const main = byModel(pick.value);
      const firstPred = r.predictions.length ? r.predictions.map((p) => p.date).sort()[0] : null;
      const cut = firstPred ? Math.max(0, r.history.dates.findIndex((d) => d >= firstPred) - 36) : 0;
      const dates = r.history.dates.slice(cut);
      const series = [
        { name: "Gerçekleşen", values: r.history.values.slice(cut), color: "var(--ink-2)" },
        { name: `${mname(pick.value)} tahmini`, values: dates.map((d) => main.get(d)?.y_pred ?? null), color: "var(--series-1)" },
      ];
      if (compare.value && compare.value !== pick.value) {
        const other = byModel(compare.value);
        series.push({ name: `${mname(compare.value)} tahmini`, values: dates.map((d) => other.get(d)?.y_pred ?? null), color: "var(--series-2)", dash: "5 4" });
      }
      const band = { name: "%80 tahmin aralığı", lower: dates.map((d) => main.get(d)?.lower ?? null), upper: dates.map((d) => main.get(d)?.upper ?? null), color: "var(--series-1)" };
      chartBox.replaceChildren(lineChart({ dates, series, band: band.lower.some((v) => v !== null) ? band : null, height: 320, format: (x) => valueText(ti, x) }));
    };
    pick.addEventListener("change", draw);
    compare.addEventListener("change", draw);
    draw();

    const verdict = gain === null ? null : gain > 0.02
      ? [h("strong", {}, `${mname(r.best_model)}, en basit yöntem olan naive tahmine göre ortalama hatayı %${nf(1).format(gain * 100)} azaltıyor`), ` (ortalama hata ${errText(best.mae)} / ${errText(naive.mae)} ${unitWord}).`]
      : [h("strong", {}, "Hiçbir model naive tahmini belirgin biçimde geçemedi."), " Bu hedef için son değer güçlü bir çıtadır; bir ML modelinin buna yakın sonuç vermesi olağandır."];
    const cov = best?.coverage80;
    const covNote = cov === null || cov === undefined ? null : Math.abs(cov - 0.8) <= 0.07 ? "aralıklar iyi ayarlı" : cov < 0.8 ? "aralıklar gereğinden dar" : "aralıklar gereğinden geniş";
    const regimeRows = r.regime_metrics.filter((x) => x.model === r.best_model || x.model === "naive");
    const regimes = [...new Set(regimeRows.map((x) => x.regime))].map((reg) => {
      const b = regimeRows.find((x) => x.regime === reg && x.model === r.best_model);
      const n = regimeRows.find((x) => x.regime === reg && x.model === "naive");
      return { regime: reg, n: b?.n ?? n?.n, best: b?.mae, naive: n?.mae, gain: b && n && n.mae ? 1 - b.mae / n.mae : null };
    });
    const winsAll = regimes.length && regimes.every((x) => x.gain === null || x.gain > 0);

    fill(out,
      summary(verdict,
        `Sınama ${r.common_origins} başlangıç noktasında yapıldı; her noktada model yalnızca o güne kadar bilinen veriyle kuruldu.`,
        regimes.length ? (winsAll ? "Üstünlük üç ekonomik dönemin hepsinde korunuyor; sonuç tek bir döneme bağlı değil." : "Üstünlük her dönemde korunmuyor; sonuçlar dönem tablosuyla birlikte yorumlanmalı.") : null,
        "Bir ML modelinin anlamlı katkı sağladığını söyleyebilmek için bu çıtanın altında hata üretmesi gerekir."),
      h("div", { class: "tiles" },
        tile("Çıta model", mname(r.best_model), (MODEL_INFO[r.best_model]?.[1] || "").replace(/\s*\([^)]*\)/g, "").split(/\.\s/)[0], "Hesap"),
        tile("Naive'e göre iyileşme", gain === null ? "—" : `%${nf(1).format(gain * 100)}`, "ortalama hatadaki azalma", "Hesap"),
        tile("Ortalama hata (MAE)", best ? errText(best.mae) : "—", unitWord, "Hesap"),
        tile("%80 aralık kapsaması", cov === null || cov === undefined ? "—" : ratioPct(cov, 0), covNote || "aralık üretilmedi", "Hesap")),
      h("section", { class: "card" },
        h("div", { class: "card-head" },
          h("div", {}, h("h2", {}, `${ti?.label || r.config.target}: gerçekleşen ve tahmin`), h("p", {}, `${ti?.transform_label || ""} · ${r.config.horizon} dönem sonrası tahmin`)),
          h("div", { class: "controls" }, h("label", { class: "field" }, h("span", {}, "Model"), pick), h("label", { class: "field" }, h("span", {}, "Karşılaştır"), compare))),
        chartBox,
        h("p", { class: "caption" }, "Gri çizgi gerçekleşen değerdir. Gölgeli alan, modelin geçmiş hatalarından kurulan %80 tahmin aralığıdır: gerçekleşenin bu alanın içinde kalması beklenir.")),
      h("div", { class: "grid-2" },
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, term("naive_beat", "Naive'e göre hata oranı")), h("p", {}, "Her modelin ortalama hatası, naive tahminin hatasına bölünmüştür. 1'in solunda kalan model naive'den iyidir; vurgulanan çubuk çıta modeldir."))),
          naive && naive.mae
            ? barChart(metrics.filter((x) => x.mae !== null).map((x) => ({ label: mname(x.model), value: x.mae / naive.mae, highlight: x.model === r.best_model, note: `MAE ${errText(x.mae)}` })),
              { format: (v) => nf(2).format(v), ref: { value: 1, label: "naive" }, cap: 2.5 })
            : h("p", { class: "muted" }, "Karşılaştırma için naive modelini de seçin."),
          h("p", { class: "caption" }, "Kesikli çizgi: naive düzeyi (1,0). Örneğin 0,44, hatanın naive'in %44'ü kadar olduğu anlamına gelir. 2,5'i aşan çubuklar kesik gösterilir.")),
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, term("regime", "Ekonomik dönemlere göre")), h("p", {}, "Çıta model ile naive'in her dönemdeki ortalama hatası."))),
          regimes.length ? table([
            { label: "Dönem", key: "regime" },
            { label: "Nokta", key: "n", num: true },
            { label: mname(r.best_model), num: true, render: (x) => errText(x.best) },
            { label: "Naive", num: true, render: (x) => errText(x.naive) },
            { label: "İyileşme", num: true, render: (x) => (x.gain === null ? "—" : `${x.gain >= 0 ? "" : "−"}%${nf(1).format(Math.abs(x.gain) * 100)}`) },
          ], regimes) : h("p", { class: "muted" }, "Dönem ayrımı için yeterli gözlem yok."))),
      h("details", { class: "card" }, h("summary", {}, "Tüm modeller ve metrikler (veri bilimi ayrıntısı)"),
        table([
          { label: "Model", render: (x) => h("span", {}, mname(x.model), h("span", { class: "code" }, x.model)) },
          { label: "Nokta", key: "n", num: true },
          { label: term("mae", "MAE"), num: true, render: (x) => errText(x.mae) },
          { label: term("rmse", "RMSE"), num: true, render: (x) => errText(x.rmse) },
          { label: term("mase", "MASE"), num: true, render: (x) => num(x.mase, 3) },
          { label: term("smape", "sMAPE"), num: true, render: (x) => num(x.smape, 2) },
          { label: term("coverage", "Kapsama"), num: true, render: (x) => ratioPct(x.coverage80, 0) },
          { label: term("width", "Aralık"), num: true, render: (x) => errText(x.width80) },
          { label: term("pinball", "Pinball"), num: true, render: (x) => num(x.pinball, 3) },
        ], metrics, { rowClass: (x) => (x.model === r.best_model ? "best" : null) })),
      h("details", { class: "card" }, h("summary", {}, "Model kartı: bu sınama nasıl yapıldı?"),
        h("ul", {},
          h("li", {}, h("strong", {}, "Hedef: "), `${ti?.label || r.config.target} (${ti?.transform_label || ""}, ${r.config.target})`),
          h("li", {}, h("strong", {}, "Ufuk: "), `${r.config.horizon} dönem sonrası`),
          h("li", {}, h("strong", {}, "Yöntem: "), `Kayan başlangıç noktalı backtest; ilk tahminden önce en az ${r.config.min_train} dönem eğitim, toplam ${r.common_origins} ortak başlangıç noktası.`),
          h("li", {}, h("strong", {}, "Look-ahead koruması: "), "Her başlangıç noktasında yalnızca o tarihte yayımlanmış veri kullanıldı (yayın gecikmeleri panelde uygulanmıştır)."),
          r.config.anchor ? h("li", {}, h("strong", {}, "Çapa: "), `${labelOf(m, r.config.anchor)} (${r.config.anchor})`) : null,
          h("li", {}, h("strong", {}, "Açıklayıcı feature'lar: "), r.features_used.length ? r.features_used.map((f) => labelOf(m, f)).join(", ") : "kullanılmadı"),
          h("li", {}, h("strong", {}, "Sınırlamalar: "), "Sonuçlar geçmiş dönemlere aittir; structural break sonrası dönemlerde hata dağılımı değişebilir. Tahmin değil, karşılaştırma çıtasıdır."))),
      stampBar({ sources: sourcesOf(m), generated: stampTime(r.generated_at), period: r.history.dates.length ? `${monthName(r.history.dates[0])} – ${monthName(r.history.dates.at(-1))}` : "—" }),
    );
  };

  run.addEventListener("click", () => busy(run, "Sınanıyor", async () => {
    const body = {
      ...scope(), target: target.value, anchor: anchor.value || undefined,
      horizon: Number(horizon.value), min_train: Number(minTrain.value), exogenous: exog.checked,
      models: state.meta.models.filter((mm) => chosen.has(mm)),
    };
    if (!body.models.length) throw new Error("En az bir model seçin.");
    if (!body.models.includes("naive")) body.models.unshift("naive"); // the benchmark is always compared with naive
    savePrefs({ [`target:${sector}`]: body.target, [`anchor:${sector}`]: anchor.value, horizon: body.horizon, min_train: body.min_train, exogenous: body.exogenous, models: body.models });
    const r = await postJSON("/api/backtest", body);
    state.backtests.set(sector, r);
    render(r);
  }, out));

  fill(root,
    viewHead("Adım 4", `${m.sector.name}: tahmin çıtası`, "Basit tahmin yöntemleri geçmişte sınanır. Ortaya çıkan en düşük hata, geliştireceğiniz ML modelinin aşması gereken çıtadır."),
    h("section", { class: "card" },
      h("div", { class: "controls" },
        h("label", { class: "field" }, h("span", {}, "Tahmin edilecek feature"), target),
        h("label", { class: "field" }, h("span", {}, term("anchor", "Çapa (isteğe bağlı)")), anchor),
        h("label", { class: "field" }, h("span", {}, term("horizon", "Ufuk (dönem)")), horizon),
        h("label", { class: "field" }, h("span", {}, term("min_train", "Asgari eğitim")), minTrain)),
      h("label", { class: "check" }, exog, h("span", {}, "Diğer feature'ları regresyon modellerinde açıklayıcı olarak kullan", h("small", {}, "Ridge ve kantil regresyon, hedefin yanında paneldeki diğer feature'ların geçmiş değerlerini de kullanır."))),
      h("details", { class: "about" }, h("summary", {}, `Sınanacak modeller (${chosen.size} seçili)`), h("div", {}, modelChecks,
        h("div", { class: "button-row" }, h("button", { type: "button", class: "ghost small", onclick: () => { state.meta.models.forEach((mm) => chosen.add(mm)); modelChecks.querySelectorAll("input").forEach((i) => (i.checked = true)); } }, "Tümünü seç")))),
      h("div", { class: "button-row" }, run)),
    out,
  );
  const cached = state.backtests.get(sector);
  if (cached) render(cached);
  else out.append(emptyState("Sınama henüz yapılmadı", "Tahmin edilecek feature'ı seçip \"Sınamayı başlat\" düğmesine basın. Aylık ortalama bir feature'ı tahmin ediyorsanız, aynı serinin ay sonu sürümünü çapa olarak seçmek çıtayı belirgin biçimde iyileştirir."));
};

views.saglik = async (root) => {
  const run = h("button", { type: "button" }, "Kaynakları denetle");
  const out = h("div", { class: "stack" });
  run.addEventListener("click", () => busy(run, "Denetleniyor", async () => {
    const rows = await getJSON("/api/health");
    const kind = { ok: ["ok", "Çalışıyor"], fail: ["crit", "Hata"], skipped: ["none", "Atlandı"] };
    const ok = rows.filter((r) => r.status === "ok").length;
    fill(out,
      summary([h("strong", {}, `${rows.length} kaynaktan ${ok} tanesi şu anda veri veriyor.`)], "Atlanan kaynaklar ya henüz otomatik çekim desteği olmayan ya da API anahtarı gerektiren kaynaklardır."),
      h("section", { class: "card" }, table([
        { label: "Kaynak", render: (r) => h("code", { class: "pill" }, r.source_id) },
        { label: "Durum", render: (r) => status(...(kind[r.status] || ["none", r.status])) },
        { label: "Ayrıntı", key: "detail" },
        { label: "Denetlenen seriler", render: (r) => r.checked.join(", ") || "—" },
      ], rows)));
  }, out));
  fill(root,
    viewHead(null, "Kaynak durumu", "Her kaynağın kritik serilerinden son gözlemin çekilip çekilemediğini denetler. EVDS için API anahtarı .env dosyasında EVDS_API_KEY olarak tanımlanmalıdır."),
    h("section", { class: "card" }, h("div", { class: "button-row" }, run)),
    out,
  );
};

// --- market share and competitors -----------------------------------------------------------------

const SHARE_COLORS = Array.from({ length: 10 }, (_, i) => `var(--cat-${i + 1})`);
const SHARE_REST = { "Diğer oyuncular": "var(--muted)", "Listelenmeyen": "var(--axis)" };
const MAX_TABLE_BYTES = 8 * 1024 * 1024 - 16 * 1024; // the API caps the request at 8 MB; room for the JSON envelope
const SAMPLE = { url: "/static/sample_market_share.csv", name: "sample_market_share.csv" };

// Fixed decimals on this screen so columns line up (%9,8 and %26,6; +0,50 puan).
const fx = (d) => new Intl.NumberFormat("tr-TR", { minimumFractionDigits: d, maximumFractionDigits: d });
const signOf = (v, d) => (Math.abs(v) < 0.5 * 10 ** -d ? "" : v > 0 ? "+" : "−");
const signed = (v, d) => `${signOf(v, d)}${fx(d).format(Math.abs(v))}`;
const ppText = (v, d = 1) => (v === null || v === undefined ? "—" : `${signed(v, d)} puan`);
const signedPct = (v, d = 1) => (v === null || v === undefined ? "—" : `${signOf(v, d)}%${fx(d).format(Math.abs(v))}`);
const sharePct = (v, d = 1) => (v === null || v === undefined ? "—" : `%${fx(d).format(v)}`);
const plainSigned = (v, d) => (v === null || v === undefined ? "—" : `${v < 0 && signOf(v, d) ? "−" : ""}${fx(d).format(Math.abs(v))}`);
const lastValue = (arr) => [...(arr || [])].reverse().find((v) => v !== null && v !== undefined) ?? null;
const tableFormat = (text, name) => (/\.json$/i.test(name || "") || /^\s*[[{]/.test(text) ? "json" : "csv");
const byteSize = (text) => new Blob([text]).size;
const sizeText = (bytes) => (bytes >= 1024 * 1024 ? `${nf(1).format(bytes / 1024 / 1024)} MB` : `${nf(0).format(Math.max(1, bytes / 1024))} KB`);

function periodName(iso, ppy) {
  if (!iso) return "—";
  if (ppy === 4) return `${iso.slice(0, 4)} ${Math.ceil(Number(iso.slice(5, 7)) / 3)}. çeyrek`;
  if (ppy === 1) return iso.slice(0, 4);
  if (ppy === 52) return dateTR(iso);
  return monthName(iso);
}

views.pazar = async (root) => {
  const saved = state.share || {};
  const opts = { measure: "value", kind: "flow", basis: "auto", unit: "", trend_years: 3, ...saved.opts };
  let input = saved.input || null; // {text, name, kind: "file" | "paste" | "sample"}

  const select = (label, value, options) => h("select", { "aria-label": label }, options.map(([v, l]) => h("option", { value: v, selected: v === value }, l)));
  const measure = select("Değer türü", opts.measure, [["value", "Tutar ya da adet"], ["share", "Yüzde pay"]]);
  const kind = select("Değerin niteliği", opts.kind, [["flow", "Akım (satış, prim)"], ["stock", "Stok (abone, mevduat)"]]);
  const basis = select("Pay tabanı", opts.basis, [["auto", "Otomatik"], ["period", "Dönemlik"], ["ttm", "Son 12 ay toplamı"]]);
  const unit = h("input", { type: "text", value: opts.unit, maxlength: 40, placeholder: "ör. adet, TL, abone", "aria-label": "Birim" });
  const years = h("input", { type: "number", min: 1, max: 10, value: opts.trend_years, "aria-label": "Eğilim penceresi (yıl)" });
  years.style.width = "90px";
  const focus = h("select", { "aria-label": "Odak oyuncu" });
  const file = h("input", { type: "file", accept: ".csv,.json,text/csv,application/json", "aria-label": "Pazar payı tablosu dosyası" });
  const paste = h("textarea", { spellcheck: "false", "aria-label": "Pazar payı tablosunu yapıştırın", placeholder: "entity,date,value,segment\nMarka A,2024-01,1520,Binek\nMarka B,2024-01,980,Binek\nTOPLAM,2024-01,3100,Binek" });
  const inputStatus = h("p", { class: "input-status", role: "status" });
  const run = h("button", { type: "button" }, "Analiz et");
  const sample = h("button", { type: "button", class: "ghost" }, "Örnek veriyi yükle");
  const out = h("div", { class: "stack" });

  const fillFocus = (players, chosen) => {
    focus.replaceChildren(h("option", { value: "" }, players.length ? "Seçilmedi" : "Önce analiz edin"),
      ...players.map((p) => h("option", { value: p.entity, selected: p.entity === chosen }, p.entity)));
    focus.disabled = !players.length;
  };
  const showStatus = () => {
    if (!input) { inputStatus.replaceChildren("Henüz tablo seçilmedi."); return; }
    const rows = Math.max(0, input.text.trim().split(/\r?\n/).length - 1);
    const what = { file: "Dosya", paste: "Yapıştırılan tablo", sample: "Örnek veri" }[input.kind];
    inputStatus.replaceChildren(h("strong", {}, `${what}: `), input.kind === "paste" ? "" : `${input.name} · `,
      tableFormat(input.text, input.name) === "json" ? "JSON" : `${nf(0).format(rows)} veri satırı`, ` · ${sizeText(byteSize(input.text))}`);
  };

  if (input?.kind !== "file" && input) paste.value = input.text;
  fillFocus(saved.result?.players || [], saved.result?.meta.focus);
  showStatus();

  file.addEventListener("change", async () => {
    const f = file.files[0];
    if (!f) return;
    notice(null);
    if (f.size > MAX_TABLE_BYTES) { notice(`Dosya çok büyük (${sizeText(f.size)}); en fazla 8 MB yüklenebilir.`); file.value = ""; return; }
    input = { text: await f.text(), name: f.name, kind: "file" };
    paste.value = "";
    fillFocus([]);
    showStatus();
  });
  paste.addEventListener("input", () => {
    input = paste.value.trim() ? { text: paste.value, name: "", kind: "paste" } : null;
    file.value = "";
    fillFocus([]);
    showStatus();
  });

  const analyze = (button) => busy(button, "Hesaplanıyor", async () => {
    if (!input) throw new Error("Önce bir dosya seçin, tabloyu yapıştırın ya da örnek veriyi yükleyin.");
    if (byteSize(input.text) > MAX_TABLE_BYTES) throw new Error("Tablo 8 MB sınırını aşıyor.");
    const body = {
      text: input.text, format: tableFormat(input.text, input.name), focus: focus.value || undefined,
      measure: measure.value, kind: kind.value, basis: basis.value, unit: unit.value.trim(), trend_years: Number(years.value),
    };
    const r = await postJSON("/api/market-share", body);
    const { text, format, focus: _f, ...rest } = body;
    state.share = { input, opts: rest, result: r };
    fillFocus(r.players, r.meta.focus);
    render(r, input, rest);
  }, out);

  run.addEventListener("click", () => analyze(run));
  focus.addEventListener("change", () => analyze(run));
  sample.addEventListener("click", async () => {
    notice(null);
    try {
      const res = await fetch(SAMPLE.url);
      if (!res.ok) throw new Error(`Örnek veri yüklenemedi (HTTP ${res.status}).`);
      input = { text: await res.text(), name: SAMPLE.name, kind: "sample" };
    } catch (err) { notice(err.message); return; }
    paste.value = input.text;
    file.value = "";
    measure.value = "value"; kind.value = "flow"; basis.value = "auto"; unit.value = "adet";
    fillFocus([]);
    showStatus();
    analyze(sample);
  });

  const render = (r, src, o) => {
    const L = r.labels;
    const meta = r.meta;
    const ppy = meta.periods_per_year;
    const conc = r.concentration.latest;
    const ago = r.concentration.year_ago;
    const players = r.players;
    const fp = meta.focus ? players.find((p) => p.entity === meta.focus) : null;
    const basisText = L.basis[meta.basis] || meta.basis;
    const period = `${periodName(meta.first, ppy)} – ${periodName(meta.last, ppy)}`;
    const trendOf = (p) => L.trend[p.trend] || p.trend;
    const gainers = players.filter((p) => p.trend === "gaining").sort((a, b) => b.trend_pp_per_year - a.trend_pp_per_year);
    const losers = players.filter((p) => p.trend === "losing").sort((a, b) => a.trend_pp_per_year - b.trend_pp_per_year);
    const growth = meta.measure === "value" ? lastValue(r.market.growth_yoy_pct) : null;
    const marketSize = meta.measure === "value" ? lastValue(r.market.total) : null;
    const coverage = meta.has_total ? lastValue(r.market.coverage) : null;
    const cands = r.competitors?.candidates || [];
    const seg = r.segments;
    const ss = seg?.shift_share;
    const listNames = (list) => list.slice(0, 3).map((p) => `${p.entity} (${signed(p.trend_pp_per_year, 2)} puan/yıl)`).join(", ");
    const trendKind = { gaining: "ok", losing: "crit", flat: "none", insufficient_data: "none" };

    // shares over time: top players + focus, the rest grouped
    let k = 0;
    const shareSeries = Object.entries(r.shares.series).map(([name, values]) => {
      const isFocus = name === meta.focus;
      const color = SHARE_REST[name] || SHARE_COLORS[k++ % SHARE_COLORS.length];
      return { name: isFocus ? `${name} (odak)` : name, values, color, width: isFocus ? 3.5 : null, dash: SHARE_REST[name] ? "4 4" : null };
    });

    const trendBars = (list, sign) => list.length
      ? barChart(list.slice(0, 10).map((p) => ({ label: p.entity, value: Math.abs(p.trend_pp_per_year), highlight: p.entity === meta.focus, note: `t = ${plainSigned(p.trend_t, 1)} · pay ${sharePct(p.share)}` })),
        { format: (v) => `${sign}${fx(2).format(v)} puan/yıl` })
      : h("p", { class: "muted" }, "Bu grupta anlamlı eğilim gösteren oyuncu yok.");

    const competitorCard = meta.focus
      ? h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, `${meta.focus} için rakip seti`), h("p", {}, "Adaylar veriden çıkarılır: pay sıralamasında komşuluk, pazar liderliği, ters yönlü pay değişimi (substitution correlation) ve segment karmasının örtüşmesi. Gerekçe sayısı arttıkça aday güçlenir."))),
        cands.length ? table([
          { label: "Oyuncu", key: "entity" },
          { label: "Pay", num: true, render: (c) => sharePct(c.share) },
          { label: term("substitution", "Korelasyon (r)"), num: true, render: (c) => plainSigned(c.r, 2) },
          { label: "Gerekçeler", render: (c) => h("ul", { class: "reasons" }, c.reasons.map((x) => h("li", {}, x))) },
        ], cands) : h("p", { class: "muted" }, "Veriden belirgin bir rakip adayı çıkmadı."),
        h("details", { class: "about" }, h("summary", {}, "Tüm oyuncularla substitution correlation"),
          h("div", {}, table([
            { label: "Oyuncu", key: "entity" },
            { label: "r", num: true, render: (x) => plainSigned(x.r, 2) },
            { label: "Gözlem (n)", num: true, key: "n" },
            { label: "Anlamlı", render: (x) => (x.significant ? status("ok", "Evet") : status("none", "Hayır")) },
          ], r.competitors.substitution))),
        h("p", { class: "caption" }, "Negatif r, odak oyuncu pay kazanırken o oyuncunun pay kaybettiğini gösterir. En az 12 gözlem ve |r| ≥ 2/√n koşulunda anlamlı sayılır; nedensellik kanıtı değildir. Nitel ölçütlerle (fiyat segmenti, kanal, coğrafya) birlikte değerlendirin."))
      : h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Rakip seti"), h("p", {}, "Rakip adaylarını görmek için yukarıdaki \"Odak oyuncu\" listesinden bir oyuncu seçin; analiz o oyuncuya göre yeniden hesaplanır."))));

    const segmentCard = seg ? h("section", { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, `Segment kırılımı (${periodName(seg.date, ppy)})`), h("p", {}, "Her segmentin pazardaki ağırlığı, yoğunlaşması ve lideri." + (meta.focus ? ` ${meta.focus} için segment payları ve bir yıllık değişimleri.` : "")))),
      table([
        { label: "Segment", key: "segment" },
        { label: "Ağırlık", num: true, render: (x) => sharePct(x.weight) },
        { label: "Ağırlık değişimi", num: true, render: (x) => ppText(x.weight_d_pp) },
        { label: "HHI", num: true, render: (x) => num(x.hhi, 0) },
        { label: "Lider", render: (x) => (x.leader ? `${x.leader} (${sharePct(x.leader_share)})` : "—") },
        ...(meta.focus ? [
          { label: `${meta.focus} payı`, num: true, render: (x) => sharePct(x.focus_share) },
          { label: "Bir yılda", num: true, render: (x) => ppText(x.focus_share_d_pp) }] : []),
      ], seg.segments),
      ss ? h("div", { class: "stack" },
        h("h3", {}, term("shift_share", `Shift-share: ${periodName(ss.from, ppy)} → ${periodName(ss.to, ppy)}`)),
        h("p", {}, `${meta.focus} payındaki ${ppText(ss.total_pp, 2)} değişimin ${ppText(ss.share_effect_pp, 2)} kısmı segment içi pay değişiminden, ${ppText(ss.mix_effect_pp, 2)} kısmı segment karmasındaki değişimden geliyor.`),
        table([
          { label: "Segment", key: "segment" },
          { label: "Segment içi pay etkisi", num: true, render: (x) => ppText(x.share_effect_pp, 2) },
          { label: "Segment karması etkisi", num: true, render: (x) => ppText(x.mix_effect_pp, 2) },
          { label: "Toplam", num: true, render: (x) => ppText(x.share_effect_pp + x.mix_effect_pp, 2) },
        ], [...ss.by_segment, { segment: "Toplam", share_effect_pp: ss.share_effect_pp, mix_effect_pp: ss.mix_effect_pp, total: true }],
        { rowClass: (x) => (x.total ? "best" : null) }))
        : meta.focus ? h("p", { class: "muted" }, "Shift-share için en az bir yıllık segment verisi gerekir.")
          : h("p", { class: "caption" }, "Odak oyuncu seçildiğinde pay değişimi segment içi pay etkisi ve segment karması etkisine ayrıştırılır.")) : null;

    fill(out,
      summary(
        [h("strong", {}, `${periodName(meta.last, ppy)} itibarıyla ilk üç oyuncunun toplam payı ${sharePct(conc.cr3)}; HHI ${num(conc.hhi, 0)} ile yoğunlaşma ${L.hhi_class[conc.class]} düzeyde.`),
          ` Paylar ${basisText} tabanında, ${meta.entities} oyuncu üzerinden hesaplandı.`],
        fp ? `${fp.entity} ${sharePct(fp.share)} payla ${fp.rank}. sırada; payı bir yılda ${ppText(fp.d_year_pp)} değişti, eğilim: ${trendOf(fp)}.` : "Rakip setini ve segment ayrıştırmasını görmek için odak oyuncuyu seçin.",
        gainers.length || losers.length
          ? `Son ${o.trend_years} yılda anlamlı biçimde pay kazananlar: ${gainers.length ? listNames(gainers) : "yok"}; pay kaybedenler: ${losers.length ? listNames(losers) : "yok"}.`
          : `Son ${o.trend_years} yılda istatistiksel olarak anlamlı bir pay kazanımı ya da kaybı yok.`,
        cands.length ? `${meta.focus} için öne çıkan rakip adayları: ${cands.slice(0, 3).map((c) => c.entity).join(", ")}.` : null),
      h("div", { class: "tiles" },
        tile("HHI", num(conc.hhi, 0), `${L.hhi_class[conc.class]} yoğunlaşma${ago ? ` · bir yıl önce ${num(ago.hhi, 0)}` : ""}`, "Hesap"),
        tile("CR3 / CR5", `${sharePct(conc.cr3)} / ${sharePct(conc.cr5)}`, "ilk 3 ve ilk 5 oyuncunun toplam payı", "Hesap"),
        tile("Eşdeğer oyuncu sayısı", fx(1).format(conc.equivalent_players), "aynı HHI'yi veren eşit paylı oyuncu sayısı", "Hesap"),
        fp ? tile(`${fp.entity} payı`, sharePct(fp.share), `${fp.rank}. sıra · bir yılda ${ppText(fp.d_year_pp)}`, "Hesap") : null,
        marketSize !== null ? tile("Pazar büyüklüğü", `${num(marketSize, 0)}${meta.unit ? ` ${meta.unit}` : ""}`, `${basisText}${growth !== null ? ` · bir yılda ${signedPct(growth)}` : ""}`, meta.has_total ? "Ölçüm" : "Hesap") : null,
        coverage !== null ? tile("Kapsama oranı", sharePct(coverage), "listelenen oyuncular / pazar toplamı", "Hesap") : null),
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, term("market_share", "Pazar payları")), h("p", {}, `${basisText} tabanında yüzde pay.${meta.focus ? " Kalın çizgi odak oyuncudur." : ""} Kesikli çizgiler küçük oyuncuların toplamını ve tabloda listelenmeyen kısmı gösterir.`))),
        lineChart({ dates: r.shares.dates, series: shareSeries, height: 340, format: (v) => sharePct(v, 1) })),
      h("div", { class: "grid-2" },
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, term("hhi", "HHI")), h("p", {}, "1.000'in altı düşük, 1.000–2.000 orta, 2.000'in üstü yüksek yoğunlaşma (AB Yatay Birleşme Rehberi)."))),
          lineChart({ dates: r.concentration.dates, series: [{ name: "HHI", values: r.concentration.hhi, color: "var(--series-1)" }], height: 220, format: (v) => num(v, 0) })),
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, term("cr_k", "CR3 ve CR5")), h("p", {}, "En büyük 3 ve 5 oyuncunun toplam payı."))),
          lineChart({ dates: r.concentration.dates, series: [
            { name: "CR3", values: r.concentration.cr3, color: "var(--series-1)" },
            { name: "CR5", values: r.concentration.cr5, color: "var(--series-2)" }], height: 220, format: (v) => sharePct(v, 1) }))),
      h("div", { class: "grid-2" },
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, term("share_trend", "Pay kazananlar")), h("p", {}, `Son ${o.trend_years} yılda yıllık pay eğilimi anlamlı biçimde pozitif olanlar.`))),
          trendBars(gainers, "+")),
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, term("share_trend", "Pay kaybedenler")), h("p", {}, `Son ${o.trend_years} yılda yıllık pay eğilimi anlamlı biçimde negatif olanlar.`))),
          trendBars(losers, "−"))),
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Oyuncular"), h("p", {}, `Son dönemdeki pay, değişimler ve eğilim. Eğilim yılda en az 0,2 puan ve |t| ≥ 2 ise anlamlı sayılır.`))),
        table([
          { label: "Sıra", num: true, key: "rank" },
          { label: "Oyuncu", render: (p) => h("span", {}, p.entity, p.entity === meta.focus ? h("span", { class: "chip accent tag-after" }, "odak") : null) },
          { label: "Pay", num: true, render: (p) => sharePct(p.share, 2) },
          { label: "Önceki döneme göre", num: true, render: (p) => ppText(p.d_prev_pp, 2) },
          { label: "Bir yılda", num: true, render: (p) => ppText(p.d_year_pp, 2) },
          { label: "Eğilim (puan/yıl)", num: true, render: (p) => (p.trend_pp_per_year === null ? "—" : signed(p.trend_pp_per_year, 2)) },
          { label: "t", num: true, render: (p) => plainSigned(p.trend_t, 1) },
          { label: "Durum", render: (p) => status(trendKind[p.trend] || "none", trendOf(p)) },
          ...(players.some((p) => p.growth_vs_market_pp !== undefined) ? [{ label: "Pazara göre büyüme", num: true, render: (p) => ppText(p.growth_vs_market_pp) }] : []),
        ], players, { scroll: true, rowClass: (p) => (p.entity === meta.focus ? "best" : null) })),
      competitorCard,
      segmentCard,
      r.issues.length ? h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Veri notları"), h("p", {}, "Tablo okunurken yapılan düzeltmeler ve yorumu etkileyen durumlar."))),
        h("div", { class: "feature-list" }, r.issues.map((i) => h("div", { class: "feature-row" },
          h("h3", {}, i.message), status(...(SEVERITY[i.severity] || ["none", i.severity])))))) : null,
      h("details", { class: "card" }, h("summary", {}, "Yöntem: bu analiz nasıl yapıldı?"),
        h("ul", {},
          h("li", {}, h("strong", {}, "Pay: "), meta.has_total ? "Oyuncu değeri / TOPLAM satırlarındaki pazar toplamı." : "Oyuncu değeri / listelenen oyuncuların toplamı (pazar toplamı verilmedi)."),
          h("li", {}, h("strong", {}, "Taban: "), `${basisText}. ${meta.basis === "ttm" ? "Akım verisinde mevsimselliği gidermek için son 12 ayın toplamı kullanıldı." : "Her dönemin kendi değeri kullanıldı."}`),
          h("li", {}, h("strong", {}, "Yoğunlaşma: "), "HHI = payların karelerinin toplamı; CR3 ve CR5 = en büyük 3 ve 5 oyuncunun payı."),
          h("li", {}, h("strong", {}, "Eğilim: "), `Son ${o.trend_years} yılın dönemlik paylarına doğrusal trend; eğim puan/yıl, anlamlılık t istatistiğiyle.`),
          h("li", {}, h("strong", {}, "Sınırlamalar: "), "Sonuçlar yalnızca yüklenen tabloyu yansıtır. Rakip adayları istatistiksel ipucudur; fiyat, kanal ve coğrafya gibi nitel ölçütlerle doğrulanmalıdır."))),
      stampBar({
        sources: src.kind === "sample" ? "Örnek veri (sentetik, otomotiv benzeri)" : src.kind === "file" ? `Kullanıcı tablosu (${src.name})` : "Kullanıcı tablosu (yapıştırıldı)",
        generated: stampTime(r.generated_at), period,
        missing: r.issues.some((i) => i.check === "missing_periods") ? "eksik dönem var (Veri notları)" : "yok",
      }),
    );
  };

  fill(root,
    viewHead("Pazar payı", "Pazar payı ve rakipler", "Kendi pazar payı tablonuzu yükleyin: paylar, yoğunlaşma (HHI, CR3/CR5), pay kazanan ve kaybeden oyuncular, rakip seti ve segment bazında shift-share ayrıştırması hesaplanır. Tablo yalnızca bu bilgisayarda işlenir, diske yazılmaz."),
    h("section", { class: "card" },
      h("div", { class: "drop" },
        h("div", {},
          h("div", { class: "file-pick" },
            h("strong", {}, "Dosya seçin (CSV ya da JSON)"),
            file,
            h("p", { class: "caption" }, "Kolonlar: entity, date, value ve isteğe bağlı segment. Pazar toplamı, oyuncu adı TOPLAM (ya da TOTAL) olan satırlarla verilir. Dönem: YYYY-AA, YYYY-Qn ya da YYYY. Noktalı virgülle ayrılmış Excel çıktılarında ondalık virgül kabul edilir. En fazla 8 MB."),
            h("div", { class: "button-row" }, sample)),
          inputStatus),
        h("div", {},
          h("label", { class: "field" }, h("span", {}, "ya da tabloyu yapıştırın"), paste))),
      h("div", { class: "controls" },
        h("label", { class: "field" }, h("span", {}, "Değer türü"), measure),
        h("label", { class: "field" }, h("span", {}, "Değerin niteliği"), kind),
        h("label", { class: "field" }, h("span", {}, term("ttm", "Pay tabanı")), basis),
        h("label", { class: "field" }, h("span", {}, "Birim"), unit),
        h("label", { class: "field" }, h("span", {}, term("share_trend", "Eğilim penceresi (yıl)")), years),
        h("label", { class: "field" }, h("span", {}, "Odak oyuncu"), focus)),
      h("div", { class: "button-row" }, run,
        h("span", { class: "caption" }, "Odak oyuncuyu değiştirdiğinizde analiz kendiliğinden yenilenir."))),
    out,
  );
  if (saved.result) render(saved.result, saved.input, { ...opts });
  else out.append(emptyState("Analiz henüz yapılmadı", "Bir tablo seçip \"Analiz et\" düğmesine basın ya da \"Örnek veriyi yükle\" ile sentetik otomotiv verisini deneyin."));
};

// --- routing and start-up -----------------------------------------------------------------------------

async function route() {
  const name = (location.hash || "#genel").slice(1);
  const view = views[name] ? name : "genel";
  document.querySelectorAll("nav a").forEach((a) => (a.dataset.view === view ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  const root = $("#view");
  $("#tooltip").hidden = true; // no mouseleave fires when the chart is replaced
  fill(root, h("p", { class: "muted" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Yükleniyor"));
  notice(null);
  try {
    await views[view](root);
    if (view !== "genel") root.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  } catch (err) {
    fill(root);
    notice(err.message);
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
    const current = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    applyTheme(next);
    savePrefs({ theme: next });
  });
  document.querySelectorAll("[data-glossary]").forEach((b) => b.addEventListener("click", () => openGlossary()));
  $("#glossary-close").addEventListener("click", () => ($("#glossary").hidden = true));
  $("#glossary-search").addEventListener("input", (e) => renderGlossary(e.target.value));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#glossary").hidden = true; });
  try {
    [state.meta, state.sectors] = await Promise.all([getJSON("/api/meta"), getJSON("/api/sectors")]);
  } catch (err) {
    notice(err.message);
    return;
  }
  const sel = $("#sector");
  sel.replaceChildren(...state.sectors.map((s) => h("option", { value: s.id, selected: s.id === (prefs.sector || "bankacilik") }, s.name)));
  $("#start").value = prefs.start || state.meta.default_start.slice(0, 7);
  $("#end").value = prefs.end || "";
  sel.addEventListener("change", () => { savePrefs({ sector: sel.value }); route(); });
  for (const id of ["start", "end"]) $(`#${id}`).addEventListener("change", (e) => { savePrefs({ [id]: e.target.value }); route(); });
  addEventListener("hashchange", route);
  route();
}

init();
