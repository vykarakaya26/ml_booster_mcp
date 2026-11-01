// ML Booster yerel paneli. Dış kütüphane yok; DOM yalnızca textContent ile doldurulur (innerHTML kullanılmaz).
// Yazım kuralı: önce özet (bulgu cümlesi + KPI kartları), sonra ayrıntı; her ekranın sonunda kaynak/dönem damgası.
import { lineChart, barChart } from "./charts.js";
import { GLOSSARY, MODEL_INFO } from "./glossary.js";

const TOKEN = document.querySelector('meta[name="mlb-token"]').content;
const $ = (sel) => document.querySelector(sel);

// --- DOM yardımcıları --------------------------------------------------------------------------------------

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") el.style.cssText = v; // CSP satır içi style niteliğini engeller; CSSOM serbest
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === undefined || c === null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

// --- biçimlendirme (Türkçe) ---------------------------------------------------------------------------------

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

// Feature değerini birimine göre yazar: log değişim ≈ yüzde, yüzde serileri % ile.
function valueText(info, v) {
  if (v === null || v === undefined) return "—";
  if (!info) return num(v, 3);
  if (info.transform === "log_diff") return `${v >= 0 ? "+" : "−"}%${nf(1).format(Math.abs(v) * 100)}`;
  if (info.transform === "yoy_pct" || info.unit.startsWith("yüzde")) return pctText(v, 2);
  return num(v, 2);
}
// İki dönem arasındaki farkı birimine göre yazar (yüzde serilerinde yüzde puan).
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
  if (out) out.replaceChildren(h("p", { class: "empty" }, h("span", { class: "spinner", "aria-hidden": "true" }), WAIT_TEXT));
  notice(null);
  try {
    return await fn();
  } catch (err) {
    if (out) out.replaceChildren();
    notice(err.message);
    return null;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

// --- bileşenler --------------------------------------------------------------------------------------------

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

// --- sözlük çekmecesi ----------------------------------------------------------------------------------------

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

const state = { meta: null, sectors: [], maps: new Map(), panels: new Map(), backtests: new Map(), audits: new Map() };

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

const FEATURE_STATUS = { hazir: "ok", kesif_bekliyor: "warn", saglayici_yok: "none" };
const FEATURE_STATUS_LABEL = { hazir: "Hazır", kesif_bekliyor: "Seri kodu bekliyor", saglayici_yok: "Kaynak bağlantısı yok" };

// Atlanan feature'lar için kullanıcıya yapılacak iş önerisi.
function skippedAdvice(m, name, reason) {
  const label = labelOf(m, name);
  if (/keşif bekliyor/.test(reason)) return [h("strong", {}, label), ": EVDS seri kodu henüz bağlanmadı. Claude Code'da ", h("em", {}, `"${label.toLocaleLowerCase("tr")} serisini bul"`), " deyin ya da ", h("code", { class: "pill" }, "mlbooster catalog search"), " ile arayıp ", h("code", { class: "pill" }, "catalog set-feature"), " ile bağlayın."];
  if (/sağlayıcısı henüz yok/.test(reason)) return [h("strong", {}, label), ": bu kaynaktan otomatik veri çekme henüz geliştirilmedi (yol haritasında)."];
  if (/Credentials|anahtar/i.test(reason)) return [h("strong", {}, label), ": API anahtarı tanımlı değil. EVDS anahtarını .env dosyasına EVDS_API_KEY olarak ekleyin."];
  return [h("strong", {}, label), `: ${reason}`];
}

// --- görünümler -------------------------------------------------------------------------------------------

const views = {};

views.genel = async (root) => {
  const go = (id, view = "harita") => { $("#sector").value = id; savePrefs({ sector: id }); location.hash = `#${view}`; };
  const total = state.sectors.reduce((a, s) => a + s.ready, 0);
  root.replaceChildren(
    h("section", { class: "hero" },
      h("span", { class: "eyebrow" }, "ML Booster Map"),
      h("h1", {}, "Türkiye'de bir sektör seçin; makine öğrenmesine hazır veri setini kaynağıyla birlikte oluşturun"),
      h("p", {}, "Panel; TCMB EVDS, BIS ve OECD gibi ücretsiz kamu kaynaklarından sektörle ilgili makro göstergeleri çeker, aynı aylık takvimde hizalar, verinin kalitesini denetler ve bir ML modelinin geçmesi gereken tahmin çıtasını hesaplar. Tüm işlem bu bilgisayarda yapılır."),
      h("div", { class: "button-row" },
        h("button", { type: "button", onclick: () => go($("#sector").value) }, `${sectorName()} ile başla`),
        h("button", { type: "button", class: "ghost", onclick: () => openGlossary() }, "Terimler sözlüğü"))),
    h("div", { class: "steps" },
      [["1", "Sektör ve kaynaklar", "Sektör için hangi ücretsiz verilerin olduğunu ve hangilerinin hazır olduğunu görün.", "Çıktı: veri kaynağı haritası", "harita"],
        ["2", "Feature paneli", "Göstergeler aylık takvimde, yayımlandıkları aya göre hizalanır.", "Çıktı: CSV / Parquet tablo", "panel"],
        ["3", "Veri kalitesi", "Durmuş seriler, eksik aylar, aykırı değerler ve kırılmalar denetlenir.", "Çıktı: önceliklendirilmiş bulgu listesi", "kalite"],
        ["4", "Tahmin çıtası", "Basit yöntemler geçmişte sınanır; ML modelinin aşması gereken hata düzeyi belirlenir.", "Çıktı: model karşılaştırması", "model"]]
        .map(([n, t, p, o, v]) => h("a", { class: "step", href: `#${v}` }, h("span", { class: "num" }, n), h("h3", {}, t), h("p", {}, p), h("span", { class: "out" }, o)))),
    h("div", { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Sektörler"), h("p", {}, `${state.sectors.length} sektörde toplam ${total} gösterge hazır. Bir sektör seçerek başlayın.`))),
      h("div", { class: "sector-grid" }, state.sectors.map((s) => {
        const bar = h("i");
        bar.style.width = `${s.features ? (s.ready / s.features) * 100 : 0}%`;
        return h("button", { type: "button", class: "sector-card", onclick: () => go(s.id) },
          h("h3", {}, s.name),
          s.ml_use_cases.length ? h("div", { class: "chips" }, s.ml_use_cases.slice(0, 3).map((u) => h("span", { class: "chip" }, u))) : null,
          h("div", { class: "bar", "aria-hidden": "true" }, bar),
          h("small", {}, `${s.ready} / ${s.features} gösterge hazır · ${s.sources} kaynak`));
      }))),
    h("div", { class: "trust" },
      h("div", {}, h("strong", {}, "Geleceği görmeyen veri"), h("p", {}, "Her değer, gerçekte yayımlandığı aya yazılır. Model, o tarihte bilinmeyen bilgiyle eğitilmez.")),
      h("div", {}, h("strong", {}, "Kaynağı belli"), h("p", {}, "Her gösterge kurum, seri kodu ve yayın gecikmesiyle birlikte gösterilir; lisans notları haritada yer alır.")),
      h("div", {}, h("strong", {}, "Yerel ve açık kaynak"), h("p", {}, "Veriler bu bilgisayarda işlenir, API anahtarı dışarı gönderilmez. Kod GitHub'da açıktır."))),
  );
};

views.harita = async (root) => {
  const m = await sectorMap($("#sector").value);
  const info = m.feature_info;
  const groups = { hazir: [], kesif_bekliyor: [], saglayici_yok: [] };
  m.macro_features.forEach((f) => (groups[f.status] || (groups[f.status] = [])).push(f));
  const ready = groups.hazir.length;
  const sourceNames = [...new Set(groups.hazir.map((f) => info[f.name]?.source_owner).filter(Boolean))];

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
      h("p", {}, f.status === "kesif_bekliyor" ? `Seri kodu bekleniyor. İpucu: ${f.discover_hint}` : i.description));
  };

  root.replaceChildren(
    viewHead("Adım 1", `${m.sector.name}: hangi veriler var?`, "Sektör için tanımlı ücretsiz kaynaklar ve modelde kullanılacak göstergeler. Hazır göstergeler bir sonraki adımda panele dönüştürülür."),
    summary(
      [h("strong", {}, `${m.sector.name} için ${m.macro_features.length} gösterge tanımlı; ${ready} tanesi kullanıma hazır.`),
        sourceNames.length ? ` Hazır göstergeler ${sourceNames.join(", ")} kaynaklı.` : ""],
      groups.kesif_bekliyor.length ? `${groups.kesif_bekliyor.length} göstergenin EVDS seri kodu henüz bağlanmadı; aşağıdaki "Sizden gerekenler" bölümü nasıl tamamlanacağını açıklar.` : "Seri kodu eksik gösterge yok.",
      m.market_share.length ? `Pazar payı için ${m.market_share.length} kaynak tanımlı (${m.market_share.map((s) => s.source.toUpperCase()).join(", ")}); bu kaynaklardan otomatik veri çekme yol haritasında.` : null),
    h("div", { class: "tiles" },
      tile("Hazır gösterge", ready, `toplam ${m.macro_features.length} göstergeden`, "Ölçüm"),
      tile("Seri kodu bekleyen", groups.kesif_bekliyor.length, groups.kesif_bekliyor.length ? "EVDS'de aranıp bağlanmalı" : "eksik yok"),
      tile("Veri kaynağı", m.sources.length, `${m.sources.filter((s) => s.provider_ready).length} kaynaktan otomatik çekim yapılıyor`),
      tile("ML kullanım alanı", m.sector.ml_use_cases.length, m.sector.ml_use_cases.join(", ") || "tanımlı değil")),
    todo("Sizden gerekenler", [...groups.kesif_bekliyor, ...groups.saglayici_yok].map((f) =>
      skippedAdvice(m, f.name, f.status === "kesif_bekliyor" ? "keşif bekliyor" : "sağlayıcısı henüz yok"))),
    h("section", { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Göstergeler"), h("p", {}, "Her göstergenin ne olduğu, hangi dönüşümle panele girdiği ve kaynağı. Mavi etiket, modelde kullanılan biçimi gösterir."))),
      h("div", { class: "feature-list" }, [...groups.hazir, ...groups.kesif_bekliyor, ...groups.saglayici_yok].map(featureRow))),
    h("section", { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Kaynaklar"), h("p", {}, "Rol: makro gösterge, sektör verisi ya da pazar payı. Doğrulama: kaynağın canlı olarak test edilip edilmediği."))),
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
      grid.replaceChildren(...(shown.length ? shown.map(card) : [emptyState("Gösterge seçilmedi", "Yukarıdan en az bir gösterge seçin.")]));
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
    out.replaceChildren(
      summary(
        [h("strong", {}, `Panel ${period} dönemini kapsıyor: ${p.rows} dönem, ${p.features.length} gösterge.`), " Her değer, kaynağın yayımladığı döneme yazıldı; tablo doğrudan model eğitiminde kullanılabilir."],
        skipped.length ? `${skipped.length} gösterge panele eklenemedi; nedenleri ve yapılması gerekenler aşağıda.` : "Tanımlı tüm hazır göstergeler panele eklendi."),
      h("div", { class: "tiles" },
        tile("Kapsanan dönem", p.rows, period, "Hesap"),
        tile("Göstergeler", p.features.length, `${m.macro_features.length} tanımlı göstergeden`, "Ölçüm"),
        tile("Panele girmeyen", skipped.length, skipped.length ? "aşağıda açıklandı" : "yok"),
        tile("Frekans", { M: "Aylık", W: "Haftalık", B: "İş günü" }[p.freq], "dönem sonu takvimi")),
      todo("Sizden gerekenler", skipped.map(([f, r]) => skippedAdvice(m, f, r))),
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Gösterilecek göstergeler"), h("p", {}, "Her gösterge kendi ölçeğinde ayrı grafikte yer alır. Seçiminiz bu tarayıcıda hatırlanır."))),
        picks),
      grid,
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Tablo görünümü: son 12 dönem"), h("p", {}, "Modelin göreceği tablo. Tamamı için CSV ya da Parquet indirin."))),
        table([{ label: "Dönem", render: (r) => monthName(r.date) },
          ...p.features.map((f) => ({ label: h("span", {}, labelOf(m, f), h("span", { class: "code" }, f)), num: true, render: (r) => valueText(info[f], p.columns[f][r.idx]) }))], rows)),
      stampBar({ sources: sourcesOf(m), generated: stampTime(p.generated_at), period, missing: skipped.length ? `${skipped.length} gösterge panele girmedi` : "yok" }),
    );
  };

  run.addEventListener("click", () => busy(run, "Oluşturuluyor", async () => {
    savePrefs({ freq: freq.value });
    const p = await postJSON("/api/panel", { ...scope(), freq: freq.value });
    state.panels.set(scopeKey({ freq: freq.value }), p);
    render(p);
  }, out));

  root.replaceChildren(
    viewHead("Adım 2", `${m.sector.name}: feature paneli`, "Hazır göstergeler aynı takvimde hizalanır. Günlük ve haftalık veriler aylığa indirgenir, çeyreklik veriler sonraki aylara taşınır."),
    h("section", { class: "card" },
      h("div", { class: "controls" },
        h("label", { class: "field" }, h("span", {}, "Frekans"), freq),
        h("div", { class: "button-row" }, run, csv, parquet)),
      h("p", { class: "caption" }, term("point_in_time", "Nokta-zamanlı hizalama"), ": Mart enflasyonu Nisan başında açıklandığı için panelde Nisan satırında yer alır. Böylece model geçmişte henüz bilinmeyen veriyi kullanamaz.")),
    out,
  );
  const cached = state.panels.get(scopeKey({ freq: freq.value }));
  if (cached) render(cached);
  else out.append(emptyState("Panel henüz oluşturulmadı", "\"Paneli oluştur\" düğmesine basın. İlk çalıştırmada veriler kaynaktan çekilir, sonrakiler yerel önbellekten okunur."));
};

const CHECKS = {
  stale: ["Güncelliğini yitirmiş seri", "stale", "Kaynakta serinin güncel kodunu arayın; seri arşive taşınmış ya da kodu değişmiş olabilir."],
  missing: ["Eksik dönem", "missing", "Başlangıç tarihini ileri alın ya da eksik dönemlerin nedenini (yayın gecikmesi, seri başlangıcı) kontrol edin."],
  outliers: ["Aykırı değer", "outlier", "Bilinen bir şokla açıklanıyorsa koruyun ve bir gösterge değişkeni ekleyin; açıklanamıyorsa kaynakla karşılaştırın."],
  structural_break: ["Yapısal kırılma", "structural_break", "Kırılma sonrası dönemi ayrı değerlendirin ya da modele rejim göstergesi ekleyin."],
  collinearity: ["Aşırı korelasyon", "collinearity", "İki göstergeden birini çıkarmayı ya da yalnızca birini kullanmayı değerlendirin."],
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
      detail.replaceChildren(
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, i?.label || f), h("span", { class: "code" }, f)), status(worst(list), { crit: "Aksiyon", warn: "Takip", ok: "Sorun yok" }[worst(list)])),
        q.columns[f] ? lineChart({
          dates: q.dates, series: [{ name: i?.label || f, values: q.columns[f], color: "var(--series-1)" }],
          markers: { name: "Aykırı değer", dates: s.outliers, color: "var(--series-2)" },
          events: s.breaks.map((b) => ({ date: b.date, label: b.event || "Yapısal kırılma" })), height: 240, format: (x) => valueText(i, x),
        }) : null,
        list.length
          ? h("div", { class: "feature-list" }, list.map((it) => h("div", { class: "feature-row" },
            h("h3", {}, CHECKS[it.check]?.[0] || it.check), status(...SEVERITY[it.severity]),
            h("p", {}, it.message), h("p", {}, h("strong", {}, "Ne yapmalı? "), CHECKS[it.check]?.[2] || "İnceleyin."))))
          : h("p", { class: "muted" }, "Bu göstergede bulgu yok."),
        h("p", { class: "caption" }, "Turuncu noktalar aykırı değerleri, kesikli dikey çizgiler yapısal kırılmaları gösterir. Bilinen bir şokla eşleşen kırılmalar adıyla yazılır."));
    };

    let filter = "all";
    const issueBox = h("div");
    const drawIssues = () => {
      const rows = q.issues.filter((i) => filter === "all" || i.severity === filter);
      issueBox.replaceChildren(rows.length ? table([
        { label: "Önem", render: (i) => status(...SEVERITY[i.severity]) },
        { label: "Gösterge", render: (i) => h("span", {}, i.feature.split(" ~ ").map((f) => labelOf(m, f)).join(" ve "), h("span", { class: "code" }, i.feature)) },
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
      { label: "Gösterge", render: (s) => featureTitle(m, s.feature) },
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
    out.replaceChildren(
      summary(
        [h("strong", {}, `Denetim ${q.series.length} göstergede ${q.issues.length} bulgu üretti: ${q.counts.high ?? 0} kritik, ${q.counts.medium ?? 0} uyarı, ${q.counts.info ?? 0} bilgi.`)],
        q.counts.high ? "Kritik bulgular modellemeden önce giderilmelidir." : "Modellemeyi engelleyen kritik bulgu yok.",
        top.length ? `Uyarıların çoğu ${top.join(" ve ")} kaynaklı; Türkiye'de 2018 ve 2021 kur şokları ile 2023 sonrası sıkılaştırma bu tür bulguların sık görülen nedenleri arasındadır; eşleşmeyi aşağıdaki grafiklerden kontrol edin. ${clean} göstergede sorun bulunmadı.` : `${clean} göstergede sorun bulunmadı.`),
      h("div", { class: "tiles" },
        tile("Kritik", q.counts.high ?? 0, "modellemeden önce giderilmeli"),
        tile("Uyarı", q.counts.medium ?? 0, "incelenmeli"),
        tile("Bilgi", q.counts.info ?? 0, "modelleme tercihlerini etkiler"),
        tile("Sorunsuz gösterge", `${clean} / ${q.series.length}`, `değerlendirme tarihi ${dateTR(q.as_of)}`, "Hesap")),
      h("div", { class: "split" },
        h("section", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Gösterge bazında durum"), h("p", {}, "Ayrıntı için bir satıra tıklayın."))), featureTable),
        h("section", { class: "card" }, detail)),
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Tüm bulgular"), h("p", {}, "Önem derecesine göre sıralıdır."))), filters, issueBox),
      pairs.length ? h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, term("collinearity", "Birbirine çok benzeyen göstergeler")), h("p", {}, "Korelasyonu yüksek çiftler. Aynı bilgiyi taşıyorlarsa modelde birini kullanmak yeterlidir."))),
        table([
          { label: "Gösterge A", render: (p) => featureTitle(m, p.a) },
          { label: "Gösterge B", render: (p) => featureTitle(m, p.b) },
          { label: "Korelasyon", num: true, render: (p) => num(p.corr, 2) },
          { label: "Not", render: (p) => p.note || "Birini çıkarmayı değerlendirin." },
        ], pairs)) : null,
      todo("Panele girmeyen göstergeler", Object.entries(q.skipped_features).map(([f, r]) => skippedAdvice(m, f, r))),
      stampBar({ sources: sourcesOf(m), generated: stampTime(q.generated_at), period: q.dates.length ? `${monthName(q.dates[0])} – ${monthName(q.dates.at(-1))}` : "—", missing: Object.keys(q.skipped_features).length ? `${Object.keys(q.skipped_features).length} gösterge denetime girmedi` : "yok" }),
    );
  };

  run.addEventListener("click", () => busy(run, "Denetleniyor", async () => {
    const q = await postJSON("/api/audit", scope());
    state.audits.set(scopeKey(), q);
    render(q);
  }, out));

  root.replaceChildren(
    viewHead("Adım 3", `${m.sector.name}: veri kalitesi`, "Göstergeler modellemeden önce denetlenir: durmuş seriler, eksik dönemler, aykırı değerler, yapısal kırılmalar ve birbirinin kopyası olan göstergeler."),
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
  const features = m.macro_features.filter((f) => f.status === "hazir").map((f) => f.name);
  if (!features.length) {
    root.replaceChildren(viewHead("Adım 4", "Tahmin çıtası"), emptyState("Bu sektörde hazır gösterge yok", "Önce 1. adımda eksik seri kodlarını tamamlayın."));
    return;
  }
  const option = (f, sel) => h("option", { value: f, selected: f === sel }, `${labelOf(m, f)} · ${info[f]?.transform_label || ""}`);
  const target = h("select", { "aria-label": "Tahmin edilecek gösterge" }, features.map((f) => option(f, prefs[`target:${sector}`])));
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

    out.replaceChildren(
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
          h("li", {}, h("strong", {}, "Açıklayıcı göstergeler: "), r.features_used.length ? r.features_used.map((f) => labelOf(m, f)).join(", ") : "kullanılmadı"),
          h("li", {}, h("strong", {}, "Sınırlamalar: "), "Sonuçlar geçmiş dönemlere aittir; yapısal kırılma sonrası dönemlerde hata dağılımı değişebilir. Tahmin değil, karşılaştırma çıtasıdır."))),
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
    if (!body.models.includes("naive")) body.models.unshift("naive"); // çıta karşılaştırması her zaman naive'e göre
    savePrefs({ [`target:${sector}`]: body.target, [`anchor:${sector}`]: anchor.value, horizon: body.horizon, min_train: body.min_train, exogenous: body.exogenous, models: body.models });
    const r = await postJSON("/api/backtest", body);
    state.backtests.set(sector, r);
    render(r);
  }, out));

  root.replaceChildren(
    viewHead("Adım 4", `${m.sector.name}: tahmin çıtası`, "Basit tahmin yöntemleri geçmişte sınanır. Ortaya çıkan en düşük hata, geliştireceğiniz ML modelinin aşması gereken çıtadır."),
    h("section", { class: "card" },
      h("div", { class: "controls" },
        h("label", { class: "field" }, h("span", {}, "Tahmin edilecek gösterge"), target),
        h("label", { class: "field" }, h("span", {}, term("anchor", "Çapa (isteğe bağlı)")), anchor),
        h("label", { class: "field" }, h("span", {}, term("horizon", "Ufuk (dönem)")), horizon),
        h("label", { class: "field" }, h("span", {}, term("min_train", "Asgari eğitim")), minTrain)),
      h("label", { class: "check" }, exog, h("span", {}, "Diğer göstergeleri regresyon modellerinde açıklayıcı olarak kullan", h("small", {}, "Ridge ve kantil regresyon, hedefin yanında paneldeki diğer göstergelerin geçmiş değerlerini de kullanır."))),
      h("details", { class: "about" }, h("summary", {}, `Sınanacak modeller (${chosen.size} seçili)`), h("div", {}, modelChecks,
        h("div", { class: "button-row" }, h("button", { type: "button", class: "ghost small", onclick: () => { state.meta.models.forEach((mm) => chosen.add(mm)); modelChecks.querySelectorAll("input").forEach((i) => (i.checked = true)); } }, "Tümünü seç")))),
      h("div", { class: "button-row" }, run)),
    out,
  );
  const cached = state.backtests.get(sector);
  if (cached) render(cached);
  else out.append(emptyState("Sınama henüz yapılmadı", "Tahmin edilecek göstergeyi seçip \"Sınamayı başlat\" düğmesine basın. Aylık ortalama bir göstergeyi tahmin ediyorsanız, aynı serinin ay sonu sürümünü çapa olarak seçmek çıtayı belirgin biçimde iyileştirir."));
};

