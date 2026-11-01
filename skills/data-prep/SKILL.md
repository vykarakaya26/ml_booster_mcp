---
name: data-prep
description: Türkiye makro/sektör panelini modellemeye hazırlar; her değişkenin dağılımını (ortalama, standart sapma, medyan, MAD, IQR, çarpıklık, basıklık, aykırı oranı, durağanlık) eğitim penceresinde ölçer ve buna göre merkez/ölçek seçimi, eksik doldurma, aykırı değer kırpma ya da işaretleme, log/Yeo–Johnson dönüşümü, fark alma ve standart/robust ölçeklemeyi sızıntısız biçimde uygular; gerekçeli HTML rapor üretir. Kullanıcı "veri ön işleme", "data prep", "normalize et", "ölçekle", "aykırı değerleri temizle", "eksikleri doldur", "dağılımı çarpık", "medyan mı ortalama mı", "modele hazırla" dediğinde ya da bir panelle model kurmadan önce kullan. Also: "distribution-aware preprocessing for Turkish macro panel".
allowed-tools:
  - Bash(${CLAUDE_SKILL_DIR}/../_shared/bin/mlb *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/render_prep_report.py *)
---

# Dağılıma dayalı veri ön işleme

Bu skill, bir sektör panelini seçilen model ailesine uygun biçimde hazırlar. Her karar değişkenin eğitim
penceresindeki dağılımına dayanır ve gerekçesiyle raporlanır. İki ilke önceliklidir:
1. **Sızıntı yok.** Bütün parametreler (merkez, ölçek, Yeo–Johnson λ, aykırı eşikleri) yalnızca eğitim penceresinden
   tahmin edilir. Eksik değerler yalnızca ileri taşınır. Geleceğe ait bilgi kullanılmaz.
2. **Bilgi kaybı yok.** Türkiye verisindeki uç değerler çoğunlukla gerçek şoklardır; silinmez, işaretlenir. Hedef
   değişkenin dağılımı değiştirilmez.

Karar kuralları ve kaynakları: `references/preprocessing-guide.md`. Kaynakça (O'Reilly ve serbest erişimli
kaynaklar): `${CLAUDE_SKILL_DIR}/../_shared/references/literature.md`. Bütünsel akıştaki yeri:
`${CLAUDE_SKILL_DIR}/../_shared/references/workflow.md`.

## Akış

### 1. Girdileri belirle → `$OUT/assumptions.json`
```bash
MLB="${CLAUDE_SKILL_DIR}/../_shared/bin/mlb"
SECTOR=<sektör>; OUT="mlbooster-reports/${SECTOR}-$(date +%Y%m%d)"; mkdir -p "$OUT"
```
Kullanıcıya en fazla üç kısa soru sor (yanıt yoksa varsayılanı kullan ve `confirmed: false` olarak kaydet):
- **Model ailesi**: doğrusal/düzenlileştirilmiş (varsayılan), ağaç tabanlı ya da yapay sinir ağı. Ağaç modellerinde
  ölçekleme ve kuvvet dönüşümü gereksizdir; skill bunları atlar.
- **Hedef değişken**: dağılımı değiştirilmeyecek kolon.
- **Eğitim penceresi sonu**: backtest ve değişken seçimiyle aynı tarih olmalıdır (ör. son 36 ay test için ayrılırsa
  bundan önceki ay). Verilmezse örneklemin ilk %80'i kullanılır ve bu raporda belirtilir.

### 2. Paneli ve ön işlemeyi üret
```bash
"$MLB" build-panel --sector "$SECTOR" --start 2012-01 --out "$OUT/panel.csv"
"$MLB" prep --sector "$SECTOR" --target <hedef> --train-end <YYYY-AA> --model linear \
  --out "$OUT/prepared.csv" --json "$OUT/prep.json"
```
- Komut her değişken için seçilen merkez/ölçeği ve uygulanan adımları yazdırır. `prep.json` profilleri (önce ve sonra),
  adımları, tahmin edilen parametreleri ve gerekçeleri içerir.
- Belirli bir adımı kapatmak ya da zorlamak için `--outliers`, `--power`, `--scaling` ve `--difference` seçenekleri
  kullanılır. Değiştirilen her varsayılan `assumptions.json` dosyasına gerekçesiyle yazılır.

### 3. Sonuçları değerlendir → `$OUT/prep-notes.md`
`prep.json` dosyasını oku ve şu soruları yanıtla:
- Hangi değişkenlerde robust merkez/ölçek seçildi ve neden (çarpıklık, basıklık, aykırı oranı)?
- Dönüşüm sonrası çarpıklık ve basıklık ne kadar azaldı? Hâlâ |çarpıklık| > 1 olan değişken varsa (ör. politika faizi
  farkı gibi basamaklı seriler) bunu açıkça yaz; bu, dönüşümle giderilemeyen yapısal bir özelliktir.
- Aykırı değer göstergeleri hangi dönemlerde yoğunlaşıyor? Bilinen şoklarla (2018-08, 2020-03/04, 2021-12, 2023-02,
  2023-06) eşleşiyor mu? Eşleşmeyenler veri hatası olabilir; kaynağından kontrol edilmelidir.
- Fark alınan değişkenler: düzey ilişkisi (ör. eşbütünleşme) modelleme açısından önemliyse `--difference none` öner.
Yorumları `references/preprocessing-guide.md` ve `${CLAUDE_SKILL_DIR}/../_shared/references/analysis-playbook.md`
kurallarına dayandır.

### 4. Raporu üret ve teslim et
```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/render_prep_report.py" --prep "$OUT/prep.json" \
  --raw "$OUT/panel.csv" --prepared "$OUT/prepared.csv" --notes "$OUT/prep-notes.md" \
  --assumptions "$OUT/assumptions.json" --out "$OUT/prep.html"
```
Kullanıcıya rapor yolunu, `prepared.csv` dosyasının modele hazır olduğunu ve sonraki adımı ver: değişken üretimi için
`feature-engineering`, performans ölçümü için `forecast-baseline`.

## Kalite ölçütleri
- Raporda eğitim penceresi sonu ve model ailesi açıkça yazılıdır.
- Hedef değişken dönüştürülmemiştir; yalnızca eksik değerleri ileri taşınmıştır.
- Robust seçimin gerekçesi sayıyla verilir (ör. "çarpıklık +2,53; aykırı oranı %14").
- Aykırı değerler "silindi" diye değil, "kırpıldı ve işaretlendi" ya da "işaretlendi" diye anlatılır.
- Ön işleme, modelin performansını tek başına kanıtlamaz; etkisi `forecast-baseline` ile ölçülür.
