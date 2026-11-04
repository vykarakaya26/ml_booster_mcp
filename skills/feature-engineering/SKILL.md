---
name: feature-engineering
description: Türkiye makro/sektör paneli için otomatik ve sektöre özgü feature'lar üretir ve leakage olmadan ön seçim yapar. Lag, rolling mean/std, momentum, acceleration, z-score, EWM; sektör tarifleri (reel faiz, faiz farkı, kur sepeti, göreli fiyat); Türkiye takvimi (iş günü, Ramazan ve Kurban bayramı, Ramazan ayı, bayram öncesi); bilinen şok ve düzenleme göstergeleri (ÖTV indirimi, eşel mobil, deprem). Seçim: effective sample size düzeltmeli Spearman, Benjamini–Hochberg, alt dönem istikrarı, redundancy elemesi. Kullanıcı "feature engineering", "değişken üret", "hangi değişkenler önemli", "feature seçimi", "modelime ne eklemeliyim", "otomatik feature" dediğinde ya da bir hedef için açıklayıcı değişken aradığında kullan. Yalnızca dağılım dönüşümü ya da eksik doldurma isteniyorsa kullanma (data-prep). Forecast ve model karşılaştırması kapsam dışıdır; skill seçilen feature'ları teslim eder, bir modele katkısını ölçmez. Also: "automatic sector-specific feature engineering Turkey", "feature selection".
allowed-tools:
  - Bash(${CLAUDE_SKILL_DIR}/../_shared/bin/mlb *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/render_feature_report.py *)
---

# Otomatik ve sektöre özgü feature engineering

Bu skill bir hedef için aday feature'ları sistematik olarak üretir ve yalnızca eğitim penceresinde tarar. Küçük bir
aylık örneklemde yüzlerce aday taramak, şans eseri "anlamlı" görünen feature'lar üretir. Bu nedenle üç güvence
birlikte uygulanır:
1. **Nedensellik:** her feature yalnızca t ve öncesindeki gözlemlerden hesaplanır; panel publication lag'e göre
   hizalıdır, look-ahead yoktur.
2. **Çoklu karşılaştırma denetimi:** otokorelasyona göre effective sample size ve Benjamini–Hochberg false discovery
   rate.
3. **İstikrar ve redundancy:** seçilen feature'ın ilişkisi alt dönemlerde aynı işareti taşır; neredeyse aynı bilgiyi
   içeren çiftlerden biri elenir.

Forecast ve model karşılaştırması bu skill'in kapsamı dışındadır. Çıktı, kullanıcının kendi modeline verebileceği
ML'e hazır feature setidir; eğitim penceresinden sonraki dönem o modelin sınanması için ayrılır.

Feature aileleri ve seçim istatistikleri: `references/feature-catalog.md`. Sektör hipotezleri ve tarif yazımı:
`references/sector-features.md`. Kaynakça: `${CLAUDE_SKILL_DIR}/../_shared/references/literature.md`. Bütünsel akış:
`${CLAUDE_SKILL_DIR}/../_shared/references/workflow.md`.

## Dil
Kullanıcıya görünen bütün metinleri (`feature-notes.md`, `assumptions.json` açıklamaları, sohbetteki özet) projenin
dil standardına göre yaz (`market-share` skill'indeki `report-guide.md` dosyası): akıcı, nesnel ve kurumsal Türkçe.
Teknik terimler İngilizce kalır ve Türkçeleştirilmez (feature, lag, rolling mean, EWM, leakage, look-ahead); ek
gerekiyorsa kesme işaretiyle bağlanır ("feature'lar", "lag'ler"). Katalogdaki feature adları (`tufe_yoy`, `aofm`,
`reel_faiz_ex_ante` …) kimliktir ve olduğu gibi yazılır. Sayılar Türkçe biçimde verilir ("Spearman r 0,42").

## Akış

### 1. Hedefi ve pencereyi belirle → `$OUT/assumptions.json`
```bash
MLB="${CLAUDE_SKILL_DIR}/../_shared/bin/mlb"
SECTOR=<sektör>; OUT="mlbooster-reports/${SECTOR}-$(date +%Y%m%d)"; mkdir -p "$OUT"
"$MLB" map show --sector "$SECTOR" --format json > "$OUT/map.json"
```
Eksikse kullanıcıya sor (en fazla üç soru): **hedef** (panel kolonu), **horizon** (ay) ve **eğitim penceresi sonu**
(son 3–4 yıl kullanıcının kendi modelini sınaması için ayrılır; ör. `2022-12`). Hedef seçimi için `sector-analysis` skill'inin hedef
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

### 3. Yorumla → `$OUT/feature-notes.md`
- Seçilen feature'ların ekonomik anlamını yaz (ör. "ÜFE−TÜFE farkının 3 aylık değişimi: maliyet baskısının fiyata
  geçişi"). Ekonomik gerekçesi olmayan bir seçimi "istatistiksel, yorumu belirsiz" diye işaretle.
- Seçimi ön eleme olarak yaz; bir modele katkısı ölçülmediği için performans iddiasında bulunma.
- Tarama sayısını belirt (ör. "457 aday tarandı, 4'ü seçildi"). Nedensellik iddia etme.

### 4. Raporu üret
```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/render_feature_report.py" --features "$OUT/features.json" \
  --notes "$OUT/feature-notes.md" --assumptions "$OUT/assumptions.json" --out "$OUT/features.html"
```
Kullanıcıya rapor yolunu ver, `features_selected.csv` dosyasının modele hazır olduğunu belirt ve seçilen feature'ları
özetle.

## Kalite ölçütleri
- Seçim yalnızca eğitim penceresinde yapılmıştır ve pencere sonu raporda yazılıdır.
- Tarama ve seçim sayıları birlikte verilir.
- Takvim ve olay değişkenleri sabit tarihlere dayanır. Tarihi doğrulanmamış bir düzenleme katalog olaylarına
  eklenmez; `assumptions.json` dosyasına varsayım olarak yazılır.
- Yeni bir sektör tarifi ya da olay, paket kataloğundaki sektör dosyasına (`catalog_data/sectors/<sektör>.yaml`)
  eklenir. Girdi adları sektörün feature adlarıyla birebir aynı olmalıdır; katalog doğrulaması bunu zorlar.