views.saglik = async (root) => {
  const run = h("button", { type: "button" }, "Kaynakları denetle");
  const out = h("div", { class: "stack" });
  run.addEventListener("click", () => busy(run, "Denetleniyor", async () => {
    const rows = await getJSON("/api/health");
    const kind = { ok: ["ok", "Çalışıyor"], fail: ["crit", "Hata"], skipped: ["none", "Atlandı"] };
    const ok = rows.filter((r) => r.status === "ok").length;
    out.replaceChildren(
      summary([h("strong", {}, `${rows.length} kaynaktan ${ok} tanesi şu anda veri veriyor.`)], "Atlanan kaynaklar ya henüz otomatik çekim desteği olmayan ya da API anahtarı gerektiren kaynaklardır."),
      h("section", { class: "card" }, table([
        { label: "Kaynak", render: (r) => h("code", { class: "pill" }, r.source_id) },
        { label: "Durum", render: (r) => status(...(kind[r.status] || ["none", r.status])) },
        { label: "Ayrıntı", key: "detail" },
        { label: "Denetlenen seriler", render: (r) => r.checked.join(", ") || "—" },
      ], rows)));
  }, out));
  root.replaceChildren(
    viewHead(null, "Kaynak durumu", "Her kaynağın kritik serilerinden son gözlemin çekilip çekilemediğini denetler. EVDS için API anahtarı .env dosyasında EVDS_API_KEY olarak tanımlanmalıdır."),
    h("section", { class: "card" }, h("div", { class: "button-row" }, run)),
    out,
  );
};

// --- yönlendirme ve başlatma -----------------------------------------------------------------------------

async function route() {
  const name = (location.hash || "#genel").slice(1);
  const view = views[name] ? name : "genel";
  document.querySelectorAll("nav a").forEach((a) => (a.dataset.view === view ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  const root = $("#view");
  $("#tooltip").hidden = true; // grafik değişirken mouseleave gelmez
  root.replaceChildren(h("p", { class: "muted" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Yükleniyor"));
  notice(null);
  try {
    await views[view](root);
    if (view !== "genel") root.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  } catch (err) {
    root.replaceChildren();
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
