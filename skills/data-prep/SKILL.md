---
name: data-prep
description: Türkiye makro/sektör panelini modellemeye hazırlar. Her değişkenin dağılımını (ortalama, standart sapma, medyan, MAD, IQR, çarpıklık, basıklık, outlier oranı, durağanlık) eğitim penceresinde ölçer; buna göre merkez/ölçek seçimini, imputation'ı, outlier winsorize etmeyi ya da işaretlemeyi, log/Yeo–Johnson dönüşümünü, fark almayı ve standard/robust scaling'i leakage olmadan uygular ve gerekçeli bir HTML rapor üretir. Kullanıcı "veri ön işleme", "data prep", "normalize et", "ölçekle", "outlier'ları temizle", "eksikleri doldur", "dağılım çarpık", "medyan mı ortalama mı", "modele hazırla" dediğinde ya da bir panelle model kurmadan önce kullan. Veri kalitesi teşhisi için kullanma (data-quality-audit); yeni feature üretimi için kullanma (feature-engineering). Also: "distribution-aware preprocessing for Turkish macro panel", "scaling, imputation, outlier handling".
allowed-tools:
  - Bash(${CLAUDE_SKILL_DIR}/../_shared/bin/mlb *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/render_prep_report.py *)
---

# Dağılıma dayalı veri ön işleme

Bu skill bir sektör panelini seçilen model ailesine uygun biçimde hazırlar. Her karar, değişkenin eğitim
penceresindeki dağılımına dayanır ve gerekçesiyle raporlanır. İki ilke önceliklidir:
1. **Leakage yok.** Bütün parametreler (merkez, ölçek, Yeo–Johnson λ, outlier eşikleri) yalnızca eğitim
   penceresinden tahmin edilir. Eksik değerler yalnızca ileri taşınır (forward fill). Geleceğe ait bilgi kullanılmaz.
2. **Bilgi kaybı yok.** Türkiye verisindeki uç değerler çoğunlukla gerçek şoklardır; silinmez, işaretlenir. Hedef
   değişkenin dağılımı değiştirilmez.

Karar kuralları ve kaynakları: `references/preprocessing-guide.md`. Kaynakça (O'Reilly ve serbest erişimli
kaynaklar): `${CLAUDE_SKILL_DIR}/../_shared/references/literature.md`. Bütünsel akıştaki yeri:
`${CLAUDE_SKILL_DIR}/../_shared/references/workflow.md`.

## Dil
Kullanıcıya görünen bütün metinleri (`prep-notes.md`, `assumptions.json` açıklamaları, sohbetteki özet) projenin
dil standardına göre yaz (`market-share` skill'indeki `report-guide.md` dosyası): akıcı, nesnel ve kurumsal Türkçe.
Teknik terimler İngilizce kalır ve Türkçeleştirilmez (outlier, winsorize, imputation, scaling, robust z-score,
Yeo–Johnson, leakage, look-ahead); ek gerekiyorsa kesme işaretiyle bağlanır ("outlier'lar", "scaling'den önce").
Sayılar Türkçe biçimde yazılır ("çarpıklık +2,53; outlier oranı %14").

## Akış

### 1. Girdileri belirle → `$OUT/assumptions.json`
```bash
MLB="${CLAUDE_SKILL_DIR}/../_shared/bin/mlb"
SECTOR=<sektör>; OUT="mlbooster-reports/${SECTOR}-$(date +%Y%m%d)"; mkdir -p "$OUT"
```
Kullanıcıya en fazla üç kısa soru sor (yanıt gelmezse varsayılanı kullan ve `confirmed: false` olarak kaydet):
- **Model ailesi**: doğrusal/düzenlileştirilmiş (varsayılan), ağaç tabanlı ya da yapay sinir ağı. Ağaç
  modellerinde scaling ve power transform gereksizdir; skill bunları atlar.
- **Hedef değişken**: dağılımı değiştirilmeyecek kolon.
- **Eğitim penceresi sonu**: backtest ve feature seçimiyle aynı tarih olmalıdır (ör. son 36 ay test için
  ayrılıyorsa bundan önceki ay). Verilmezse örneklemin ilk %80'i kullanılır ve bu raporda belirtilir.

### 2. Paneli ve ön işlemeyi üret
```bash
"$MLB" build-panel --sector "$SECTOR" --start 2012-01 --out "$OUT/panel.csv"
"$MLB" prep --sector "$SECTOR" --target <hedef> --train-end <YYYY-AA> --model linear \
  --out "$OUT/prepared.csv" --json "$OUT/prep.json"
```
- Komut her değişken için seçilen merkez/ölçeği ve uygulanan adımları yazdırır. `prep.json`; profilleri (önce ve
  sonra), adımları, tahmin edilen parametreleri ve gerekçeleri içerir.
- Belirli bir adımı kapatmak ya da zorlamak için `--outliers`, `--power`, `--scaling` ve `--difference` seçenekleri
  kullanılır. Değiştirilen her varsayılan, gerekçesiyle birlikte `assumptions.json` dosyasına yazılır.

### 3. Sonuçları değerlendir → `$OUT/prep-notes.md`
`prep.json` dosyasını oku ve şu soruları yanıtla:
- Hangi değişkenlerde robust merkez/ölçek seçildi ve neden (çarpıklık, basıklık, outlier oranı)?
- Dönüşümden sonra çarpıklık ve basıklık ne kadar azaldı? |çarpıklık| > 1 olmaya devam eden bir değişken varsa
  (ör. politika faizi farkı gibi basamaklı seriler) bunu açıkça yaz; bu, dönüşümle giderilemeyen yapısal bir
  özelliktir.
- Outlier göstergeleri hangi dönemlerde yoğunlaşıyor? Bilinen şoklarla (2018-08, 2020-03/04, 2021-12, 2023-02,
  2023-06) örtüşüyor mu? Örtüşmeyenler veri hatası olabilir ve kaynağından kontrol edilmelidir.
- Fark alınan değişkenler: düzey ilişkisi (ör. eşbütünleşme) modelleme açısından önemliyse `--difference none` öner.
Yorumları `references/preprocessing-guide.md` ve `${CLAUDE_SKILL_DIR}/../_shared/references/analysis-playbook.md`
kurallarına dayandır.

### 4. Raporu üret ve teslim et
```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/render_prep_report.py" --prep "$OUT/prep.json" \
  --raw "$OUT/panel.csv" --prepared "$OUT/prepared.csv" --notes "$OUT/prep-notes.md" \
  --assumptions "$OUT/assumptions.json" --out "$OUT/prep.html"
```
Kullanıcıya rapor yolunu ver, `prepared.csv` dosyasının modele hazır olduğunu belirt ve sonraki adımı öner:
feature üretimi için `feature-engineering`, performans ölçümü için `forecast-baseline`.

## Kalite ölçütleri
- Raporda eğitim penceresi sonu ve model ailesi açıkça yazılıdır.
- Hedef değişken dönüştürülmemiştir; yalnızca eksik değerleri ileri taşınmıştır.
- Robust seçimin gerekçesi sayıyla verilir (ör. "çarpıklık +2,53; outlier oranı %14").
- Outlier'lar "silindi" diye değil, "winsorize edildi ve işaretlendi" ya da "işaretlendi" diye anlatılır.
- Ön işleme tek başına model performansını kanıtlamaz; etkisi `forecast-baseline` ile ölçülür.
