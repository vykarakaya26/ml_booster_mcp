// ML Booster local panel. No external libraries; the DOM is filled via textContent only (never innerHTML).
// Layout rule: summary first (finding sentence + KPI cards), then details; every view ends with a source/period stamp.
import { lineChart, barChart } from "./charts.js";
import { GLOSSARY } from "./glossary.js";

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
  return nf(a >= 1000 ? 0 : a >= 100 ? Math.min(1, digits) : digits).format(v);
}
const pctText = (v, d = 1) => (v === null || v === undefined ? "—" : `%${nf(d).format(v)}`);
const ratioPct = (v, d = 1) => (v === null || v === undefined ? "—" : pctText(v * 100, d));
const monthName = (iso) => (iso ? `${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(0, 4)}` : "—");
const dateTR = (iso) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}` : "—");
const stampTime = (iso) => (iso ? `${dateTR(iso.slice(0, 10))} ${iso.slice(11, 16)}` : "—");
const cap = (text) => (text ? text[0].toLocaleUpperCase("tr") + text.slice(1) : text); // labels arrive lower case

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
  ok: ["ok", "Hazır"], warn: ["warn", "İncelenmeli"], crit: ["crit", "Giderilmeli"], none: ["none", "Veri yok"],
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
  return h("section", { class: "summary", "aria-label": "Özet" }, h("h2", {}, "Özet"), sentences.filter(Boolean).map((s) => h("p", {}, ...[].concat(s))));
}

function stampBar({ sources, generated, period, missing, note }) {
  const item = (k, v) => (v ? h("span", {}, h("b", {}, `${k}: `), v) : null);
  return h("footer", { class: "stamp" },
    item("Kaynaklar", sources), item("Veri çekimi", generated), item("Kapsanan dönem", period), item("Eksik veri", missing),
    item("Durum", note || "Taslak: otomatik üretildi; karar öncesinde uzman incelemesi gerekir"));
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
  const entries = Object.entries(GLOSSARY).map(([k, g]) => [k, g.term, g.text])
    .filter(([, t, x]) => !q || t.toLocaleLowerCase("tr").includes(q) || x.toLocaleLowerCase("tr").includes(q))
    .sort((a, b) => a[1].localeCompare(b[1], "tr", { sensitivity: "base" }));
  $("#glossary-list").replaceChildren(...entries.map(([k, t, x]) => h("div", { id: `g-${k}` }, h("dt", {}, t), h("dd", {}, x))));
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

const state = { meta: null, sectors: [], maps: new Map(), panels: new Map(), audits: new Map(), share: null, shareSeries: null };

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

function shareText(m) {
  if (!m.market_share.length) return null;
  const ready = new Set(m.sources.filter((s) => s.provider_ready).map((s) => s.id));
  const names = (list) => list.map((s) => s.source.toUpperCase()).join(", ");
  const auto = m.market_share.filter((s) => ready.has(s.source));
  const manual = m.market_share.filter((s) => !ready.has(s.source));
  return `Pazar payı için ${m.market_share.length} kaynak tanımlı.` +
    (auto.length ? ` ${names(auto)} verisi "Pazar payı" ekranında doğrudan çekilebilir.` : "") +
    (manual.length ? ` ${names(manual)} için otomatik çekim henüz yok; bu kaynakların verisi tablo olarak yüklenebilir.` : "");
}

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

// What the user needs to do for a skipped feature.
function skippedAdvice(m, name, reason) {
  const label = labelOf(m, name);
  if (/keşif bekliyor|seri kodu bekleniyor/.test(reason)) return [h("strong", {}, label), ": EVDS seri kodu henüz bağlanmadı. Claude Code'da ", h("em", {}, `"${label.toLocaleLowerCase("tr")} serisini bul"`), " deyin ya da ", h("code", { class: "pill" }, "mlbooster catalog search"), " ile arayıp ", h("code", { class: "pill" }, "catalog set-feature"), " ile bağlayın."];
  if (/sağlayıcısı henüz yok|henüz provider yok/.test(reason)) return [h("strong", {}, label), ": bu kaynak için otomatik çekim henüz geliştirilmedi (yol haritasında)."];
  if (/Credentials|anahtar/i.test(reason)) return [h("strong", {}, label), ": kimlik bilgisi tanımlı değil. EVDS için API anahtarını .env dosyasına EVDS_API_KEY olarak ekleyin."];
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
      h("h1", {}, "Bir sektör seçin; kaynağı belgelenmiş, ML'e hazır aylık paneli oluşturun"),
      h("p", {}, "ML Booster; TCMB EVDS, TÜİK, BDDK, TBB, TSB, BTK, ODMD, EPDK, BKM, EPİAŞ, BIS ve OECD gibi ücretsiz kamu kaynaklarından sektörle ilgili feature'ları çeker ve point-in-time aylık bir panelde birleştirir. Ardından veri kalitesini denetler, ML'e hazır feature'ları dışa aktarır ve beş sektörde (bankacılık, sigorta, telekom, otomotiv, akaryakıt) bir marka adından sektörü, payı ve rakipleri doğrudan kaynaktan çıkarır. Tüm işlemler bu bilgisayarda yapılır."),
      h("div", { class: "button-row" },
        h("button", { type: "button", onclick: () => go($("#sector").value) }, `${sectorName()} ile başla`),
        h("button", { type: "button", class: "ghost", onclick: () => openGlossary() }, "Terim sözlüğü"))),
    h("div", { class: "steps" },
      [["1", "Sektör ve kaynaklar", "Sektör için hangi ücretsiz verilerin bulunduğu ve hangilerinin kullanıma hazır olduğu.", "Çıktı: veri kaynağı haritası", "harita"],
        ["2", "Feature paneli", "Feature'lar yayımlandıkları aya yazılır (point-in-time) ve ortak bir takvimde birleştirilir.", "Çıktı: CSV ya da Parquet panel", "panel"],
        ["3", "Veri kalitesi", "Stale seriler, missing value'lar, outlier'lar ve structural break'ler denetlenir.", "Çıktı: önem sırasına göre bulgular", "kalite"]]
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
      h("div", {}, h("strong", {}, "Look-ahead bias'a karşı koruma"), h("p", {}, "Her değer, gerçekte yayımlandığı aya yazılır; panel o tarihte bilinmeyen bilgi içermez.")),
      h("div", {}, h("strong", {}, "İzlenebilir kaynak"), h("p", {}, "Her feature kurum, seri kodu ve yayın gecikmesiyle birlikte gösterilir; lisans notları haritada yer alır.")),
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
      status(FEATURE_STATUS[f.status], cap(m.labels?.status?.[f.status] || f.status)),
      h("div", { class: "chips", style: "grid-column:1/-1" },
        h("span", { class: "chip accent" }, i.transform_label),
        h("span", { class: "chip" }, `Birim: ${i.unit}`),
        i.frequency ? h("span", { class: "chip" }, `Kaynak frekansı: ${i.frequency}`) : null,
        h("span", { class: "chip" }, i.source_name),
        i.publication_lag_days ? h("span", { class: "chip" }, `Yayın gecikmesi: ${i.publication_lag_days} gün`) : null),
      h("p", {}, f.status === "pending_discovery" ? `Seri kodu henüz bağlanmadı. Arama ipucu: ${f.discover_hint}` : i.description));
  };

  fill(root,
    viewHead("Adım 1", `${m.sector.name}: veri kaynakları ve feature'lar`, "Sektör için tanımlı ücretsiz kaynaklar ve modelde kullanılacak feature'lar. Hazır feature'lar bir sonraki adımda panele dönüştürülür."),
    summary(
      [h("strong", {}, `${m.sector.name} için ${m.macro_features.length} feature tanımlı; ${ready} tanesi kullanıma hazır.`),
        sourceNames.length ? ` Hazır feature'lar ${sourceNames.join(", ")} kaynaklı.` : ""],
      groups.pending_discovery.length ? `${groups.pending_discovery.length} feature'ın EVDS seri kodu henüz bağlanmadı; nasıl tamamlanacağı "Yapılması gerekenler" bölümünde açıklanır.` : "Seri kodu eksik feature yok.",
      shareText(m)),
    h("div", { class: "tiles" },
      tile("Hazır feature", ready, `toplam ${m.macro_features.length} feature'dan`, "Ölçüm"),
      tile("Seri kodu eksik", groups.pending_discovery.length, groups.pending_discovery.length ? "EVDS'de bulunup bağlanmalı" : "eksik yok"),
      tile("Veri kaynağı", m.sources.length, `${m.sources.filter((s) => s.provider_ready).length} kaynak otomatik çekiliyor`),
      tile("ML kullanım alanı", m.sector.ml_use_cases.length, m.sector.ml_use_cases.join(", ") || "tanımlı değil")),
    todo("Yapılması gerekenler", [...groups.pending_discovery, ...groups.no_provider].map((f) =>
      skippedAdvice(m, f.name, f.status === "pending_discovery" ? "keşif bekliyor" : "sağlayıcısı henüz yok"))),
    h("section", { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Feature'lar"), h("p", {}, "Her feature'ın tanımı, panele hangi dönüşümle girdiği ve kaynağı. Mavi etiket, modelde kullanılan dönüşümü gösterir."))),
      h("div", { class: "feature-list" }, [...groups.ready, ...groups.pending_discovery, ...groups.no_provider].map(featureRow))),
    h("section", { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Kaynaklar"), h("p", {}, "Rol: makro feature, sektör verisi ya da pazar payı. Doğrulama: kaynağın erişim yönteminin canlı olarak test edilip edilmediği."))),
      table([
        { label: "Kurum", render: (s) => h("span", {}, s.name, h("span", { class: "code" }, s.owner)) },
        { label: "Rol", render: (s) => cap(s.roles.join(", ")) },
        { label: "Erişim", render: (s) => cap(m.labels?.access?.[s.access] || s.access) },
        { label: "Frekans", render: (s) => cap(s.frequency.map((f) => m.labels?.frequency?.[f] || f).join(", ")) },
        { label: "Yayın gecikmesi (gün)", key: "publication_lag_days", num: true },
        { label: "Doğrulama", render: (s) => cap(m.labels?.verification?.[s.verification_status] || s.verification_status) },
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
        [h("strong", {}, `Panel ${period} dönemini kapsıyor: ${p.rows} dönem, ${p.features.length} feature.`), " Her değer, kaynağın yayımladığı döneme yazıldı (point-in-time); panel doğrudan model eğitiminde kullanılabilir."],
        skipped.length ? `${skipped.length} feature panele eklenemedi; nedenleri ve yapılması gerekenler aşağıda.` : "Tanımlı tüm hazır feature'lar panele eklendi."),
      h("div", { class: "tiles" },
        tile("Dönem sayısı", p.rows, period, "Hesap"),
        tile("Feature sayısı", p.features.length, `${m.macro_features.length} tanımlı feature'dan`, "Ölçüm"),
        tile("Panele eklenemeyen", skipped.length, skipped.length ? "nedenleri aşağıda" : "yok"),
        tile("Frekans", { M: "Aylık", W: "Haftalık", B: "İş günü" }[p.freq], "dönem sonu takvimi")),
      todo("Yapılması gerekenler", skipped.map(([f, r]) => skippedAdvice(m, f, r))),
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Gösterilecek feature'lar"), h("p", {}, "Her feature kendi ölçeğinde ayrı grafikte yer alır. Seçiminiz bu tarayıcıda hatırlanır."))),
        picks),
      grid,
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Panelin son 12 dönemi"), h("p", {}, "Modelin göreceği tablo. Tamamı için CSV ya da Parquet olarak indirin."))),
        table([{ label: "Dönem", render: (r) => monthName(r.date) },
          ...p.features.map((f) => ({ label: h("span", {}, labelOf(m, f), h("span", { class: "code" }, f)), num: true, render: (r) => valueText(info[f], p.columns[f][r.idx]) }))], rows)),
      stampBar({ sources: sourcesOf(m), generated: stampTime(p.generated_at), period, missing: skipped.length ? `${skipped.length} feature panele eklenemedi` : "yok" }),
    );
  };

  run.addEventListener("click", () => busy(run, "Oluşturuluyor", async () => {
    savePrefs({ freq: freq.value });
    const p = await postJSON("/api/panel", { ...scope(), freq: freq.value });
    state.panels.set(scopeKey({ freq: freq.value }), p);
    render(p);
  }, out));

  fill(root,
    viewHead("Adım 2", `${m.sector.name}: feature paneli`, "Hazır feature'lar ortak bir takvimde birleştirilir. Günlük ve haftalık veriler aylık frekansa indirgenir; çeyreklik veriler yeni değer yayımlanana kadar sonraki aylara taşınır."),
    h("section", { class: "card" },
      h("div", { class: "controls" },
        h("label", { class: "field" }, h("span", {}, "Frekans"), freq),
        h("div", { class: "button-row" }, run, csv, parquet)),
      h("p", { class: "caption" }, term("point_in_time"), ": Mart enflasyonu Nisan başında açıklandığı için panelde Nisan satırında yer alır. Böylece model, o tarihte henüz bilinmeyen veriyi kullanamaz ve look-ahead bias oluşmaz.")),
    out,
  );
  const cached = state.panels.get(scopeKey({ freq: freq.value }));
  if (cached) render(cached);
  else out.append(emptyState("Panel henüz oluşturulmadı", "\"Paneli oluştur\" düğmesine basın. İlk çalıştırmada veriler kaynaktan çekilir, sonrakiler yerel önbellekten okunur."));
};

