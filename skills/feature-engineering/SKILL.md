---
name: feature-engineering
description: Türkiye makro/sektör paneli için otomatik ve sektöre özgü açıklayıcı değişken (feature) üretir ve sızıntısız ön seçim yapar. Gecikmeler, hareketli ortalama/oynaklık, momentum, ivme, standart skor, üstel ortalama; sektör tarifleri (reel faiz, faiz farkı, kur sepeti, göreli fiyat); Türkiye takvimi (iş günü, Ramazan ve Kurban bayramı, Ramazan ayı, bayram öncesi); bilinen şok ve düzenleme göstergeleri (ÖTV indirimi, eşel mobil, deprem). Seçim: etkin örneklem düzeltmeli Spearman, Benjamini–Hochberg, alt dönem istikrarı, fazlalık eleme; ardından backtest ile katkı ölçümü. Kullanıcı "feature engineering", "değişken üret", "hangi değişkenler önemli", "feature seçimi", "modelime ne eklemeliyim", "otomatik feature" dediğinde ya da bir hedef için açıklayıcı değişken aradığında kullan. Also: "automatic sector-specific feature engineering Turkey".
allowed-tools:
  - Bash(${CLAUDE_SKILL_DIR}/../_shared/bin/mlb *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/render_feature_report.py *)
---

# Otomatik ve sektöre özgü değişken üretimi

Bu skill bir hedef için aday değişkenleri sistematik olarak üretir, yalnızca eğitim penceresinde tarar ve seçimin
gerçek katkısını seçim penceresinden sonraki dönemde backtest ile ölçer. Küçük aylık örneklemde yüzlerce aday taramak
şans eseri "anlamlı" değişkenler üretir. Bu nedenle üç güvence birlikte uygulanır:
1. **Nedensellik:** her değişken yalnızca t ve öncesindeki gözlemlerden hesaplanır; panel yayın gecikmesine göre
   hizalıdır.
2. **Çoklu karşılaştırma denetimi:** otokorelasyona göre etkin örneklem ve Benjamini–Hochberg yanlış keşif oranı.
3. **Dış doğrulama:** seçilen değişkenlerin değeri, seçimde kullanılmayan dönemde naive ve sektör paneliyle
   karşılaştırılarak ölçülür.

Değişken aileleri ve seçim istatistikleri: `references/feature-catalog.md`. Sektör hipotezleri ve tarif yazımı:
`references/sector-features.md`. Kaynakça: `${CLAUDE_SKILL_DIR}/../_shared/references/literature.md`. Bütünsel akış:
`${CLAUDE_SKILL_DIR}/../_shared/references/workflow.md`.

## Akış

### 1. Hedefi ve pencereyi belirle → `$OUT/assumptions.json`
```bash
MLB="${CLAUDE_SKILL_DIR}/../_shared/bin/mlb"
SECTOR=<sektör>; OUT="mlbooster-reports/${SECTOR}-$(date +%Y%m%d)"; mkdir -p "$OUT"
"$MLB" map show --sector "$SECTOR" --format json > "$OUT/map.json"
```
Eksikse kullanıcıya sor (en fazla üç soru): **hedef** (panel kolonu), **ufuk** (ay), **eğitim penceresi sonu**
(son 3–4 yıl değerlendirme için ayrılır; ör. `2022-12`). Hedef seçimi için
`sector-analysis` skill'inin hedef tablosuna (`targets.md`) bak. Sektörün tarifleri ve olayları
`map.json` ile birlikte `references/sector-features.md` dosyasından okunur; seri kodu bağlanmamış girdiler tarifin
atlanmasına yol açar. Bunu kullanıcıya bildir ve gerekirse `evds-series-discovery` ile tamamla.

### 2. Değişkenleri üret ve tara
```bash
"$MLB" features --sector "$SECTOR" --target <hedef> --horizon <h> --train-end <YYYY-AA> \
  --model linear --json "$OUT/features.json" --out "$OUT/features_all.csv" --selected-out "$OUT/features_selected.csv"
```
- Varsayılan olarak önce `data-prep` kuralları uygulanır (`--prep`). Ağaç modeli için `--model tree`.
- Kalıcı hedeflerde (enflasyon, faiz, kur düzeyi) tarama otomatik olarak **h dönemlik değişime** karşı yapılır;
  düzeyler arası korelasyon ortak trend nedeniyle sahte ilişki üretir.
- Hiç değişken seçilmezse bu bir bulgudur: panel, hedefin kendi geçmişinin ötesinde güvenilir bilgi taşımıyor
  olabilir. Ölçütleri gevşetme; raporda bunu yaz.

### 3. Katkıyı ölç (seçimde kullanılmayan dönemde)
```bash
"$MLB" backtest --sector "$SECTOR" --target <hedef> --horizon <h> --eval-start <YYYY-AA> --json "$OUT/bt_base.json"
"$MLB" backtest --sector "$SECTOR" --target <hedef> --horizon <h> --eval-start <YYYY-AA> \
  --panel-file "$OUT/features_selected.csv" --json "$OUT/bt_features.json"
```
`--eval-start` eğitim penceresi sonuyla aynı olmalıdır. Aksi halde seçim yapılan dönemde ölçülen performans iyimser olur.
Aylık ortalama hedeflerde çapa (`--anchor`) ver; ayrıntı `forecast-baseline` skill'inde.

### 4. Yorumla → `$OUT/feature-notes.md`
- Seçilen değişkenlerin ekonomik anlamını yaz (ör. "ÜFE−TÜFE farkının 3 aylık değişimi: maliyet baskısının fiyata
  geçişi"). Ekonomik gerekçesi olmayan bir seçimi "istatistiksel, yorumu belirsiz" diye işaretle.
- Backtest sonucunu sayıyla ver: seçilen değişkenlerle ridge MAE'si, sektör paneliyle ridge MAE'si ve naive MAE.
  İyileşme yoksa bunu açıkça yaz.
- Tarama sayısını belirt (ör. "457 aday tarandı, 4'ü seçildi"). Nedensellik iddia etme.

### 5. Raporu üret
```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/render_feature_report.py" --features "$OUT/features.json" \
  --bt-base "$OUT/bt_base.json" --bt-features "$OUT/bt_features.json" --notes "$OUT/feature-notes.md" \
  --assumptions "$OUT/assumptions.json" --out "$OUT/features.html"
```
Kullanıcıya rapor yolunu, `features_selected.csv` dosyasının modele hazır olduğunu, seçilen değişkenleri ve
seçimin dış dönemdeki katkısını ver.

## Kalite ölçütleri
- Seçim yalnızca eğitim penceresinde yapılmış, katkı yalnızca sonrasında ölçülmüştür; iki tarih raporda yazılıdır.
- Tarama ve seçim sayıları birlikte verilir.
- Takvim ve olay değişkenleri sabit tarihlere dayanır; tarihi doğrulanmamış bir düzenleme katalog olayına eklenmez,
  `assumptions.json` dosyasına varsayım olarak yazılır.
- Yeni bir sektör tarifi ya da olay, paket kataloğundaki sektör dosyasına (`catalog_data/sectors/<sektör>.yaml`)
  eklenir. Girdi adları sektörün değişken adlarıyla birebir aynı olmalıdır; katalog doğrulaması bunu zorlar.
