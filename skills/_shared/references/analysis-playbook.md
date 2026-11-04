# Analiz rehberi: dağılım teşhisi, ön işleme, değerlendirme

Türkiye makro/sektör aylık panelleri için. `[sezgisel]` işaretli eşikler yaygın uygulamadır, standart
değildir; raporda "eşik" değil "uyarı" diye geç. Kaynaklar satır içinde verilmiştir.

## 1. Seri başına teşhis (modelden önce)

| Teşhis | Nasıl | Uyarı eşiği | mlbooster karşılığı |
|---|---|---|---|
| Kapsam | eksik oran, en uzun boşluk, son gözlem ↔ beklenen yayın | iç boşluk; son gözlemin bir yayın döngüsünden daha eski olması | `audit`: `missing`, `stale` |
| Sabit değer dizileri | aynı değerin en uzun ardışık tekrarı | piyasa serisinde ≥3 ay: idari fiyat ya da ileri doldurma şüphesi | elle kontrol |
| Skewness | **dönüştürülmüş** seride (Δlog) | \|S\| 0,5–1 orta, >1 güçlü `[sezgisel]` | `diagnostics.skew` |
| Heavy tail | excess kurtosis | >1 dikkat, >3 belirgin `[sezgisel]` | `diagnostics.excess_kurtosis`, `heavy_tails` |
| Normallik | Jarque-Bera | n≥150'de hemen her zaman reddeder; p değil S ve K'yı raporla ([statsmodels](https://www.statsmodels.org/v0.13.5/stats.html)) | `diagnostics.jarque_bera_p` |
| Outlier oranı | modifiye z = 0,6745·(x−medyan)/MAD | literatürde \|z\|>3,5 (Iglewicz & Hoaglin); mlbooster, Türkiye verisindeki heavy tail nedeniyle 5 kullanır. Noktaların >%5'i işaretliyse bu tek tek outlier değil, rejim ya da heavy tail'dir `[sezgisel]` | `outliers` |
| Volatilite rejimi | değişimlerin std'si, son 36 ay / tüm dönem | oran >2 ya da <0,5 `[sezgisel]` | `diagnostics.volatility_ratio` |
| Mevsimsellik | ACF(12) ve STL gücü F_S | F_S ≥ 0,64 ise mevsimsel fark ([FPP3 §9.1](https://otexts.com/fpp3/stationarity.html)) | `diagnostics.acf12` |
| Structural break | bilinen tarihlerde Chow; içsel structural break için Zivot-Andrews / CUSUM | aday tarihleri her zaman olay takvimiyle yan yana göster | `structural_break` (ortalama kayması) |

### Stationarity: ADF ve KPSS birlikte
Bu iki testin sıfır hipotezleri terstir ([statsmodels ADF/KPSS](https://www.statsmodels.org/v0.14.4/examples/notebooks/generated/stationarity_detrending_adf_kpss.html)):

| ADF reddeder | KPSS reddeder | Karar | Önerilen işlem |
|---|---|---|---|
| evet | hayır | stationary | düzey kullanılabilir |
| hayır | evet | unit root | fark / log-fark al |
| evet | evet | trend-stationary ya da structural break | trendi çıkar, rejim dummy'si ekle, structural break'e dayanıklı test |
| hayır | hayır | belirsiz (kısa örneklem) | "belirsiz" diye raporla; aşırı fark alma |

mlbooster ADF'yi sabitli formda koşar: Schwert üst sınırı ⌊12·(T/100)^¼⌋, AIC ile gecikme seçimi ve
MacKinnon (2010) sonlu örneklem kritik değerleri. KPSS kritik değerleri: %10 0,347 · %5 0,463 · %1 0,739.

## 2. Yüksek enflasyon dönemine özgü kurallar (Türkiye, 2021–2024 ve sonrası)
- **Nominal TL seviyeleri** (TÜFE, kur, kredi stoku, satış TL): log al ve Δlog modelle.
- **YoY ve MoM.** YoY TÜFE, son 12 aylık MoM log değişimlerinin toplamıdır. Bu nedenle yüksek kalıcılık gösterir ve
  baz etkisi taşır. YoY'deki düşüşü "politika etkisi" diye yorumlamadan önce baz etkisini ayır:
  `YoY(t+h) = bilinen (12−h) MoM + tahmin edilen h MoM`.
- **Reel faiz**: `ln(1+i) − ln(1+π)` (i − π yaklaşımı %40–50 faizde bozulur). Ex ante için PKA
  beklentisini, ex post için gerçekleşen TÜFE'yi kullan.
- **Efektif politika duruşu ≠ politika faizi.** TCMB'nin repo ihalesine ara verdiği dönemlerde
  AOFM politika faizinin üstünde kalır. Efektif gösterge olarak AOFM'yi kullan ve farkı işaretle.
- **KKM (2021-12 → 2025-08)** mevduat faizi ve kompozisyon serilerini bozar; bu dönemi işaretle.
  KKM stoku 2023-08'de 3,4 trilyon TL iken 2025-08'de 478 milyar TL'ye indi; yeni hesaplar
  2025-08-23'te kapandı ([AGBI](https://www.agbi.com/banking-finance/2025/08/turkey-terminates-fx-protected-deposit-scheme/)).
- **Dolarizasyon.** Döviz mevduatının toplam mevduattaki payı 2026'nın ilk yarısında yaklaşık %37,9'du
  ve sıkı politikayla yavaş düşüyor (BDDK verisi, [hisse.net](https://www.hisse.net/haber/doviz-zengini-anadolu-sehirleri-belli-oldu-87298)).
  Hanehalkı kur şoklarında TL mevduattan dövize geçer; dolarizasyon bu geçişlerle izlenir.
  Haftalık YP mevduat serileri EVDS'de (`bie_hpbitablo4`) yalnızca 2024'ten itibaren var; uzun
  panelde kısa seri olarak kullan.
- **Kontrollü kur rejimi (yönetilen kur patikası).** 2023 ortasından beri USD/TRY, TCMB ve kamu bankası
  müdahaleleriyle yönetilen bir patikada ilerliyor. Commerzbank bu patikayı yıllık yaklaşık %20 değer
  kaybı (ayda ~%1,5) olarak tanımlıyor; %30–35 enflasyonla birlikte bu, TL'nin reel değer kazanması
  anlamına geliyor ([Tradingpedia, 2026-10-15](https://www.tradingpedia.com/2026/10/15/commerzbank-warns-of-ongoing-devaluation-risks-for-lira/)).
  2026 Mart'taki piyasa stresinde (İran savaşı) kurda sıçrama görülmedi; maliyet rezervlerden karşılandı ve haftalık repo
  ihaleleri durduruldu. Modelleme sonuçları:
  1. Rejim içinde saf random walk uygun bir benchmark değildir; yaklaşık sabit hızlı değer kaybı (naive
     log-fark ya da `drift_recent`) daha iyi uyar. mlbooster denetimi bunu `managed_path` olarak işaretler.
  2. Kalan volatilite düşüktür; normal dağılım varsayımlı aralıklar kuyruklarda gereğinden dardır.
  3. Kuyruk riskini sıçrama/rejim değişimi olarak ele al. Kalibrasyon kümesi: 2018-08, 2021-12,
     2023-06, 2025-03. Rezerv erimesi sıçrama olasılığını artırır; haftalık rezerv serisini
     (`TP.AB.TOPLAM`) izle.
  4. Üst quantile'ları horizon'la birlikte merkezden daha hızlı genişleyen quantile forecast'ları kullan (`qr`).
- **Olay takvimi** (rejim dummy'si; adım = rejim, dürtü = tek ay sıçrama): 2014-01 olağanüstü faiz
  artırımı · 2018-08 kur krizi · 2020-03 COVID · 2021-09/12 faiz indirimleri, kur şoku, KKM · 2023-06
  normalleşme · 2024-03 sıkılaştırma zirvesi · 2025-03 piyasa stresi · 2026-01 TÜFE 2025=100 baz değişikliği.
- **Ocak etkisi**: asgari ücret, yeniden değerleme oranıyla endekslenen vergi/harçlar ve idari fiyatlar
  Ocak MoM'unu politika takvimine bağlar. Bu "mevsimsellik" yıldan yıla değişkendir.
- **Winsorize ya da koruma.** Değerlendirme **hedefini asla winsorize etme**; kullanıcı açısından asıl
  önemli olan şoklardır. Feature'ları winsorize edeceksen eşikleri her eğitim penceresinde yeniden öğren ve sonuçları
  winsorize edilmiş ve edilmemiş haliyle raporla. Silmek yerine dummy ya da robust loss tercih et.

## 3. Ön işleme (ML)
- **Dönüşüm varsayılanları.** Seviye/fiyat → log → Δlog. Oranlar → seviye ya da Δ (düzey yüksekse
  log(1+r)). Sınırlı endeksler (güven) → seviye, varsa mevsimsellikten arındırılmış. Paylar → logit.
- **Box-Cox / Yeo-Johnson.** λ her eğitim penceresinde ayrı tahmin edilir. Düz geri dönüşüm medyanı
  verir, ortalama için yanlılık düzeltmesi gerekir ([FPP3 §5.6](https://otexts.com/fpp3/ftransformations.html)).
- **Scaling.** Heavy tail içeren panelde robust scaling (medyan/IQR) kullan; yalnızca eğitim penceresinde fit et.
- **Karışık frekans ve publication lag.** Tarihi yayın tarihine göre birleştir (as-of join). mlbooster
  paneli bunu zaten yapar; satır tarihi, verinin kullanılabilir olduğu aydır. Çeyreklik seriyi
  çeyrek sonları arasında doğrusal enterpolasyonla doldurma, bu bir sonraki çeyreği sızdırır; son
  yayımlanan değeri basamak olarak kullan.
- **Vintage.** EVDS'de herkese açık bir vintage arşivi doğrulanamadı. mlbooster her çekimi ayrı vintage
  olarak saklar (`as_of` ile okunabilir). TÜFE genelde revize edilmez; milli gelir, ödemeler dengesi
  ve dış borç edilebilir.

### Leakage kontrol listesi (her madde metodolojik hata sayılır)
- Scaler, λ ya da PCA tüm örneklemde fit edilmiş.
- İki taraflı filtreler (HP, STL, X-13) tüm örneklemde koşulmuş.
- Tahmin sınırını aşan enterpolasyon.
- Ortalanmış (centered) rolling window.
- Test döneminde feature seçimi ya da hiperparametre ayarı.
- Revize veriyle "gerçek zamanlı" performans iddiası.
- Horizon'la çakışan YoY hedef feature'ı.
- Publication lag'in göz ardı edilmesi.

## 4. Değerlendirme
- **Rolling origin** (genişleyen pencere, adım 1). Horizon'ları (1, 3, 6, 12) ayrı raporla
  ([FPP3 §5.10](https://otexts.com/fpp3/tscv.html)). Structural break içeren makro panelde k-fold yerine rolling origin
  kullan ([Bergmeir vd. 2018](https://ideas.repec.org/a/eee/csdana/v120y2018icp70-83.html)). En az ~60
  aylık eğitim `[sezgisel]`; sonuçları rejime göre alt dönemlerde de göster: 2021 öncesi, 2021–23, 2024+.
- **Benchmark'lar (her zaman):**
  - naive, seasonal naive, drift.
  - Enflasyon için **Atkeson–Ohanian**: son 12 ay ortalaması (`mean12`). Geçilmesi zor bir benchmark'tır
    ([Stock & Watson](https://scholar.harvard.edu/sites/scholar.harvard.edu/files/stock/files/forecastinginflation.pdf)).
  - **Aylık ortalama hedefler** için ay sonu değeri (`--anchor <hedef>_son`). Ortalaması alınmış bir random
    walk'un farkları pozitif otokorelasyonludur; bu yüzden düz naive zayıf bir benchmark'tır.
- **Metrikler.** MAE ve RMSE; MASE < 1 naive'den iyi demektir ([FPP3 §5.8](https://otexts.com/fpp3/accuracy.html)).
  Sıfıra yakın oranlarda MAPE ve sMAPE kullanma.
- **İyileşme iddiası.** Diebold–Mariano testi (HAC, h−1 gecikme, HLN düzeltmesi;
  [statsmodels](https://www.statsmodels.org/dev/generated/statsmodels.tsa.stattools.diebold_mariano_test.html))
  yalnızca benchmark'a karşı ve horizon başına yapılır. İç içe modellerde DM'nin boyutu bozulur.
- **Aralıklar.** Horizon başına backtest hatalarının quantile'larından ampirik bant çıkar (Fed yaklaşımı,
  [Reifschneider & Tulip 2017](https://www.federalreserve.gov/econres/feds/gauging-the-uncertainty-of-the-economic-outlook-using-historical-forecasting-errors-the-federal-reserves-approach.htm)).
  Mevcut rejim backtest örnekleminden farklıysa bandı genişlet ya da işaretle.
