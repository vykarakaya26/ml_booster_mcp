# Bütünsel analiz akışı: skill'ler birlikte nasıl çalışır

Beş skill tek bir analiz zincirinin halkalarıdır. Her biri tek başına da çalışır. Birlikte kullanıldıklarında aynı
çalışma klasörünü (`mlbooster-reports/<sektör>-<tarih>/`), aynı varsayım kaydını (`assumptions.json`) ve aynı eğitim
penceresi sonunu paylaşırlar. Böylece veri haritasından feature setine kadar her karar izlenebilir ve leakage'sız kalır.

Zincir ML'e hazır feature'larda biter. Forecast, model eğitimi ve model karşılaştırması bu ürünün kapsamı dışındadır.

```
sector-analysis ──► evds-series-discovery ──► data-quality-audit ──► data-prep ──► feature-engineering
  (harita, panel)     (eksik seri kodları)       (veri sorunları)      (dağılım)     (feature üretimi)
        │
        └──► market-share (oyuncu düzeyi: pay, yoğunlaşma, rakipler; pazar büyümesi makro panelle ilişkilendirilir)
```

| Adım | Skill | Girdi | Çıktı | Sonraki adıma aktarılan |
|---|---|---|---|---|
| 1 | `sector-analysis` | Sektör, amaç | `map.json`, `panel.csv`, `report.html` | Keşif bekleyen feature'lar, hedef adayı |
| 2 | `evds-series-discovery` | Keşif bekleyen feature | Kullanıcı kataloğunda bağlanan seri | Tamamlanmış panel |
| 3 | `data-quality-audit` | Sektör paneli | `audit.json`, kalite raporu | Structural break ve outlier dönemleri, güncelliğini yitirmiş seriler |
| 4 | `data-prep` | Panel, hedef, eğitim sonu, model ailesi | `prepared.csv`, `prep.json` | Dağılıma göre hazırlanmış panel |
| 5 | `feature-engineering` | Panel (ön işlemeyle), hedef, horizon, eğitim sonu | `features_selected.csv`, `features.json` | Seçilen feature'lar |
| — | `market-share` | Oyuncu bazlı tablo, sektör | `share.json`, rekabet raporu | Pazar büyümesinin makro sürücüleri, rakip seti |

## Ortak kurallar
1. **Tek eğitim penceresi:** `prep --train-end` ve `features --train-end` aynı tarihtir. Ön işleme parametreleri ve
   feature selection bu tarihe kadar öğrenilir; sonraki dönem kullanıcının kendi modelini sınaması için ayrılır.
2. **Tek varsayım kaydı:** her skill aynı `assumptions.json` dosyasına ekleme yapar; kimlikler (A1, A2, …) çakışmaz.
3. **Dayanak etiketleri:** her ifade veri, varsayım, model ya da yorum olarak etiketlenir
   (`assumptions-guide.md`).
4. **Kalite bulguları aktarılır:** `data-quality-audit` tarafından işaretlenen structural break tarihleri, `data-prep`
   outlier göstergeleri ve `feature-engineering` olay feature'larıyla tutarlı yorumlanır.
5. **Dil standardı:** raporlar kurumsal araştırma notu üslubuyla yazılır (`market-share/references/report-guide.md`).

## Tipik istekler ve izlenecek yol
| İstek | Yol |
|---|---|
| "Otomotiv sektörü için uçtan uca analiz ve ML'e hazır veri" | 1 → 3 → 4 → 5, ardından gerekiyorsa `market-share` |
| "Enflasyon modelim için hangi feature'ları kullanmalıyım?" | 4 → 5 (makro_genel) |
| "Panelim modele hazır mı?" | 3 → 4 |
| "Marka X'in pazar payı ve rakipleri" | `market-share`, `--sector` ile makro sürücüler |
