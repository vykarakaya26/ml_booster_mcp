# _shared — skill'lerin ortak rapor kütüphanesi

`SKILL.md` içermediği için bir skill değildir; `sector-analysis`, `data-quality-audit` ve
`forecast-baseline` script'leri buradan import eder. Yalnızca Python standart kütüphanesi
kullanır, böylece script'ler `mlbooster` kurulu olmayan bir Python ile de çalışır.

- `mlb_report/svg.py` — tek eksenli çizgi grafik (küçük çoklu panel), dikey olay işaretleri, nokta vurguları, yatay çubuk
- `mlb_report/html.py` — kendi kendine yeten HTML sayfa: tema token'ları (açık/koyu), tablo, rozet, güvenli Markdown
- `mlb_report/data.py` — CSV/JSON okuma yardımcıları
- `mlb_report/assets/` — `report.css`, `report.js` (tooltip + crosshair; DOM'a yalnızca `textContent` yazar)

Renkler dataviz referans paletinden: kategorik ilk üç slot (mavi, turuncu, aqua) iki temada
da all-pairs doğrulandı; durum renkleri yalnızca önem derecesi için, her zaman ikon + etiketle.
