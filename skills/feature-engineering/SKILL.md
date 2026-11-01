---
name: feature-engineering
description: Türkiye makro/sektör paneli için otomatik ve sektöre özgü feature'lar üretir ve leakage olmadan ön seçim yapar. Lag, rolling mean/std, momentum, acceleration, z-score, EWM; sektör tarifleri (reel faiz, faiz farkı, kur sepeti, göreli fiyat); Türkiye takvimi (iş günü, Ramazan ve Kurban bayramı, Ramazan ayı, bayram öncesi); bilinen şok ve düzenleme göstergeleri (ÖTV indirimi, eşel mobil, deprem). Seçim: effective sample size düzeltmeli Spearman, Benjamini–Hochberg, alt dönem istikrarı, redundancy elemesi; ardından katkının backtest ile ölçülmesi. Kullanıcı "feature engineering", "değişken üret", "hangi değişkenler önemli", "feature seçimi", "modelime ne eklemeliyim", "otomatik feature" dediğinde ya da bir hedef için açıklayıcı değişken aradığında kullan. Yalnızca dağılım dönüşümü ya da eksik doldurma isteniyorsa kullanma (data-prep); model karşılaştırması için kullanma (forecast-baseline). Also: "automatic sector-specific feature engineering Turkey", "feature selection".
allowed-tools:
  - Bash(${CLAUDE_SKILL_DIR}/../_shared/bin/mlb *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/render_feature_report.py *)
---

# Otomatik ve sektöre özgü feature engineering

Bu skill bir hedef için aday feature'ları sistematik olarak üretir, yalnızca eğitim penceresinde tarar ve seçimin
gerçek katkısını seçim penceresinden sonraki dönemde backtest ile ölçer. Küçük bir aylık örneklemde yüzlerce aday
taramak, şans eseri "anlamlı" görünen feature'lar üretir. Bu nedenle üç güvence birlikte uygulanır:
1. **Nedensellik:** her feature yalnızca t ve öncesindeki gözlemlerden hesaplanır; panel publication lag'e göre
   hizalıdır, look-ahead yoktur.
2. **Çoklu karşılaştırma denetimi:** otokorelasyona göre effective sample size ve Benjamini–Hochberg false discovery
   rate.
3. **Dış doğrulama:** seçilen feature'ların değeri, seçimde kullanılmayan dönemde naive model ve sektör paneliyle
   karşılaştırılarak ölçülür.

Feature aileleri ve seçim istatistikleri: `references/feature-catalog.md`. Sektör hipotezleri ve tarif yazımı:
`references/sector-features.md`. Kaynakça: `${CLAUDE_SKILL_DIR}/../_shared/references/literature.md`. Bütünsel akış:
`${CLAUDE_SKILL_DIR}/../_shared/references/workflow.md`.

