# Backtest metodolojisi

Kaynak kod: `mlbooster.domain.services.backtest.rolling_origin_backtest`.

## Rolling-origin (expanding window)
Her origin ayı `t` için model yalnızca `≤ t` satırlarıyla eğitilir ve `t+h` tahmin edilir. İlk `t`,
`min_train`'inci satırdır (varsayılan 48). Panel point-in-time'dır: `t` satırı, `t` ayının sonuna kadar
yayımlanmış bilgiyi içerir. Bu yüzden publication lag kaynaklı leakage yoktur.

Kod, look-ahead olmadığını bir testle de doğrular: gelecekteki veriler bozulduğunda geçmiş tahminler
değişmemelidir (`test_backtest_future_values_do_not_change_past_predictions`).

## Modeller
| Model | Tahmin | Uygun olduğu durumlar |
|---|---|---|
| naive | y[t] | Random walk'a yakın seriler (kur, kısa vadeli YoY) |
| seasonal_naive | y[t+h−12] | Mevsimsel seviye serileri. YoY serilerde zayıf kalır |
| drift | y[t] + h · ortalama eğim | Trendli seviye serileri |
| mean12 | son 12 ayın ortalaması (Atkeson–Ohanian) | Enflasyon: literatürde geçilmesi zor bir benchmark ([Stock & Watson](https://scholar.harvard.edu/sites/scholar.harvard.edu/files/stock/files/forecastinginflation.pdf)) |
| anchor_naive | anchor[t] (`--anchor`) | Aylık ortalama hedefler. Ay sonu değeri ortalamadan belirgin biçimde daha günceldir |
| median12 | son 12 ayın medyanı | Çarpık ya da outlier içeren veride mean12'nin robust karşılığı |
| drift_recent | y[t] + h · son 6 ayın ortalama eğimi | Kontrollü patikalar (ör. TCMB'nin yönettiği kur); tüm dönemin drift'i rejim değişiminde yanlı sonuç verir |
| qr | taban[t] + quantile regression (τ=0,5); aralık τ=0,1/0,9 | Belirsizliğin asimetrik olduğu durumlar (kur sıçraması riski, enflasyon şokları); olasılıksal forecast |
| combo_mean / combo_median | üyelerin ortalaması / medyanı | Basit kombinasyon çoğu zaman tek modeli geçer (forecast combination puzzle); medyan, zayıf performanslı bir üyeye karşı robust'tır |
| auto_robust | geçmiş değişimlerde excess kurtosis > 3 ya da outlier oranı > %5 ise medyan, değilse ortalama | Çarpıklığı dönemden döneme değişen seriler; kural yalnızca geçmiş veriyle uygulanır |
| ridge | taban[t] + ridge(lag'ler → y[t+h] − taban[t]); taban = anchor ya da y | Exogenous değişkenlerin bilgi taşıdığı durumlar (ör. politika faizi → kredi faizi) |

Ridge ayrıntıları:
- Lag'ler 0, 1, 2 ve 11 aydır; hedefin ve (açıksa) diğer feature'ların lag'leri kullanılır.
- Standardizasyon yalnızca training kümesiyle yapılır; alpha 10'dur.
- Değişimi öğrenip naive'in üzerine eklediği için en kötü durumda naive'e yakın kalır. Durağan olmayan
  Türkiye serilerinde seviyeyi doğrudan tahmin etmekten daha sağlamdır.

## Aralıklar ve olasılıksal değerlendirme
- **Quantile regression** (L2 cezalı, IRLS): yalnızca hedefin ve anchor'ın lag'leriyle; τ = 0,1 / 0,5 / 0,9.
  Growth-at-risk yaklaşımının (Adrian, Boyarchenko & Giannone 2019) basit bir uyarlamasıdır.
- **Ampirik aralık** (diğer modeller): modelin yalnızca o güne kadar gerçekleşmiş hatalarının %10/%90
  quantile'ları; en az 12 hata gerekir. Look-ahead yoktur.
- **%80 coverage**: gerçekleşen değerin aralık içinde kalma oranı; ideali ≈ %80. Belirgin biçimde düşükse
  aralık belirsizliği olduğundan dar gösteriyordur; rejim değişimlerinde bu olağandır.
- **Aralık genişliği**: hedef coverage'ı sağlayan dar aralık tercih edilir. Coverage sağlanmadan elde edilen dar
  aralık bir üstünlük sayılmaz.
- **Pinball (quantile) loss**: üç quantile'ın ortalaması; olasılıksal forecast'in tek sayılık özeti. Düşük
  değer daha iyidir.

## Metrikler
- **MAE**: birincil sıralama ölçütü, hedefin biriminde.
- **RMSE**: büyük hataları cezalandırır. MAE'ye göre belirgin biçimde büyükse hatalar şok aylarında
  yoğunlaşmıştır.
- **sMAPE**: simetrik yüzde hata. Sıfıra yakın hedeflerde (log-fark) aşırı büyür; bu durumda yorumlanmamalıdır.
- **MASE**: ilk training penceresindeki h adımlı naive hatasına oran. 1'in altındaki değer, o dönemin naive'inden
  daha düşük hata anlamına gelir.
- **Ortak origin'ler**: metrikler yalnızca tüm modellerin tahmin ürettiği origin'lerde hesaplanır.

## Rejim alt dönemleri
`regime_metrics`, metrikleri aynı ortak örneklemde üç rejim için ayrı hesaplar: 2021-11 ve öncesi,
2021-12 – 2023, 2024 ve sonrası. Tam dönem sonucu tek başına yanıltıcı olabilir; bir model sakin dönemde
kazanıp şok döneminde kaybediyor olabilir. Kabul ölçütüne son rejimdeki MAE'yi de ekle.

## Dikkat edilecek noktalar
- Ufuk büyüdükçe (h ≥ 6) örnek sayısı azalır ve metrikler oynaklaşır.
- 2021-12 ile 2023 arası tüm modellerin hatasını büyütür. Bu dönemi dışarıda bırakan ayrı bir backtest
  (`--start` ya da `--end` ile) karşılaştırma için faydalı olabilir; dönem dışlandıysa bu raporda
  belirtilmelidir.
- Bu çerçeve bilinçli olarak hyperparameter araması yapmaz; baseline niteliği buna dayanır.

## Anchor'ın gerekçesi
Günlük bir seri ortalama alınarak aylığa indirgendiğinde ay içindeki son seviye bilgisi ortalamada kaybolur.
Örneğin bir faiz kararı ayın ortasında açıklandıysa aylık ortalama yeni seviyeyi yalnızca kısmen yansıtır.
Ay sonu değeri (`agg: last`) ise t anında zaten bilinir ve t+1 ortalamasının en iyi basit tahminidir. 2026-10
canlı testinde AOFM için anchor naive MAE 0,45 iken düz naive 1,02 ölçüldü. Anchor'sız bir backtest
benchmark'ı yarı yarıya olduğundan düşük gösterir.

## Çarpık veride ortalama ve medyan seçimi
- Değişimlerde excess kurtosis > 3 ya da outlier oranı > %5 ise **medyan** tabanlı kuralları kullan
  (`median12`, `combo_median`, `auto_robust`). Ortalama tek bir şok ayına çok duyarlıdır.
- Skewness düşük ve dağılım simetrikse ortalama daha verimlidir (`mean12`, `combo_mean`).
- Kombinasyona belirgin biçimde zayıf bir üye girdiğinde (ör. trendli seride `mean12`) ortalama
  kombinasyonunun performansı bozulur, medyan kombinasyonu etkilenmez. Canlı AOFM testinde `combo_mean`
  1,58, `combo_median` 0,81 MAE verdi.
- Canlı USD/TRY testinde (log-fark) medyan kombinasyonu ve `auto_robust` naive'i yaklaşık %13 geçti: kur
  değişimlerinde şoklar sık olduğu için medyan daha robust'tır.

## Kontrollü kur rejimi
Audit'in `managed_path` bulgusu, son 12 ayda değişimin pozitif ve istikrarlı, volatilitenin de düşük olduğunu
gösterir; 2023 ortasından beri USD/TRY'de bu durum görülür. Bu rejimde:
- Benchmark son dönem değişimidir (naive / `drift_recent`). Uzun dönem ortalaması yanıltır.
- Nokta tahmin iyi görünür ama tail risk yüksektir: geçmiş kırılmalar 2018-08, 2021-12, 2023-06 ve
  2025-03'tür. Quantile aralığını ve kırılma senaryosunu raporda ayrıca göster.
- Dış forecast'lerle karşılaştır (`../_shared/references/external-forecasts.md`).
