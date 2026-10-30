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
| ridge | y[t] + ridge(gecikmeler → y[t+h] − y[t]) | Dış değişkenlerin bilgi taşıdığı durumlar (ör. politika faizi → kredi faizi) |

Ridge ayrıntıları:
- Gecikmeler 0, 1, 2 ve 11 aydır; hedefin ve (açıksa) diğer feature'ların gecikmeleri kullanılır.
- Standartlaştırma yalnızca eğitim kümesiyle yapılır; alpha 10'dur.
- Değişimi öğrenip naive'in üstüne eklediği için en kötü durumda naive'e yakın kalır. Durağan
  olmayan TR serilerinde seviyeyi doğrudan tahmin etmekten daha sağlamdır.

## Metrikler
- **MAE**: birincil sıralama ölçüsü, hedefin biriminde.
- **RMSE**: büyük hataları cezalandırır. MAE'ye göre çok büyükse hata şok aylarında yoğunlaşmıştır.
- **sMAPE**: simetrik yüzde hata. Sıfıra yakın hedeflerde (log-fark) şişer; orada yorumlama.
- **MASE**: ilk eğitim penceresindeki h-adımlı naive hatasına oran. 1'in altı o dönemin naive'inden
  iyidir.
- **Ortak noktalar**: metrikler yalnızca tüm modellerin tahmin ürettiği başlangıçlarda hesaplanır.

## Tuzaklar
- Ufuk büyüdükçe (h ≥ 6) örnek sayısı azalır, metrikler oynaklaşır.
- 2021-12 ile 2023 arası tüm modellerin hatasını büyütür. Bu dönemi dışlayan ayrı bir backtest
  (`--start` ya da `--end` ile) karşılaştırma için faydalı olabilir; dışladıysan raporda söyle.
- Bu çerçeve hiperparametre araması yapmaz. Referans olmasının amacı da budur.