## Dil
Kullanıcıya görünen bütün metinleri (`feature-notes.md`, `assumptions.json` açıklamaları, sohbetteki özet) projenin
dil standardına göre yaz (`market-share` skill'indeki `report-guide.md` dosyası): akıcı, nesnel ve kurumsal Türkçe.
Teknik terimler İngilizce kalır ve Türkçeleştirilmez (feature, lag, rolling mean, EWM, backtest, baseline, naive,
ridge, MAE, look-ahead); ek gerekiyorsa kesme işaretiyle bağlanır ("feature'lar", "backtest'te"). Katalogdaki
feature adları (`tufe_yoy`, `aofm`, `reel_faiz_ex_ante` …) kimliktir ve olduğu gibi yazılır. Sayılar Türkçe
biçimde verilir ("MAE 0,42 puan").

## Akış

### 1. Hedefi ve pencereyi belirle → `$OUT/assumptions.json`
```bash
MLB="${CLAUDE_SKILL_DIR}/../_shared/bin/mlb"
SECTOR=<sektör>; OUT="mlbooster-reports/${SECTOR}-$(date +%Y%m%d)"; mkdir -p "$OUT"
"$MLB" map show --sector "$SECTOR" --format json > "$OUT/map.json"
```
Eksikse kullanıcıya sor (en fazla üç soru): **hedef** (panel kolonu), **ufuk** (ay) ve **eğitim penceresi sonu**
(son 3–4 yıl değerlendirme için ayrılır; ör. `2022-12`). Hedef seçimi için `sector-analysis` skill'inin hedef
tablosuna (`targets.md`) bak. Sektörün tarifleri ve olayları `map.json` ile birlikte
`references/sector-features.md` dosyasından okunur. Seri kodu bağlanmamış girdiler tarifin atlanmasına yol açar;
bunu kullanıcıya bildir ve gerekirse `evds-series-discovery` ile tamamla.

### 2. Feature'ları üret ve tara
```bash
"$MLB" features --sector "$SECTOR" --target <hedef> --horizon <h> --train-end <YYYY-AA> \
  --model linear --json "$OUT/features.json" --out "$OUT/features_all.csv" --selected-out "$OUT/features_selected.csv"
```
- Varsayılan olarak önce `data-prep` kuralları uygulanır (`--prep`). Ağaç modeli için `--model tree` kullanılır.
- Kalıcı hedeflerde (enflasyon, faiz, kur düzeyi) tarama otomatik olarak **h dönemlik değişime** karşı yapılır;
  düzeyler arasındaki korelasyon ortak trend nedeniyle sahte ilişki üretir.
- Hiç feature seçilmezse bu da bir bulgudur: panel, hedefin kendi geçmişinin ötesinde güvenilir bilgi taşımıyor
  olabilir. Ölçütleri gevşetme; bunu raporda yaz.

### 3. Katkıyı ölç (seçimde kullanılmayan dönemde)
```bash
"$MLB" backtest --sector "$SECTOR" --target <hedef> --horizon <h> --eval-start <YYYY-AA> --json "$OUT/bt_base.json"
"$MLB" backtest --sector "$SECTOR" --target <hedef> --horizon <h> --eval-start <YYYY-AA> \
  --panel-file "$OUT/features_selected.csv" --json "$OUT/bt_features.json"
```
`--eval-start` eğitim penceresi sonuyla aynı olmalıdır; aksi halde seçimin yapıldığı dönemde ölçülen performans
iyimser çıkar. Aylık ortalama hedeflerde çapa (`--anchor`) ver; ayrıntı `forecast-baseline` skill'indedir.

### 4. Yorumla → `$OUT/feature-notes.md`
- Seçilen feature'ların ekonomik anlamını yaz (ör. "ÜFE−TÜFE farkının 3 aylık değişimi: maliyet baskısının fiyata
  geçişi"). Ekonomik gerekçesi olmayan bir seçimi "istatistiksel, yorumu belirsiz" diye işaretle.
- Backtest sonucunu sayıyla ver: seçilen feature'larla ridge MAE'si, sektör paneliyle ridge MAE'si ve naive MAE.
  İyileşme yoksa bunu açıkça yaz.
- Tarama sayısını belirt (ör. "457 aday tarandı, 4'ü seçildi"). Nedensellik iddia etme.

### 5. Raporu üret
```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/render_feature_report.py" --features "$OUT/features.json" \
  --bt-base "$OUT/bt_base.json" --bt-features "$OUT/bt_features.json" --notes "$OUT/feature-notes.md" \
  --assumptions "$OUT/assumptions.json" --out "$OUT/features.html"
```
Kullanıcıya rapor yolunu ver, `features_selected.csv` dosyasının modele hazır olduğunu belirt; seçilen feature'ları
ve seçimin dış dönemdeki katkısını özetle.

## Kalite ölçütleri
- Seçim yalnızca eğitim penceresinde yapılmış, katkı yalnızca sonrasında ölçülmüştür; iki tarih de raporda yazılıdır.
- Tarama ve seçim sayıları birlikte verilir.
- Takvim ve olay değişkenleri sabit tarihlere dayanır. Tarihi doğrulanmamış bir düzenleme katalog olaylarına
  eklenmez; `assumptions.json` dosyasına varsayım olarak yazılır.
- Yeni bir sektör tarifi ya da olay, paket kataloğundaki sektör dosyasına (`catalog_data/sectors/<sektör>.yaml`)
  eklenir. Girdi adları sektörün feature adlarıyla birebir aynı olmalıdır; katalog doğrulaması bunu zorlar.