const CHECKS = {
  stale: ["Stale seri", "stale", "Kaynakta serinin güncel kodunu arayın; seri arşive taşınmış ya da kodu değişmiş olabilir."],
  missing: ["Missing value", "missing", "Başlangıç tarihini ileri alın ya da missing value'ların nedenini (yayın gecikmesi, serinin başlangıç tarihi) kontrol edin."],
  outliers: ["Outlier", "outlier", "Bilinen bir şokla açıklanıyorsa değeri koruyun ve bir dummy feature ekleyin; açıklanamıyorsa kaynakla karşılaştırın."],
  structural_break: ["Structural break", "structural_break", "Structural break sonrasını ayrı değerlendirin ya da modele bir rejim dummy'si ekleyin."],
  collinearity: ["Yüksek korelasyon", "collinearity", "İki feature'dan yalnızca birini kullanmayı değerlendirin."],
  constant: ["Sabit seri", null, "Seri kodunu kontrol edin; sabit seri modele bilgi taşımaz."],
  empty: ["Boş seri", null, "Seri kodunu ve tarih aralığını kontrol edin."],
  heavy_tails: ["Heavy tail", "heavy_tail", "Şok dönemleri belirgin; robust (medyan tabanlı) ölçüler ya da winsorizing kullanmayı değerlendirin."],
  nonstationary: ["Non-stationary", "stationarity", "Modelde düzey yerine fark ya da yıllık değişim kullanmayı değerlendirin."],
  managed_path: ["Kontrollü seyir", null, "Geçmiş volatilite ani sıçrama riskini olduğundan düşük gösterebilir; feature'ı yorumlarken geçmiş structural break dönemlerini de dikkate alın."],
};
const SEVERITY = { high: ["crit", "Kritik"], medium: ["warn", "Uyarı"], info: ["none", "Bilgi"] };
const STATIONARITY = { stationary: "Stationary", unit_root: "Non-stationary (unit root)", break_suspected: "Structural break şüphesi", inconclusive: "Belirsiz" };
const WORST = { crit: "Giderilmeli", warn: "İncelenmeli", ok: "Sorun yok" };

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
    const top = Object.entries(checkCounts).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([c, n]) => `${CHECKS[c]?.[0] || "Diğer"} (${n})`);
    const clean = q.series.filter((s) => worst(byFeature.get(s.feature) || []) === "ok").length;
    const cleanText = clean ? `${clean} feature'da sorun bulunmadı.` : "Bulgu içermeyen feature yok.";

    const detail = h("div", { class: "stack" });
    const showFeature = (f) => {
      const s = q.series.find((x) => x.feature === f);
      const i = info[f];
      const list = (byFeature.get(f) || []);
      fill(detail,
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, i?.label || f), h("span", { class: "code" }, f)), status(worst(list), WORST[worst(list)])),
        q.columns[f] ? lineChart({
          dates: q.dates, series: [{ name: i?.label || f, values: q.columns[f], color: "var(--series-1)" }],
          markers: { name: "Outlier", dates: s.outliers, color: "var(--series-2)" },
          events: s.breaks.map((b) => ({ date: b.date, label: b.event || "Structural break" })), height: 240, format: (x) => valueText(i, x),
        }) : null,
        list.length
          ? h("div", { class: "feature-list" }, list.map((it) => h("div", { class: "feature-row" },
            h("h3", {}, CHECKS[it.check]?.[0] || "Diğer bulgu"), status(...SEVERITY[it.severity]),
            h("p", {}, it.message), h("p", {}, h("strong", {}, "Öneri: "), CHECKS[it.check]?.[2] || "Bulguyu kaynakla karşılaştırarak inceleyin."))))
          : h("p", { class: "muted" }, "Bu feature'da bulgu yok."),
        h("p", { class: "caption" }, "Turuncu noktalar outlier'ları, kesikli dikey çizgiler structural break'leri gösterir. Bilinen bir şokla eşleşen structural break'lerin yanında olayın adı yazılır."));
    };

    let filter = "all";
    const issueBox = h("div");
    const drawIssues = () => {
      const rows = q.issues.filter((i) => filter === "all" || i.severity === filter);
      issueBox.replaceChildren(rows.length ? table([
        { label: "Önem", render: (i) => status(...SEVERITY[i.severity]) },
        { label: "Feature", render: (i) => h("span", {}, i.feature.split(" ~ ").map((f) => labelOf(m, f)).join(" ve "), h("span", { class: "code" }, i.feature)) },
        { label: "Denetim", render: (i) => CHECKS[i.check]?.[0] || "Diğer" },
        { label: "Bulgu", key: "message" },
        { label: "Öneri", render: (i) => CHECKS[i.check]?.[2] || "Bulguyu kaynakla karşılaştırarak inceleyin." },
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
      { label: "Durum", render: (s) => { const w = worst(byFeature.get(s.feature) || []); return status(w, WORST[w]); } },
      { label: "Son gözlem", render: (s) => monthName(s.last) },
      { label: term("missing"), num: true, render: (s) => ratioPct(s.missing_ratio) },
      { label: term("outlier"), num: true, render: (s) => s.outliers.length },
      { label: term("structural_break"), num: true, render: (s) => s.breaks.length },
      { label: term("stationarity"), render: (s) => STATIONARITY[diag.get(s.feature)?.stationarity] || "—" },
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
        [h("strong", {}, `${q.series.length} feature'da ${q.issues.length} bulgu var: ${q.counts.high ?? 0} kritik, ${q.counts.medium ?? 0} uyarı, ${q.counts.info ?? 0} bilgi.`)],
        q.counts.high ? "Kritik bulgular modellemeden önce giderilmelidir." : "Modellemeyi engelleyen kritik bulgu yok.",
        top.length ? `En sık bulgular: ${top.join(", ")}. Bu bulgular Türkiye'de çoğunlukla 2018 ve 2021 kur şoklarına ve 2023 sonrası parasal sıkılaştırmaya denk gelir; eşleşmeyi aşağıdaki grafiklerden kontrol edin. ${cleanText}` : cleanText),
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
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, term("collinearity", "Yüksek korelasyonlu feature çiftleri")), h("p", {}, "Aynı bilgiyi taşıyan çiftlerde modelde birini kullanmak yeterlidir."))),
        table([
          { label: "Feature A", render: (p) => featureTitle(m, p.a) },
          { label: "Feature B", render: (p) => featureTitle(m, p.b) },
          { label: "Korelasyon", num: true, render: (p) => num(p.corr, 2) },
          { label: "Not", render: (p) => p.note || "Birini çıkarmayı değerlendirin." },
        ], pairs)) : null,
      todo("Panele eklenemeyen feature'lar", Object.entries(q.skipped_features).map(([f, r]) => skippedAdvice(m, f, r))),
      stampBar({ sources: sourcesOf(m), generated: stampTime(q.generated_at), period: q.dates.length ? `${monthName(q.dates[0])} – ${monthName(q.dates.at(-1))}` : "—", missing: Object.keys(q.skipped_features).length ? `${Object.keys(q.skipped_features).length} feature denetlenemedi` : "yok" }),
    );
  };

  run.addEventListener("click", () => busy(run, "Denetleniyor", async () => {
    const q = await postJSON("/api/audit", scope());
    state.audits.set(scopeKey(), q);
    render(q);
  }, out));

  fill(root,
    viewHead("Adım 3", `${m.sector.name}: veri kalitesi`, "Feature'lar modellemeden önce denetlenir: stale seriler, missing value'lar, outlier'lar, structural break'ler ve yüksek korelasyonlu feature çiftleri."),
    h("section", { class: "card" }, h("div", { class: "button-row" }, run,
      h("span", { class: "caption" }, "Bulgular üç düzeydedir: kritik (modellemeden önce giderilmeli), uyarı (incelenmeli), bilgi (model tercihlerini etkiler)."))),
    out,
  );
  const cached = state.audits.get(scopeKey());
  if (cached) render(cached);
  else out.append(emptyState("Denetim henüz çalıştırılmadı", "\"Denetimi başlat\" düğmesine basın. Panel gerekirse otomatik oluşturulur."));
};

