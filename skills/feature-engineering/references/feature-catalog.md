# Feature aileleri ve seçim istatistikleri

Uygulama: `src/mlbooster/domain/services/feature_engineering.py` ve `calendar_tr.py`. Kaynak kısaltmaları
`${CLAUDE_SKILL_DIR}/../_shared/references/literature.md` dosyasındadır.

## 1. Üretilen aileler
Her temel değişken (`base`) ve her sektör tarifi için aşağıdaki dönüşümler üretilir. Bütün pencereler geriye
dönüktür: t anındaki değer yalnızca t ve öncesini kullanır. Değişkenin kendi geçmişine göre hesaplanan
istatistiklerde karşılaştırma penceresi t−1'de biter (z-score). Merkezlenmiş pencere, geriye doldurma ve iki yönlü
filtre kullanılmaz (FES §6.1; HUYEN böl. 5).

| Aile (`family`, kolon son eki) | Tanım (aylık) | Gerekçe ve kaynak |
|---|---|---|
| Lag (`lag`, `__l{k}`) | x(t−k), k ∈ {1, 2, 3, 6, 12} | Dağıtılmış lag'ler (FPP §7.4) |
| Rolling mean (`rolling_mean`, `__mean{w}`) | son 3, 6, 12 dönemin ortalaması | Gürültüyü azaltır, eğilimi yakalar |
| Rolling std (`rolling_std`, `__std{w}`) | son 3, 6, 12 dönemin standart sapması | Belirsizlik ve rejim sinyali |
| Momentum (`momentum`, `__chg{k}`) | x(t) − x(t−3), x(t) − x(t−12) | Kısa vadeli ve yıllık değişim |
| Acceleration (`acceleration`, `__accel`) | Δx(t) − Δx(t−1) | Dönüm noktaları |
| Z-score (`zscore`, `__z24`) | (x(t) − ort[t−24..t−1]) / std[t−24..t−1] | Değişkenin kendi geçmişine göre olağandışılığı |
| EWM (`ewm`, `__ewm{h}`) | yarı ömür 3 ve 6 dönem | Keskin pencere kenarı olmadan düzleştirme |
| Sektör tarifi (`sector_recipe`) | spread, ratio, mean, sum, product, gap, zscore | Ekonomik yapı, kaba kuvvet aramasından daha bilgilidir; etkileşimler için Feature Cross (MLDP #3; FES böl. 7; ZC böl. 2) |
| Takvim (`calendar`) | ay (`ay_sin`, `ay_cos`), iş günü (`is_gunu`), yıllık iş günü farkı (`is_gunu_fark`), Ramazan ve Kurban bayramı günleri (`ramazan_bayrami_gun`, `kurban_bayrami_gun`), Ramazan ayı günleri (`ramazan_ayi_gun`), bayram öncesi 7 gün (`bayram_oncesi_gun`) | İşlem günü ve tatil regresörleri (FPP §7.4); Türkiye'de hareketli bayram etkisi (AA04; IUE11) |
| Olay ve rejim (`event`) | genel şoklar (`olay_2018_08_kur_soku`, `olay_2020_03_covid`, `olay_2020_04_covid`, `olay_2021_12_kur_soku`, `olay_2023_02_deprem`) ve rejimler (2021-09 – 2023-05 faiz indirimi için `rejim_2021_09_faiz_indirim`, 2023-06 sonrası normalleşme için `rejim_2023_06_normallesme`); sektör olayları | Müdahale değişkenleri (FPP §7.4) |
| Ön işleme göstergesi (`indicator`) | `__missing`, `__outlier` | Eksiklik ve outlier bilgisi kendi başına sinyal taşıyabilir (FES §8.4) |

Kalıcı bir hedefin kendi lag'leri de adaydır. Hedefin t anındaki değeri t'de bilindiği için t+h tahmininde
kullanılabilir.

## 2. Etiket
- Hedefin 1. lag otokorelasyonu ≥ 0,9 ise etiket **h dönemlik değişimdir**: y(t+h) − y(t). Kalıcı serilerde
  düzeyler arası korelasyon ortak trendden kaynaklanır ve sahte ilişki üretir (FES §10.4; durağanlık için FPP §9.1).
- Aksi halde etiket h dönem sonraki düzeydir. `--label level|change` ile zorlanabilir.

## 3. Seçim adımları
1. **Pencere:** yalnızca etiketi eğitim penceresi sonuna kadar gözlenmiş dönemler kullanılır (t ≤ eğitim sonu − h).
   Seçimi tam örneklemde yapmak hatayı olduğundan iyimser gösterir (FES §10.4).
2. **İlişki:** Spearman sıra korelasyonu. Kalın kuyruklu değişkenlerde Pearson'dan daha dayanıklıdır.
3. **Effective sample size:** n_eff = n · (1 − ρx·ρy) / (1 + ρx·ρy); ρ, 1. lag otokorelasyonudur. Otokorelasyonlu
   serilerde bağımsız gözlem sayısı ay sayısından azdır (BRETHERTON).
4. **Anlamlılık ve çoklu karşılaştırma:** n_eff ile t istatistiği, normal yaklaşımla p değeri ve Benjamini–Hochberg
   q değeri. Eşik q ≤ 0,10'dur (BH; tsfresh'in FRESH yaklaşımıyla aynı ilke, TSFRESH).
5. **İstikrar:** eğitim penceresi üç ardışık alt döneme bölünür. Korelasyon en az ikisinde aynı işaretli ve
   |r| ≥ 0,1 olmalıdır (stability selection'ın basitleştirilmiş biçimi, STABSEL).
6. **Redundancy:** adaylar puan sırasıyla işlenir. Daha önce seçilmiş bir feature ile |r| ≥ 0,95 olan aday elenir.
   0,9 da yaygın bir eşiktir; `redundancy_r` ile değiştirilebilir.
7. **Üst sınır:** seçilen feature sayısı ≤ eğitim gözlemi / 15 (en fazla 40). Bu, Harrell'in aday parametre
   kuralıdır (HARRELL §4.4). VIF yalnızca tanı amaçlıdır; mekanik eşiklerle eleme önerilmez.

Puan = |r| × istikrar. Elenen her adayın gerekçesi `features.json` → `screening[].reason` alanında yer alır.

## 4. Değerlendirme
Seçimin değeri, seçim penceresinden sonraki dönemde ölçülür:
`backtest --panel-file features_selected.csv --eval-start <eğitim sonu>`. Sektör paneli ve naive model de aynı
`--eval-start` ile koşulur. Ridge, seçilen feature'ların lag'lerini hedefin değişimine karşı modelleyerek seçimi
doğrudan sınar.
