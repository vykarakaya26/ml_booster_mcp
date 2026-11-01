# _shared — skill'lerin ortak rapor kütüphanesi

`SKILL.md` içermediği için bir skill değildir; `sector-analysis`, `data-quality-audit`, `forecast-baseline`,
`data-prep`, `feature-engineering` ve `market-share` script'leri buradan import eder. Yalnızca Python standart kütüphanesi
kullanır, böylece script'ler `mlbooster` kurulu olmayan bir Python ile de çalışır.

- `mlb_report/svg.py` — tek eksenli çizgi grafik (small multiples), dikey olay işaretleri, nokta vurguları, tahmin aralığı bandı, yatay çubuk
- `mlb_report/html.py` — bağımsız (self-contained) HTML sayfa: tema token'ları (açık/koyu), tablo, badge, güvenli Markdown
- `mlb_report/data.py` — CSV/JSON okuma yardımcıları
- `mlb_report/assumptions.py`, `mlb_report/external.py` — varsayım kaydı ve dış tahmin (konsensüs) bölümleri
- `mlb_report/assets/` — `report.css`, `report.js` (tooltip + crosshair; DOM'a yalnızca `textContent` yazar)

Renkler dataviz referans paletinden: kategorik ilk üç slot (mavi, turuncu, aqua) iki temada
da all-pairs doğrulandı; durum renkleri yalnızca önem derecesi için, her zaman ikon + etiketle.