views.saglik = async (root) => {
  const run = h("button", { type: "button" }, "Kaynakları denetle");
  const out = h("div", { class: "stack" });
  run.addEventListener("click", () => busy(run, "Denetleniyor", async () => {
    const rows = await getJSON("/api/health");
    const kind = { ok: ["ok", "Çalışıyor"], fail: ["crit", "Hata"], skipped: ["none", "Atlandı"] };
    const ok = rows.filter((r) => r.status === "ok").length;
    fill(out,
      summary([h("strong", {}, ok === rows.length ? `${rows.length} kaynağın tamamı şu anda veri döndürüyor.` : `${rows.length} kaynağın ${ok} tanesi şu anda veri döndürüyor.`)],
        rows.some((r) => r.status === "skipped") ? "Atlanan kaynaklar ya otomatik çekim desteği olmayan ya da kimlik bilgisi (API anahtarı, kullanıcı hesabı) tanımlanmamış kaynaklardır." : null),
      h("section", { class: "card" }, table([
        { label: "Kaynak", render: (r) => h("code", { class: "pill" }, r.source_id) },
        { label: "Durum", render: (r) => status(...(kind[r.status] || ["none", r.status])) },
        { label: "Ayrıntı", key: "detail" },
        { label: "Kontrol serileri", render: (r) => r.checked.join(", ") || "—" },
      ], rows)));
  }, out));
  fill(root,
    viewHead(null, "Kaynak durumu", "Her kaynağın kontrol serilerinden son gözlem çekilerek kaynağa erişilip erişilemediği denetlenir (healthcheck). EVDS için API anahtarı .env dosyasında EVDS_API_KEY olarak tanımlanmalıdır."),
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
  let mode = saved.mode || "brand"; // "brand": brand search · "table": user table · "source": catalog series with entity rows

  const select = (label, value, options) => h("select", { "aria-label": label }, options.map(([v, l]) => h("option", { value: v, selected: v === value }, l)));
  const measure = select("Değer türü", opts.measure, [["value", "Tutar ya da adet"], ["share", "Yüzde pay"]]);
  const kind = select("Değerin niteliği", opts.kind, [["flow", "Akım (satış, prim)"], ["stock", "Stok (abone, mevduat)"]]);
  const basis = select("Pay tabanı", opts.basis, [["auto", "Otomatik"], ["period", "Dönemlik"], ["ttm", "TTM (son 12 ay toplamı)"]]);
  const unit = h("input", { type: "text", value: opts.unit, maxlength: 40, placeholder: "ör. adet, TL, abone", "aria-label": "Birim" });
  const years = h("input", { type: "number", min: 1, max: 10, value: opts.trend_years, "aria-label": "Trend penceresi (yıl)" });
  years.style.width = "90px";
  const focus = h("select", { "aria-label": "Odak oyuncu" });
  const file = h("input", { type: "file", accept: ".csv,.json,text/csv,application/json", "aria-label": "Pazar payı tablosu dosyası" });
  const paste = h("textarea", { spellcheck: "false", "aria-label": "Pazar payı tablosunu yapıştırın", placeholder: "entity,date,value,segment\nMarka A,2024-01,1520,Binek\nMarka B,2024-01,980,Binek\nTOPLAM,2024-01,3100,Binek" });
  const inputStatus = h("p", { class: "input-status", role: "status" });
  const run = h("button", { type: "button" }, "Analiz et");
  const sample = h("button", { type: "button", class: "ghost" }, "Örnek veriyi yükle");
  const out = h("div", { class: "stack" });
  const modeBrand = h("button", { type: "button", class: "ghost small" }, "Marka ara");
  const modeTable = h("button", { type: "button", class: "ghost small" }, "Kendi tablom");
  const modeSource = h("button", { type: "button", class: "ghost small" }, "Kaynaktan çek");
  const brandInput = h("input", { type: "search", maxlength: 80, value: saved.brand?.query || "", placeholder: "ör. Togg, Garanti, Anadolu Sigorta, Turkcell, Opet", "aria-label": "Marka ya da şirket adı" });
  const brandRun = h("button", { type: "submit" }, "Raporu hazırla");
  const brandRefresh = h("input", { type: "checkbox" });
  const brandBox = h("form", { class: "file-pick" },
    h("div", { class: "controls" }, h("label", { class: "field" }, h("span", {}, "Marka ya da şirket adı"), brandInput)),
    h("label", { class: "inline-check" }, brandRefresh, "Önbelleği yok say ve kaynaktan yeniden çek"),
    h("div", { class: "button-row" }, brandRun),
    h("p", { class: "caption" }, "Ad, oyuncu düzeyinde veri veren kaynaklarda aranır: ODMD (otomotiv), TBB ve BDDK (bankacılık), TSB (sigorta), EPDK (akaryakıt ve LPG), BTK (telekom). Dönem son beş yıldır (BTK'da üç yıl); üst çubuktaki aylar bu ekranda kullanılmaz. İlk arama kaynakları okuduğu için birkaç dakika sürebilir; sonraki aramalar yerel önbelleği kullanır."));
  const series = h("select", { "aria-label": "Kaynak serisi" }, h("option", { value: "" }, "Yükleniyor"));
  series.disabled = true;
  const refresh = h("input", { type: "checkbox" });
  const sourceNote = h("p", { class: "caption" });
  const tableOnly = [];
  const field = (label, control, onlyTable = false) => {
    const el = h("label", { class: "field" }, h("span", {}, label), control);
    if (onlyTable) tableOnly.push(el);
    return el;
  };

  const sourcePick = h("div", { class: "file-pick" },
    h("div", { class: "controls" }, field("Kaynak serisi", series)),
    h("label", { class: "inline-check" }, refresh, "Önbelleği yok say ve kaynaktan yeniden çek"),
    sourceNote);
  const describeSeries = () => {
    const [source] = series.value.split("|");
    const s = (state.shareSeries || []).find((x) => x.source === source);
    sourceNote.replaceChildren(s
      ? `${s.name}. Dönem, üst çubuktaki başlangıç ve bitiş aylarıdır. Bazı kaynaklarda her ay ayrı istekle çekildiği için ilk çekim birkaç dakika sürebilir; sonraki çalıştırmalar yerel önbelleği kullanır. Kullanım koşulu: ${s.license}`
      : "");
  };
  const loadShareSeries = async (chosen) => {
    try {
      state.shareSeries ??= await getJSON("/api/share-series");
    } catch (err) { notice(err.message); return; }
    const groups = state.shareSeries;
    if (!groups.length) {
      series.replaceChildren(h("option", { value: "" }, "Otomatik çekilebilen kaynak yok"));
      return;
    }
    series.replaceChildren(...groups.map((g) => h("optgroup", { label: g.name },
      g.series.map((s) => h("option", { value: `${g.source}|${s.code}`, selected: `${g.source}|${s.code}` === chosen }, s.name)))));
    series.disabled = false;
    describeSeries();
  };

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

  const analyzeSource = async () => {
    const [source, code] = series.value.split("|");
    if (!code) throw new Error("Önce bir kaynak serisi seçin.");
    const { start, end } = scope();
    const opts = { basis: basis.value, trend_years: Number(years.value) };
    const body = { source, series: code, start, end, focus: focus.value || undefined, refresh: refresh.checked, ...opts };
    const r = await postJSON("/api/market-share/source", body);
    refresh.checked = false;
    const src = { kind: "source", series: series.value, start, end };
    state.share = { ...state.share, mode, sourceInput: src, opts: { ...state.share?.opts, ...opts }, result: r };
    fillFocus(r.players, r.meta.focus);
    render(r, src, opts);
  };

  const analyze = (button) => busy(button, "Hesaplanıyor", async () => {
    if (mode === "source") return analyzeSource();
    if (!input) throw new Error("Önce bir dosya seçin, tabloyu yapıştırın ya da örnek veriyi yükleyin.");
    if (byteSize(input.text) > MAX_TABLE_BYTES) throw new Error("Tablo 8 MB sınırını aşıyor.");
    const body = {
      text: input.text, format: tableFormat(input.text, input.name), focus: focus.value || undefined,
      measure: measure.value, kind: kind.value, basis: basis.value, unit: unit.value.trim(), trend_years: Number(years.value),
    };
    const r = await postJSON("/api/market-share", body);
    const { text, format, focus: _f, ...rest } = body;
    state.share = { ...state.share, mode, input, opts: rest, result: r };
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

  // brand flow: name → candidates → report; a name that no source lists gets its limits, never a share
  const sectorLabel = (id) => state.sectors.find((s) => s.id === id)?.name || id;
  const brandRequest = () => ({ query: state.share.brand.query, choice: state.share.brand.choice, search_interest: state.share.brand.result?.search_interest?.status === "done" });

  const askBrand = async (query, choice, searchInterest = false) => {
    const r = await postJSON("/api/brand/analyze", { query, choice, refresh: brandRefresh.checked, search_interest: searchInterest });
    brandRefresh.checked = false;
    state.share = { ...state.share, mode, brand: { query, choice, result: r, features: null } };
    showBrand(r);
  };

  const showBrand = (r) => {
    if (r.status === "resolved") renderBrand(r);
    else if (r.status === "ambiguous") renderCandidates(r);
    else if (r.status === "not_found") renderLimits(r);
    else fill(out, emptyState("Pay hesaplanamadı", r.message), evidenceCard(r.evidence), searchCard(r));
  };

  // Google Trends block: opt-in (BigQuery queries), kept apart from computed shares and published findings
  const searchMessage = (r) => {
    const s = r.search_interest;
    const run = h("button", { type: "button", class: "ghost" }, "Arama ilgisini getir");
    run.addEventListener("click", () => busy(run, "BigQuery sorgulanıyor", () => askBrand(r.query, state.share.brand.choice, true), out));
    return [h("p", {}, s.message), h("div", { class: "button-row" }, run)];
  };

  const searchTerms = (s) => {
    const terms = s.terms.filter((t) => t.status !== "not_found");
    const missing = s.terms.filter((t) => t.status === "not_found").map((t) => t.label);
    return [
      terms.length ? table([
        { label: "Tür", key: "mode_label" },
        { label: "Terim", key: "label" },
        { label: "Durum", key: "status_label" },
        { label: "Liste günü", num: true, render: (t) => nf(0).format(t.list_days) },
        { label: "En iyi sıra", num: true, render: (t) => (t.best_rank === null ? "—" : nf(0).format(t.best_rank)) },
        { label: "Listedeki terimler", render: (t) => [...t.terms, ...t.related_terms].slice(0, 3).join(", ") },
        { label: "Kendi geçmişine göre", num: true, render: (t) => (t.interest?.momentum == null ? "—" : `${nf(2).format(t.interest.momentum)} kat`) },
      ], terms) : h("p", {}, "Aranan terimlerin hiçbiri listelere girmemiş."),
      missing.length ? h("p", { class: "caption" }, `Listede yok: ${missing.join(", ")}. Listede olmamak terimin aranmadığı anlamına gelmez.`) : null,
    ];
  };

  // local copy of the Trends tables: asked after a search-interest run, every step is the user's click
  const mirrorPanel = (r) => {
    const box = h("div", { class: "stack" });
    const ask = (action) => postJSON("/api/trends-mirror", { action });
    const button = (label, working, fn, cls = "ghost") => {
      const b = h("button", { type: "button", class: cls }, label);
      b.addEventListener("click", () => busy(b, working, fn));
      return b;
    };
    const days = (m) => `${nf(0).format(m.days)} gün (${m.first_day} – ${m.last_day}), ${nf(1).format(m.disk_mb)} MB`;
    const show = (m, note = null) => {
      const noteLine = note ? h("p", { class: "caption" }, note) : null;
      if (m.state === "on") {
        const askDelete = h("button", { type: "button", class: "ghost" }, "Kapat ve sil");
        askDelete.addEventListener("click", () => fill(box,
          h("p", {}, `Yerel kopya silinecek: ${days(m)}. 30 günden eski listeler BigQuery'den geri alınamaz.`),
          h("div", { class: "button-row" },
            button("Evet, sil", "Siliniyor", async () => show(await ask("disable"), "Yerel kopya kapatıldı ve silindi.")),
            button("Vazgeç", "Hazırlanıyor", async () => show(m)))));
        fill(box, h("p", {}, `Yerel kopya açık: ${days(m)}.`), noteLine,
          h("div", { class: "button-row" }, button("Güncelle", "Eksik günler alınıyor", async () => {
            const u = await ask("update");
            show(u, `${nf(0).format(u.days_added)} yeni gün eklendi; BigQuery ${nf(2).format(u.gib)} GiB okudu.`);
          }), askDelete));
      } else if (m.state === "declined") {
        fill(box, h("p", { class: "muted" }, "Yerel kopya kapalı; tercihiniz kaydedildi."), noteLine,
          h("div", { class: "button-row" }, button("Yerel kopya seçeneğini göster", "Ölçülüyor", async () => show({ ...(await ask("status")), state: "off" }))));
      } else {
        const later = button("Şimdi değil", "Kaydediliyor", async () => show(await ask("decline")));
        fill(box, h("h3", {}, "Yerel kopya oluşturulsun mu?"), h("p", {}, m.offer), noteLine,
          h("div", { class: "button-row" },
            button("Yerel kopya oluştur", "Kopyalanıyor", async () => {
              later.disabled = true;
              try {
                const u = await ask("enable");
                show(u, `Kopya hazır: ${nf(0).format(u.days_added)} gün eklendi, BigQuery ${nf(2).format(u.gib)} GiB okudu.`);
              } finally { later.disabled = false; }
            }, ""),
            later));
      }
    };
    fill(box, h("p", { class: "muted" }, "Yerel kopya durumu okunuyor."));
    ask("status").then((m) => show(m)).catch((err) => fill(box, h("p", { class: "muted" }, err.message)));
    return box;
  };

  const searchCard = (r) => {
    const s = r.search_interest;
    if (!s) return null;
    const done = s.status === "done";
    const listed = done ? s.ranking.filter((x) => x.rank !== null) : [];
    return h("section", { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Arama ilgisi"), h("p", {}, s.note))),
      done ? [
        h("p", {}, `Google Trends Türkiye listeleri, ${s.coverage.first_day} – ${s.coverage.last_day} (${nf(0).format(s.coverage.list_days)} günlük liste). ${s.data_source.label}`),
        ...searchTerms(s),
        listed.length ? h("p", {}, `Listelerde görünme sırası: ${listed.map((x) => `${x.rank}. ${x.label} (${nf(0).format(x.list_days)} gün)`).join(", ")}.`) : null,
        s.provinces ? h("p", {}, `${r.brand?.name || s.brand} için ${nf(0).format(s.provinces.provinces_with_data)} ilde veri var. ${s.provinces.note}`) : null,
        h("p", { class: "caption" }, s.withheld.arama_payi),
        h("p", { class: "caption" }, `${s.attribution}. BigQuery: ${nf(2).format(s.cost.gib)} GiB, ${nf(0).format(s.cost.queries)} sorgu.`),
        mirrorPanel(r),
      ] : searchMessage(r));
  };

  const candidateList = (matches, query) => h("ul", { class: "candidates" }, matches.map((m) => {
    const pick = h("button", { type: "button", class: "ghost" }, h("strong", {}, m.name), ` · ${sectorLabel(m.sector)} · ${m.source.toUpperCase()}`);
    pick.addEventListener("click", () => busy(pick, "Hazırlanıyor", () => askBrand(query, m.id), out));
    return h("li", {}, pick);
  }));

  const renderCandidates = (r) => fill(out,
    h("section", { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Hangisini kastettiniz?"), h("p", {}, r.message))),
      candidateList(r.matches, r.query),
      r.total > r.matches.length ? h("p", { class: "caption" }, `Toplam ${r.total} aday var; ilk ${r.matches.length} tanesi gösteriliyor. Adı daraltarak yeniden arayın.`) : null));

  // Published findings: claims copied from web pages. They stay in their own card; no tile, chart or feature uses them.
  // The URLs come from the web: only https links are rendered, and the text goes through textContent like everything else.
  const safeLink = (url, text) => (/^https:\/\//.test(url) ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, text) : text);
  const scopeText = (e) => [e.basis_label, e.segment, e.geography === "Türkiye" ? null : e.geography].filter(Boolean).join(" · ");
  const evidenceTable = (rows) => table([
    { label: "Bulgu", render: (e) => h("strong", {}, e.claim_text) },
    { label: "Dönem", key: "period" },
    { label: "Ölçü ve kapsam", render: scopeText },
    { label: "Kaynak türü", render: (e) => (e.second_hand ? `${e.tier_label} · ikinci el` : e.tier_label) },
    { label: "Yayıncı", render: (e) => h("span", {}, safeLink(e.url, e.publisher), e.original_source ? ` (özgün kaynak: ${e.original_source})` : "") },
    { label: "Alıntı", render: (e) => e.quote || "—" },
    { label: "Yayım", key: "published" },
  ], rows);

  // Opt-in web search: the server runs the user's own Claude Code; this box starts it, follows it and refreshes the report.
  let refreshedJob = null;
  const webSearch = (name, sector) => {
    const line = h("p", { class: "caption", "aria-live": "polite" });
    const run = h("button", { type: "button", class: "ghost small" }, "Web'de kaynak ara");
    const stop = h("button", { type: "button", class: "ghost small", hidden: true }, "Durdur");
    const report = h("details", { class: "about", hidden: true }, h("summary", {}, "Agent'ın raporu"));
    const show = (j) => {
      run.hidden = j.running;
      stop.hidden = !j.running;
      if (j.status === "idle" || j.brand !== name) { line.textContent = ""; return; }
      line.textContent = `${j.label}: ${nf(0).format(j.searches)} arama, ${nf(0).format(j.pages)} sayfa, ${nf(0).format(j.records)} kayıt.${j.message ? ` ${j.message}` : ""}`;
      if (j.report) {
        report.hidden = false;
        report.replaceChildren(report.firstChild, h("p", { class: "report-text" }, j.report));
      }
    };
    const follow = async () => {
      if (!run.isConnected) return; // the screen changed; the job goes on and the next report shows its records
      let j;
      try { j = await getJSON("/api/research/status"); } catch (e) { line.textContent = e.message; return; }
      show(j);
      if (j.running) { setTimeout(follow, 2500); return; }
      if (j.brand === name && j.id !== refreshedJob && j.status !== "idle") {
        refreshedJob = j.id;
        const b = state.share.brand;
        await askBrand(b.query, b.choice);
      }
    };
    run.addEventListener("click", async () => {
      notice("");
      run.disabled = true;
      try { show(await postJSON("/api/research/start", { query: name, sector })); setTimeout(follow, 1500); }
      catch (e) { line.textContent = e.message; }
      run.disabled = false;
    });
    stop.addEventListener("click", async () => {
      try { show(await postJSON("/api/research/cancel", {})); } catch (e) { line.textContent = e.message; }
    });
    setTimeout(follow, 0); // a search started earlier keeps being followed after a reload
    return h("div", {},
      h("div", { class: "button-row" }, run, stop),
      h("p", { class: "caption" }, "Arama bu bilgisayardaki Claude Code'u kendi planınızla çalıştırır, birkaç dakika sürer ve siz başlatmadan çalışmaz. API key gerekmez."),
      line, report);
  };

  const evidenceCard = (ev, name, sector) => {
    if (!ev) return null;
    const hasAny = ev.current.length + ev.historical.length > 0;
    return h("section", { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Yayımlanmış pay bulguları"), h("p", {}, `Bu bölüm ${ev.label}. Rakamlar web sayfalarından aktarılmıştır; hesaplanmış paylarla birleştirilmez, feature'lara girmez.`))),
      ev.current.length ? evidenceTable(ev.current) : h("p", { class: "muted" }, hasAny ? "Güncel bulgu yok." : "Kayıtlı bulgu yok."),
      ev.single_source ? h("p", { class: "caption" }, "Bulgular tek bir bağımsız kaynağa dayanıyor.") : null,
      ev.contradictions.length ? h("div", {}, h("h3", {}, "Çelişen bulgular"), h("ul", {}, ev.contradictions.map((c) => h("li", {}, c.message)))) : null,
      ev.comparison.length ? h("div", {},
        h("h3", {}, "Hesaplanan pay ile karşılaştırma"),
        table([
          { label: "Hesaplanan", render: (c) => `${c.computed_text} · ${c.computed_period}` },
          { label: "Seri", key: "series_name" },
          { label: "Bulgu", render: (c) => `${c.evidence_text} · ${c.evidence_period} · ${c.evidence_basis}` },
          { label: "Yayıncı", render: (c) => safeLink(c.url, c.publisher) },
          { label: "Fark", num: true, render: (c) => (c.claim === "share" ? ppText(c.difference, 1) : `${plainSigned(c.difference, 0)} sıra`) },
          { label: "Olası neden", render: (c) => h("ul", { class: "reasons" }, c.reasons.map((x) => h("li", {}, x))) },
        ], ev.comparison)) : null,
      ev.historical.length ? h("details", { class: "about" }, h("summary", {}, `Eski bulgular (${ev.historical.length}): dönemi ${nf(0).format(ev.max_age_days)} günden eski`), evidenceTable(ev.historical)) : null,
      hasAny ? h("p", { class: "caption" }, ev.note) : null,
      webSearch(name, sector));
  };

  // A group share is a computed sum of the brands of the source; every membership shows the document behind it.
  const groupCard = (g) => (g ? h("section", { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, `${g.name}: ${g.label}`), h("p", {}, "Grup payı kaynakta yayımlanmaz; aşağıdaki markaların satışları toplanarak hesaplanır."))),
    table([
      { label: "Marka", render: (m) => (m.included ? m.name : `${m.name} (bu dönemde üye değil)`) },
      { label: "Pay", num: true, render: (m) => sharePct(m.share, 2) },
      { label: "Sıra", num: true, render: (m) => m.rank ?? "—" },
      { label: "Bir yıllık değişim", num: true, render: (m) => ppText(m.d_year_pp, 2) },
      { label: "Üyelik", render: (m) => (m.from ? `${m.from} tarihinden itibaren` : "başlangıcı doğrulanamadı") },
      { label: "Dayanak", render: (m) => h("span", {}, safeLink(m.url, "belge"), ` · ${m.checked}`, m.note ? ` · ${m.note}` : "") },
    ], g.members),
    h("ul", {}, g.notes.map((n) => h("li", {}, n)))) : null);

  const renderLimits = (r) => {
    const toPanel = (sector) => {
      const go = h("button", { type: "button", class: "ghost small" }, `${sectorLabel(sector)} paneline git`);
      go.addEventListener("click", () => { $("#sector").value = sector; $("#sector").dispatchEvent(new Event("change")); location.hash = "#panel"; });
      return go;
    };
    const toTable = h("button", { type: "button", class: "ghost small" }, "Kendi tablomu yükle");
    toTable.addEventListener("click", () => setMode("table"));
    const found = r.evidence && r.evidence.current.length + r.evidence.historical.length > 0;
    fill(out,
      found ? evidenceCard(r.evidence) : null,
      searchCard(r),
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Oyuncu düzeyinde veri yok"), h("p", {}, r.message))),
        r.sector_candidates.length
          ? h("ul", {}, r.sector_candidates.map((s) => h("li", {}, h("strong", {}, `${s.name}: `), `${s.reason}.`)))
          : h("p", { class: "muted" }, "Addan sektör çıkarılamadı; sol menüden bir sektör seçerek makro feature panelini hazırlayabilirsiniz."),
        h("h3", {}, "Bunun yerine kullanılabilecekler"),
        h("ul", {}, r.available.map((a) => h("li", {}, a.message, a.series ? ` Seri kodu: ${a.series}.` : ""))),
        h("div", { class: "button-row" }, r.sector_candidates.map((s) => toPanel(s.sector)), toTable)),
      r.others.length ? h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Adı benzeyen oyuncular"), h("p", {}, "Aradığınız marka bunlardan biriyse seçin."))),
        candidateList(r.others, r.query)) : null);
  };

  const brandFeatures = (r) => {
    const body = h("div", { class: "stack" });
    const prepare = h("button", { type: "button" }, "Feature panelini hazırla");
    const csv = h("button", { type: "button", class: "ghost" }, "CSV indir");
    const parquet = h("button", { type: "button", class: "ghost" }, "Parquet indir");
    const exportAs = (format, btn) => busy(btn, "Hazırlanıyor", async () => {
      const res = await api("/api/brand/export", { ...brandRequest(), format });
      const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") || "")?.[1] || `marka_panel.${format}`;
      downloadBlob(await res.blob(), name);
    });
    csv.addEventListener("click", () => exportAs("csv", csv));
    parquet.addEventListener("click", () => exportAs("parquet", parquet));
    const show = (f) => fill(body,
      h("p", {}, `${monthName(f.first)} – ${monthName(f.last)} · ${nf(0).format(f.months)} ay · ${nf(0).format(f.columns)} kolon. Marka kolonlarının yanında ${nf(0).format(f.macro_columns)} makro feature ve bunların lag, rolling ve momentum türevleri bulunur.`),
      table([{ label: "Kolon", render: (c) => h("code", {}, c.name) }, { label: "Tanım", key: "description" }], f.brand_columns),
      f.notes.map((n) => h("p", { class: "caption" }, n)),
      f.withheld.length ? h("p", { class: "caption" }, `Kullanım koşulları saklamaya izin vermediği için ${nf(0).format(f.withheld.length)} kolon dosyaya yazılmadı.`) : null,
      h("div", { class: "button-row" }, csv, parquet));
    prepare.addEventListener("click", () => busy(prepare, "Hazırlanıyor", async () => {
      const f = await postJSON("/api/brand/features", brandRequest());
      state.share.brand.features = f;
      show(f);
    }, body));
    if (state.share.brand.features) show(state.share.brand.features);
    else fill(body, h("p", { class: "muted" }, "Pay, pay değişimi, sıra, lider farkı, pazara göre büyüme ve rakip payı aylık ve point-in-time olarak hazırlanır; sektörün makro feature'ları eklenir."), h("div", { class: "button-row" }, prepare));
    return h("section", { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "ML'e hazır feature'lar"), h("p", {}, `${r.brand.name} için aylık panel. Her satır yalnızca o ayın sonuna kadar yayımlanmış veriyi içerir.`))),
      body);
  };

  const renderBrand = (r) => {
    const L = r.labels;
    const p = r.position;
    const conc = r.concentration;
    const trendText = (s) => L.trend[s.trend] || s.trend;
    const seriesName = (code) => (code === "segment" ? "Segment karması" : r.series_names[code] || code);
    const basisText = L.basis[p.basis] || p.basis;
    const period = `${monthName(r.period.first)} – ${monthName(r.period.last)}`;
    const trendKind = { gaining: "ok", losing: "crit", flat: "none", insufficient_data: "none" };
    let k = 0;
    const history = Object.entries(r.share_history.series).map(([name, values]) => {
      const isFocus = name === p.focus;
      const color = SHARE_REST[name] || SHARE_COLORS[k++ % SHARE_COLORS.length];
      return { name, values, color, width: isFocus ? 3.5 : null, dash: SHARE_REST[name] ? "4 4" : null };
    });
    const reasonLines = (c) => {
      const groups = new Map();
      for (const x of c.reasons) groups.set(x.reason, [...(groups.get(x.reason) || []), x.series]);
      const lines = [...groups].map(([reason, codes]) => (codes.length > 1 ? `${reason}: ${codes.length} seride` : `${reason}: ${seriesName(codes[0])}`));
      return lines.length > 4 ? [...lines.slice(0, 4), `${lines.length - 4} gerekçe daha`] : lines;
    };
    const moverList = (rows) => (rows.length
      ? h("ul", {}, rows.map((x) => h("li", {}, h("strong", {}, x.entity), ` ${sharePct(x.share)} · ${ppText(x.d_year_pp, 2)}`)))
      : h("p", { class: "muted" }, "Yakın sıralarda oyuncu yok."));
    const seg = r.segments;
    const ss = seg?.shift_share;
    fill(out,
      summary(
        [h("strong", {}, r.headline)],
        r.is_proxy ? "Bu sonuçlar proxy veriye dayanır; paylar yaklaşık olarak yorumlanmalıdır." : null,
        `Yoğunlaşma: HHI ${num(conc.hhi, 0)} (${L.hhi_class[conc.class]}), ilk üç oyuncu ${sharePct(conc.cr3)}, ilk beş oyuncu ${sharePct(conc.cr5)}.`),
      h("div", { class: "tiles" },
        tile("Pay", sharePct(p.share), `${basisText} · ${monthName(p.last)}`, "Hesap"),
        tile("Sıra", `${p.rank} / ${p.players}`, p.rank_year_ago ? `bir yıl önce ${p.rank_year_ago}. sıra` : "aktif oyuncular arasında", "Hesap"),
        tile("Bir yıllık değişim", ppText(p.d_year_pp, 2), `trend: ${trendText(p)}`, "Hesap"),
        tile("Lidere uzaklık", p.rank === 1 ? "Lider" : `${fx(1).format(p.gap_to_leader_pp)} puan`, p.rank === 1 ? `en yakın rakip: ${p.next_below || "—"}` : `lider: ${p.leader} ${sharePct(p.leader_share)}`, "Hesap"),
        tile("HHI", num(conc.hhi, 0), `yoğunlaşma ${L.hhi_class[conc.class]}`, "Hesap")),
      r.others.length ? h("p", { class: "caption" }, `Adı benzeyen diğer oyuncular: ${r.others.map((m) => m.name).join(", ")}. Aradığınız bunlardan biriyse adı daraltarak yeniden arayın.`) : null,
      groupCard(r.group),
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, `Sektör: ${r.sector.name}`), h("p", {}, r.sector.coverage), r.sector.notes ? h("p", {}, r.sector.notes) : null)),
        table([
          { label: "Seri", render: (s) => s.name },
          { label: "Pay", num: true, render: (s) => sharePct(s.share, 2) },
          { label: "Sıra", num: true, render: (s) => `${s.rank} / ${s.players}` },
          { label: "Bir yıllık değişim", num: true, render: (s) => ppText(s.d_year_pp, 2) },
          { label: term("share_trend", "Trend"), render: (s) => status(trendKind[s.trend] || "none", trendText(s)) },
          { label: "Lider", render: (s) => (s.rank === 1 ? "—" : `${s.leader} ${sharePct(s.leader_share)}`) },
          { label: "Dönem", render: (s) => monthName(s.last) },
        ], r.series),
        Object.keys(r.skipped_series).length ? h("p", { class: "caption" }, `Bu seriler rapora girmedi: ${Object.entries(r.skipped_series).map(([c, why]) => (c === "fetch" ? `kaynak okunamadı (${why})` : `${seriesName(c)} (${why})`)).join("; ")}.`) : null),
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, `${r.brand.name} için rakip seti`), h("p", {}, "Rakipler veriden çıkarılır: pay sıralamasında komşuluk, pazar liderliği, ters yönlü pay değişimi (substitution correlation) ve segment karmasının örtüşmesi. Daha çok seride adı geçen oyuncu üstte yer alır."))),
        r.competitors.length ? table([
          { label: "Oyuncu", key: "entity" },
          { label: "Pay", num: true, render: (c) => sharePct(c.share) },
          { label: term("substitution", "Korelasyon (r)"), num: true, render: (c) => plainSigned(c.r, 2) },
          { label: "Gerekçeler", render: (c) => h("ul", { class: "reasons" }, reasonLines(c).map((x) => h("li", {}, x))) },
        ], r.competitors) : h("p", { class: "muted" }, "Veriden belirgin bir rakip adayı çıkmadı."),
        h("div", { class: "cols" },
          h("div", {}, h("h3", {}, "Yakın sıralarda pay kazananlar"), moverList(r.movers.gainers)),
          h("div", {}, h("h3", {}, "Yakın sıralarda pay kaybedenler"), moverList(r.movers.losers)))),
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, term("market_share", "Pay geçmişi")), h("p", {}, `${seriesName(p.series)} · ${basisText} tabanında yüzde pay. Kalın çizgi ${p.focus}.`))),
        lineChart({ dates: r.share_history.dates, series: history, height: 320, format: (v) => sharePct(v, 1) })),
      seg ? h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Segmentler"), h("p", {}, "Pazarı bölen serilerde ağırlık ve markanın payı."))),
        table([
          { label: "Segment", render: (x) => seriesName(x.segment) },
          { label: "Ağırlık", num: true, render: (x) => sharePct(x.weight) },
          { label: `${r.brand.name} payı`, num: true, render: (x) => sharePct(x.focus_share) },
          { label: "Bir yıllık değişim", num: true, render: (x) => ppText(x.focus_share_d_pp) },
          { label: "Lider", render: (x) => (x.leader ? `${x.leader} (${sharePct(x.leader_share)})` : "—") },
        ], seg.segments),
        ss ? h("p", {}, term("shift_share", "Shift-share"), `: ${monthName(ss.from)} → ${monthName(ss.to)} arasında ${ppText(ss.total_pp, 2)}; ${ppText(ss.share_effect_pp, 2)} segment içi pay, ${ppText(ss.mix_effect_pp, 2)} segment karması etkisi.`) : null) : null,
      evidenceCard(r.evidence),
      searchCard(r),
      brandFeatures(r),
      r.issues.length ? h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Veri notları"), h("p", {}, "Sonucu yorumlarken dikkate alınması gereken durumlar."))),
        h("div", { class: "feature-list" }, r.issues.map((i) => h("div", { class: "feature-row" },
          h("h3", {}, i.message), status(...(SEVERITY[i.severity] || ["none", i.severity])))))) : null,
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Kaynak ve lisans"))),
        r.sources.map((s) => h("p", {}, h("strong", {}, `${s.attribution}. `), s.license))),
      stampBar({ sources: r.sources.map((s) => s.attribution.replace(/^Kaynak:\s*/, "")).join(", "), generated: stampTime(r.generated_at), period, missing: r.issues.some((i) => i.check === "missing_periods") ? "eksik dönem var (Veri notları)" : "yok" }),
    );
  };

  brandBox.addEventListener("submit", (e) => {
    e.preventDefault();
    const query = brandInput.value.trim();
    if (!query) { notice("Bir marka ya da şirket adı yazın."); return; }
    busy(brandRun, "Hazırlanıyor", () => askBrand(query), out);
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
      : h("p", { class: "muted" }, "Bu grupta anlamlı trend gösteren oyuncu yok.");

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
          { label: "Bir yıllık değişim", num: true, render: (x) => ppText(x.focus_share_d_pp) }] : []),
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
        fp ? `${fp.entity} ${sharePct(fp.share)} payla ${fp.rank}. sırada; payı bir yılda ${ppText(fp.d_year_pp)} değişti; trend: ${trendOf(fp)}.` : "Rakip setini ve segment ayrıştırmasını görmek için odak oyuncuyu seçin.",
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
        coverage !== null ? tile("Coverage", sharePct(coverage), "listelenen oyuncular / pazar toplamı", "Hesap") : null),
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
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, term("share_trend", "Pay kazananlar")), h("p", {}, `Son ${o.trend_years} yılda pay trendi anlamlı biçimde pozitif olan oyuncular.`))),
          trendBars(gainers, "+")),
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, term("share_trend", "Pay kaybedenler")), h("p", {}, `Son ${o.trend_years} yılda pay trendi anlamlı biçimde negatif olan oyuncular.`))),
          trendBars(losers, "−"))),
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Oyuncular"), h("p", {}, "Son dönemdeki pay, pay değişimleri ve trend. Trend yılda en az 0,2 puan ve |t| ≥ 2 ise anlamlı sayılır."))),
        table([
          { label: "Sıra", num: true, key: "rank" },
          { label: "Oyuncu", render: (p) => h("span", {}, p.entity, p.entity === meta.focus ? h("span", { class: "chip accent tag-after" }, "odak") : null) },
          { label: "Pay", num: true, render: (p) => sharePct(p.share, 2) },
          { label: "Önceki döneme göre", num: true, render: (p) => ppText(p.d_prev_pp, 2) },
          { label: "Bir yıllık değişim", num: true, render: (p) => ppText(p.d_year_pp, 2) },
          { label: "Trend (puan/yıl)", num: true, render: (p) => (p.trend_pp_per_year === null ? "—" : signed(p.trend_pp_per_year, 2)) },
          { label: "t", num: true, render: (p) => plainSigned(p.trend_t, 1) },
          { label: "Durum", render: (p) => status(trendKind[p.trend] || "none", cap(trendOf(p))) },
          ...(players.some((p) => p.growth_vs_market_pp !== undefined) ? [{ label: "Pazara göre büyüme", num: true, render: (p) => ppText(p.growth_vs_market_pp) }] : []),
        ], players, { scroll: true, rowClass: (p) => (p.entity === meta.focus ? "best" : null) })),
      competitorCard,
      segmentCard,
      r.issues.length ? h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Veri notları"), h("p", {}, "Tablo okunurken yapılan düzeltmeler ve yorumu etkileyen durumlar."))),
        h("div", { class: "feature-list" }, r.issues.map((i) => h("div", { class: "feature-row" },
          h("h3", {}, i.message), status(...(SEVERITY[i.severity] || ["none", i.severity])))))) : null,
      h("details", { class: "card" }, h("summary", {}, "Yöntem ve varsayımlar"),
        h("ul", {},
          h("li", {}, h("strong", {}, "Pay: "), meta.has_total ? "Oyuncu değeri / TOPLAM satırlarındaki pazar toplamı." : "Oyuncu değeri / listelenen oyuncuların toplamı (pazar toplamı verilmedi)."),
          h("li", {}, h("strong", {}, "Taban: "), `${basisText}. ${meta.basis === "ttm" ? "Akım verisinde mevsimselliği gidermek için son 12 ayın toplamı kullanıldı." : "Her dönemin kendi değeri kullanıldı."}`),
          h("li", {}, h("strong", {}, "Yoğunlaşma: "), "HHI = payların karelerinin toplamı; CR3 ve CR5 = en büyük 3 ve 5 oyuncunun payı."),
          h("li", {}, h("strong", {}, "Trend: "), `Son ${o.trend_years} yılın dönemlik paylarına doğrusal trend uydurulur; eğim puan/yıl cinsindendir, anlamlılık t istatistiğiyle sınanır.`),
          h("li", {}, h("strong", {}, "Sınırlamalar: "), src.kind === "source" ? `Sonuçlar yalnızca seçilen kaynak serisini yansıtır (${meta.source.name}). Rakip adayları istatistiksel ipucudur; nitel ölçütlerle doğrulanmalıdır.` : "Sonuçlar yalnızca yüklenen tabloyu yansıtır. Rakip adayları istatistiksel ipucudur; fiyat, kanal ve coğrafya gibi nitel ölçütlerle doğrulanmalıdır."))),
      stampBar({
        sources: src.kind === "source" ? `${meta.source.attribution.replace(/^Kaynak:\s*/, "")} · ${meta.source.name} (${meta.source.series})`
          : src.kind === "sample" ? "Örnek veri (sentetik, otomotiv benzeri)" : src.kind === "file" ? `Kullanıcı tablosu (${src.name})` : "Kullanıcı tablosu (yapıştırıldı)",
        generated: stampTime(r.generated_at), period,
        missing: r.issues.some((i) => i.check === "missing_periods") ? "eksik dönem var (Veri notları)" : "yok",
      }),
    );
  };

  fill(root,
    viewHead("Pazar payı", "Pazar payı ve rakipler", "Bir marka ya da şirket adı yazın: sektörü, sırası, payı, rakipleri ve yoğunlaşması oyuncu düzeyinde veri veren kaynaklardan (ODMD, TBB, BDDK, TSB, EPDK, BTK) çıkarılır. Kendi tablonuzu da yükleyebilirsiniz; yüklenen tablo yalnızca bu bilgisayarda işlenir ve diske yazılmaz."),
    h("section", { class: "card" },
      h("div", { class: "button-row", role: "group", "aria-label": "Veri girişi" }, modeBrand, modeTable, modeSource),
      brandBox,
      sourcePick,
      h("div", { class: "drop" },
        h("div", {},
          h("div", { class: "file-pick" },
            h("strong", {}, "Dosya seçin (CSV ya da JSON)"),
            file,
            h("p", { class: "caption" }, "Sütunlar: entity, date, value ve isteğe bağlı segment. Pazar toplamı, oyuncu adı TOPLAM (ya da TOTAL) olan satırlarla verilir. Dönem: YYYY-AA, YYYY-Qn ya da YYYY. Noktalı virgülle ayrılmış Excel çıktılarında ondalık virgül kabul edilir. En fazla 8 MB."),
            h("div", { class: "button-row" }, sample)),
          inputStatus),
        h("div", {},
          h("label", { class: "field" }, h("span", {}, "ya da tabloyu yapıştırın"), paste))),
      h("div", { class: "controls", "data-share-options": "" },
        field("Değer türü", measure, true),
        field("Değerin niteliği", kind, true),
        field(term("ttm", "Pay tabanı"), basis),
        field("Birim", unit, true),
        field(term("share_trend", "Trend penceresi (yıl)"), years),
        field("Odak oyuncu", focus)),
      h("div", { class: "button-row", "data-share-options": "" }, run,
        h("span", { class: "caption" }, "Odak oyuncuyu değiştirdiğinizde analiz kendiliğinden yenilenir."))),
    out,
  );
  const drop = root.querySelector(".drop");
  const setMode = (next, keepResult = false) => {
    mode = next;
    for (const [b, m] of [[modeBrand, "brand"], [modeTable, "table"], [modeSource, "source"]]) {
      b.classList.toggle("active", m === mode);
      b.setAttribute("aria-pressed", String(m === mode));
    }
    brandBox.hidden = mode !== "brand";
    drop.hidden = mode !== "table";
    sourcePick.hidden = mode !== "source";
    for (const el of tableOnly) el.hidden = mode !== "table";
    root.querySelectorAll("[data-share-options]").forEach((el) => { el.hidden = mode === "brand"; });
    if (!keepResult) {
      fillFocus([]);
      fill(out, emptyState("Analiz henüz yapılmadı", {
        brand: "Bir marka ya da şirket adı yazıp \"Raporu hazırla\" düğmesine basın.",
        source: "Bir kaynak serisi seçip \"Analiz et\" düğmesine basın. Dönem, üst çubuktaki başlangıç ve bitiş aylarıdır.",
        table: "Bir tablo seçip \"Analiz et\" düğmesine basın ya da \"Örnek veriyi yükle\" ile sentetik otomotiv verisini deneyin.",
      }[mode]));
    }
  };
  modeBrand.addEventListener("click", () => {
    if (mode === "brand") return;
    const last = state.share?.brand?.result;
    setMode("brand", Boolean(last));
    if (last) showBrand(last);
  });
  modeTable.addEventListener("click", () => mode !== "table" && setMode("table"));
  modeSource.addEventListener("click", () => mode !== "source" && setMode("source"));
  series.addEventListener("change", () => { fillFocus([]); describeSeries(); });

  const shown = mode === "brand" ? saved.brand?.result : saved.result && saved.mode === mode;
  setMode(mode, Boolean(shown));
  if (shown && mode === "brand") showBrand(saved.brand.result);
  else if (shown) render(saved.result, mode === "source" ? saved.sourceInput : saved.input, { ...opts });
  loadShareSeries(saved.sourceInput?.series);
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
