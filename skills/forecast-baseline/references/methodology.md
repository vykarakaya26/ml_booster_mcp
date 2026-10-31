# Backtest metodolojisi

Kaynak kod: `mlbooster.domain.services.backtest.rolling_origin_backtest`.

## Rolling-origin (genişleyen pencere)
Her başlangıç ayı `t` için yalnızca `≤ t` satırlarıyla eğitilir ve `t+h` tahmin edilir. İlk `t`,
`min_train` ayıncı satırdır (varsayılan 48). Panel nokta-zamanlıdır: `t` satırı, `t` ayının sonunda
yayımlanmış bilgiyi içerir. Bu yüzden yayın gecikmesinden kaynaklanan sızıntı yoktur.

Kod, look-ahead olmadığını bir testle de doğrular: gelecekteki veriler bozulduğunda geçmiş
tahminler değişmemelidir (`test_backtest_future_values_do_not_change_past_predictions`).

## Modeller
| Model | Tahmin | Ne zaman iyi |
|---|---|---|
| naive | y[t] | Rastgele yürüyüşe yakın seriler (kur, kısa vadeli YoY) |
| seasonal_naive | y[t+h−12] | Mevsimsel seviye serileri. YoY serilerde kötüdür |
| drift | y[t] + h · ortalama eğim | Trendli seviye serileri |
| mean12 | son 12 ayın ortalaması (Atkeson–Ohanian) | Enflasyon: literatürde geçilmesi zor çıta ([Stock & Watson](https://scholar.harvard.edu/sites/scholar.harvard.edu/files/stock/files/forecastinginflation.pdf)) |
| anchor_naive | anchor[t] (`--anchor`) | Aylık ortalama hedefler. Ay sonu değeri, ortalamadan çok daha günceldir |
| median12 | son 12 ayın medyanı | Sapmalı/aykırı veride mean12'nin dayanıklı karşılığı |
| drift_recent | y[t] + h · son 6 ayın ortalama eğimi | Kontrollü patikalar (ör. TCMB'nin yönettiği kur); tüm dönem drift'i rejim değişince yanılır |
| qr | taban[t] + kantil regresyon (τ=0,5); aralık τ=0,1/0,9 | Belirsizliğin asimetrik olduğu durumlar (kur sıçraması riski, enflasyon şokları); olasılıksal tahmin |
| combo_mean / combo_median | üyelerin ortalaması / medyanı | Basit kombinasyon çoğu zaman tek modeli geçer (kombinasyon bilmecesi); medyan kötü bir üyeye karşı dayanıklıdır |
| auto_robust | geçmiş değişimlerde fazla basıklık > 3 ya da aykırı oranı > %5 ise medyan, değilse ortalama | Sapmanın dönemden döneme değiştiği seriler; kural yalnız geçmiş veriyle uygulanır |
| ridge | taban[t] + ridge(gecikmeler → y[t+h] − taban[t]); taban = çapa ya da y | Dış değişkenlerin bilgi taşıdığı durumlar (ör. politika faizi → kredi faizi) |

Ridge ayrıntıları:
- Gecikmeler 0, 1, 2 ve 11 aydır; hedefin ve (açıksa) diğer feature'ların gecikmeleri kullanılır.
- Standartlaştırma yalnızca eğitim kümesiyle yapılır; alpha 10'dur.
- Değişimi öğrenip naive'in üstüne eklediği için en kötü durumda naive'e yakın kalır. Durağan
  olmayan TR serilerinde seviyeyi doğrudan tahmin etmekten daha sağlamdır.

## Aralıklar ve olasılıksal değerlendirme
- **Kantil regresyon** (L2 cezalı, IRLS): yalnız hedefin ve çapanın gecikmeleriyle; τ = 0,1 / 0,5 / 0,9.
  Büyüme-risk yaklaşımının (Adrian, Boyarchenko & Giannone 2019) basit bir uyarlamasıdır.
- **Ampirik aralık** (diğer modeller): modelin yalnızca o güne kadar gerçekleşmiş hatalarının %10/%90
  kantilleri; en az 12 hata gerekir. Look-ahead yoktur.
- **%80 kapsama**: gerçekleşenin aralık içinde kalma oranı, ideali ≈ %80. Belirgin biçimde düşükse
  aralık belirsizliği olduğundan dar gösteriyordur; rejim değişiminde olağandır.
- **Aralık genişliği**: dar ve iyi kapsayan aralık iyidir. Kapsama sağlanmadan dar aralık bir erdem değildir.
- **Pinball (kantil) kaybı**: üç kantilin ortalaması; olasılıksal tahminin tek sayılık özeti. Düşük olan iyidir.

## Metrikler
- **MAE**: birincil sıralama ölçüsü, hedefin biriminde.
- **RMSE**: büyük hataları cezalandırır. MAE'ye göre çok büyükse hata şok aylarında yoğunlaşmıştır.
- **sMAPE**: simetrik yüzde hata. Sıfıra yakın hedeflerde (log-fark) şişer; orada yorumlama.
- **MASE**: ilk eğitim penceresindeki h-adımlı naive hatasına oran. 1'in altı o dönemin naive'inden
  iyidir.
- **Ortak noktalar**: metrikler yalnızca tüm modellerin tahmin ürettiği başlangıçlarda hesaplanır.

## Rejim alt dönemleri
`regime_metrics`, metrikleri aynı ortak örneklemde üç rejim için ayrı hesaplar: 2021-11 ve öncesi,
2021-12 – 2023, 2024 ve sonrası. Tam dönem sonucu tek başına yanıltıcı olabilir; bir model sakin
dönemde kazanıp şok döneminde kaybediyor olabilir. Kabul ölçütüne son rejimdeki MAE'yi de ekle.

## Tuzaklar
- Ufuk büyüdükçe (h ≥ 6) örnek sayısı azalır, metrikler oynaklaşır.
- 2021-12 ile 2023 arası tüm modellerin hatasını büyütür. Bu dönemi dışlayan ayrı bir backtest
  (`--start` ya da `--end` ile) karşılaştırma için faydalı olabilir; dışladıysan raporda söyle.
- Bu çerçeve hiperparametre araması yapmaz. Referans olmasının amacı da budur.

## Çapa (anchor) neden gerekli
Günlük bir seri aylığa ortalama alınarak indirgenince, ay içindeki son seviye bilgisi ortalamada
kaybolur. Örneğin bir faiz kararı ayın ortasında açıklanmışsa, aylık ortalama yeni seviyeyi yalnızca
kısmen yansıtır. Ay sonu değeri (`agg: last`) ise t anında zaten bilinir ve t+1 ortalamasının en iyi
basit tahminidir. 2026-10 canlı testinde AOFM için çapa naive MAE 0,45 iken düz naive 1,02 ölçüldü.
Çapasız bir backtest çıtayı yarı yarıya olduğundan düşük gösterir.

## Sapmalı veride ortalama mı medyan mı?
- Değişimlerde fazla basıklık > 3 ya da aykırı oranı > %5 ise: **medyan** temelli kurallar
  (`median12`, `combo_median`, `auto_robust`). Ortalama tek bir şok ayına çok duyarlıdır.
- Sapma düşük ve dağılım simetrikse ortalama daha verimlidir (`mean12`, `combo_mean`).
- Kombinasyona çok kötü bir üye girdiğinde (ör. trendli seride `mean12`) ortalama kombinasyonu
  bozulur, medyan bozulmaz. Canlı AOFM testinde `combo_mean` 1,58, `combo_median` 0,81 MAE verdi.
- Canlı USD/TRY testinde (log-fark) medyan kombinasyonu ve `auto_robust` naive'i yaklaşık %13 geçti:
  kur değişimlerinde şoklar sık olduğu için medyan daha dayanıklıdır.

## Kontrollü kur rejimi
Denetimin `managed_path` bulgusu, son 12 ayda değişimin pozitif ve istikrarlı, oynaklığın da düşük
olduğunu söyler; 2023 ortasından beri USD/TRY'de bu görülür. Bu rejimde:
- Çıta son dönem değişimidir (naive / `drift_recent`). Uzun dönem ortalaması yanıltır.
- Nokta tahmin iyi görünür ama kuyruk riski yüksektir: geçmiş kırılmalar 2018-08, 2021-12, 2023-06
  ve 2025-03'tür. Kantil aralığı ve kırılma senaryosunu raporda ayrıca göster.
- Dış tahminlerle karşılaştır (`../_shared/references/external-forecasts.md`).
