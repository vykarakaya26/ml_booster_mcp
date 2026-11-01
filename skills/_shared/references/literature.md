# Kaynakça: veri hazırlama ve feature engineering

Skill'lerdeki kurallar aşağıdaki kaynaklara dayanır. Metinler özetlenmiştir; doğrudan alıntı yapılmamıştır.
Kitap bölümleri yayınevi içindekiler tablosundan, yazarın resmî deposundan ya da yazarın kendi özetinden
doğrulanmıştır. Alt bölüm düzeyinde doğrulanamayan atıflar `[doğrula]` ile işaretlidir.

## Serbest erişimli temel kaynaklar
| Kısaltma | Kaynak | Kullanıldığı konu |
|---|---|---|
| FPP | Hyndman & Athanasopoulos, *Forecasting: Principles and Practice*, 3. baskı, https://otexts.com/fpp3/ | Dönüşümler ve Guerrero λ (§3.1), geri dönüşüm yanlılığı (§5.6), zaman serisi cross-validation (§5.10), müdahale ve takvim değişkenleri (§7.4), durağanlık ve fark alma (§9.1), aykırı ve eksik değerler (§13.9) |
| FES | Kuhn & Johnson, *Feature Engineering and Selection*, https://feat.engineering/ | Sayısal dönüşümler (§6.1), etkileşimler (bölüm 7), eksik veri ve eksiklik göstergesi (§8.4–8.5), seçimde overfitting ve dış doğrulama (§10.4) |
| NIST | NIST/SEMATECH e-Handbook of Statistical Methods | Değiştirilmiş z skoru ve 3,5 eşiği (§1.3.5.17), çarpıklık ve basıklık (§1.3.5.11) |
| SKL | scikit-learn kullanıcı kılavuzu, "Preprocessing data" ve "Common pitfalls" | StandardScaler, RobustScaler, PowerTransformer (Box–Cox, Yeo–Johnson), QuantileTransformer; ön işlemenin yalnızca eğitim verisinde öğrenilmesi |
| SM | statsmodels belgeleri | ADF ve KPSS'nin birlikte yorumlanması, STL, X-13 |
| ESS | Eurostat, *ESS Guidelines on Seasonal Adjustment* (2015) | Takvim ve outlier ön düzeltmesi, revizyon politikası |
| HARRELL | Harrell, *Regression Modeling Strategies*, §4.4, https://hbiostat.org/rmsc/multivar | Aday feature sayısı < etkin örneklem / 15 |
| BH | Benjamini & Hochberg (1995), *JRSS-B* 57(1) | Yanlış keşif oranı denetimi |
| TSFRESH | tsfresh belgeleri; Christ vd. (2018), *Neurocomputing* 307 | Feature başına test + FDR denetimiyle otomatik seçim (FRESH) |
| HAMPEL | Pearson vd. (2016), "Generalized Hampel Filters", *EURASIP J. Adv. Signal Process.* | Rolling window medyanı ve MAD ile outlier tespiti |
| KIM13 | Kim (2013), *Restor Dent Endod* 38(1) | Örneklem büyüklüğüne göre çarpıklık ve basıklık eşikleri |
| STABSEL | Meinshausen & Bühlmann (2010), *JRSS-B* 72(4) | İstikrar seçimi (alt örneklemlerde tutarlılık) |
| BRETHERTON | Bretherton vd. (1999), *Journal of Climate* 12 | Otokorelasyonlu serilerde etkin örneklem büyüklüğü |
| AA04 | Aruoba & Alper (2004), "Moving Holidays and Seasonal Adjustment: The Case of Turkey" | Türkiye'de hareketli bayramların mevsimsel düzeltmeye etkisi |
| IUE11 | "Sanayi Üretiminde Tatil Etkileri" (2011), İzmir Ekonomi Üniversitesi dergisi | Bayramların sanayi üretimi üzerindeki anlamlı negatif etkisi |

## O'Reilly kitapları
| Kısaltma | Kitap | Kullanıldığı konu |
|---|---|---|
| ZC | Zheng & Casari, *Feature Engineering for Machine Learning* (2018) | Bölüm 2: scaling, etkileşim feature'ları ve feature selection; log ve kuvvet dönüşümleri `[alt bölüm doğrula]` |
| GERON | Géron, *Hands-On Machine Learning* (3. baskı, 2022) | Bölüm 2 "Prepare the Data": veri temizleme, scaling ve dönüşüm, dönüşüm pipeline'ları |
| MLDP | Lakshmanan, Robinson & Munn, *Machine Learning Design Patterns* (2020) | Feature Cross (#3), Transform (#21: eğitimde öğrenilen dönüşümün aynen saklanması), Repeatable Sampling (#22), Windowed Inference (#24), Feature Store (#26) |
| NIELSEN | Nielsen, *Practical Time Series Analysis* (2019) | Bölüm 2: eksik veri, yeniden örnekleme, düzleştirme; bölüm 8: zaman serisi feature'larının üretimi ve seçimi |
| HUYEN | Huyen, *Designing Machine Learning Systems* (2022) | Bölüm 5: eksik değer, scaling, discretization, feature crossing, data leakage'ın yaygın nedenleri ve tespiti |
| PSDS | Bruce, Bruce & Gedeck, *Practical Statistics for Data Scientists* (2. baskı, 2020) | Bölüm 1: konum (ortalama, budanmış ortalama, medyan) ve yayılım (standart sapma, MAD, IQR) tahmincileri `[bölüm adları doğrula]` |

## Türkiye'ye özgü takvim ve düzenleme bilgisi
- Bayram tarihleri: 2429 sayılı Kanun ve Diyanet takvimi; her tarih en az iki bağımsız kaynakla doğrulanmıştır
  (`src/mlbooster/domain/services/calendar_tr.py`).
- Sektör olayları (ÖTV indirimi, eşel mobil, yıl ortası asgari ücret artışları, 6 Şubat 2023 depremleri) basın ve Resmî
  Gazete kaynaklıdır; kaynakları katalogdaki `events` kayıtlarında yer alır. Tarihi doğrulanmamış düzenlemeler kataloğa
  eklenmemiştir.
