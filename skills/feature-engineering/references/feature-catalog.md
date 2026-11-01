# Değişken aileleri ve seçim istatistikleri

Uygulama: `src/mlbooster/domain/services/feature_engineering.py` ve `calendar_tr.py`. Kaynak kısaltmaları
`${CLAUDE_SKILL_DIR}/../_shared/references/literature.md` dosyasındadır.

## 1. Üretilen aileler
Her temel değişken ve her sektör tarifi için aşağıdaki dönüşümler üretilir. Bütün pencereler geriye dönüktür:
t anındaki değer t ve öncesini kullanır. Hedefle ilgili istatistiklerde karşılaştırma penceresi t−1'de biter
(standart skor). Merkezlenmiş pencere, geriye doldurma ve iki yönlü filtre kullanılmaz (FES §6.1; HUYEN böl. 5).

| Aile | Tanım (aylık) | Gerekçe ve kaynak |
|---|---|---|
| Gecikme | x(t−k), k ∈ {1, 2, 3, 6, 12} | Dağıtılmış gecikmeler (FPP §7.4) |
| Hareketli ortalama | son 3, 6, 12 dönem ortalaması | Gürültüyü azaltır, eğilimi yakalar |
| Hareketli oynaklık | son 3, 6, 12 dönem standart sapması | Belirsizlik ve rejim sinyali |
| Momentum | x(t) − x(t−3), x(t) − x(t−12) | Kısa ve yıllık değişim |
| İvme | Δx(t) − Δx(t−1) | Dönüm noktaları |
| Standart skor | (x(t) − ort[t−24..t−1]) / std[t−24..t−1] | Geçmişine göre olağandışılık |
| Üstel ortalama | yarı ömür 3 ve 6 dönem | Sert pencere kenarı olmadan düzleştirme |
| Sektör tarifi | spread, ratio, mean, sum, product, gap, zscore | Ekonomik yapı, kaba kuvvet aramadan daha bilgilidir; etkileşimler için Feature Cross (MLDP #3; FES böl. 7; ZC böl. 2) |
| Takvim | ay (sinüs/kosinüs), iş günü, yıllık iş günü farkı, Ramazan ve Kurban bayramı günleri, Ramazan ayı günleri, bayram öncesi 7 gün | İşlem günü ve tatil regresörleri (FPP §7.4); Türkiye'de hareketli bayram etkisi (AA04; IUE11) |
| Olay ve rejim | genel şoklar (2018-08, 2020-03/04, 2021-12, 2023-02) ve rejimler (2021-09 – 2023-05 faiz indirimi, 2023-06 sonrası normalleşme); sektör olayları | Müdahale değişkenleri (FPP §7.4) |
| Ön işlem göstergesi | `__eksik`, `__aykiri` | Eksiklik ve aykırılık bilgi taşıyabilir (FES §8.4) |

Kalıcı bir hedefin kendi gecikmeleri de adaydır. Hedefin t anındaki değeri t'de bilindiği için t+h tahmininde
kullanılabilir.

## 2. Etiket
- Hedefin 1. gecikme otokorelasyonu ≥ 0,9 ise etiket **h dönemlik değişimdir**: y(t+h) − y(t). Kalıcı serilerde
  düzeyler arası korelasyon ortak trendden kaynaklanır ve sahte ilişki üretir (FES §10.4; durağanlık için FPP §9.1).
- Aksi halde etiket h dönem sonraki düzeydir. `--label level|change` ile zorlanabilir.

## 3. Seçim adımları
1. **Pencere:** yalnızca etiketi eğitim penceresi sonuna kadar gözlenmiş dönemler kullanılır (t ≤ eğitim sonu − h).
   Seçimi tam örneklemde yapmak iyimser hata tahmini verir (FES §10.4).
2. **İlişki:** Spearman sıra korelasyonu. Kalın kuyruklu değişkenlerde Pearson'dan daha dayanıklıdır.
3. **Etkin örneklem:** n_eff = n · (1 − ρx·ρy) / (1 + ρx·ρy). ρ, 1. gecikme otokorelasyonudur. Otokorelasyonlu
   serilerde bağımsız gözlem sayısı ay sayısından azdır (BRETHERTON).
4. **Anlamlılık ve çoklu karşılaştırma:** n_eff ile t istatistiği, normal yaklaşımla p değeri, Benjamini–Hochberg q
   değeri. Eşik q ≤ 0,10 (BH; tsfresh FRESH yaklaşımıyla aynı ilke, TSFRESH).
5. **İstikrar:** eğitim penceresi üç ardışık alt döneme bölünür. Korelasyon en az ikisinde aynı işaretli ve |r| ≥ 0,1
   olmalıdır (istikrar seçiminin basitleştirilmiş biçimi, STABSEL).
6. **Fazlalık:** puan sırasıyla ilerlenir. Seçilmiş bir değişkenle |r| ≥ 0,95 olan aday elenir. 0,9 da yaygın bir
   eşiktir; `redundancy_r` ile değiştirilebilir.
7. **Üst sınır:** seçilen değişken sayısı ≤ eğitim gözlemi / 15 (en fazla 40). Harrell'in aday parametre kuralıdır
   (HARRELL §4.4). VIF yalnızca tanı amaçlıdır; mekanik eşiklerle eleme önerilmez.

Puan = |r| × istikrar. Elenen her adayın gerekçesi `features.json` → `screening[].reason` alanında yer alır.

## 4. Değerlendirme
Seçimin değeri, seçim penceresinden sonraki dönemde ölçülür:
`backtest --panel-file features_selected.csv --eval-start <eğitim sonu>`. Aynı `--eval-start` ile sektör paneli ve
naive model de koşulur. Ridge, seçilen değişkenlerin gecikmelerini hedefin değişimine karşı modelleyerek seçimi doğrudan
sınar.
