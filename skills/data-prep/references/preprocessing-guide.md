# Dağılıma dayalı ön işleme kılavuzu

Bu belge `mlbooster prep` komutunun karar kurallarını ve dayanaklarını açıklar. Kaynak kısaltmaları
`${CLAUDE_SKILL_DIR}/../_shared/references/literature.md` dosyasındadır. Varsayılan eşikler yaygın uygulamaya
dayanır; kanonik bir kural olmayanlar ayrıca belirtilmiştir.

## 1. Merkez ve ölçek seçimi
İstatistikler **yalnızca eğitim penceresinde** hesaplanır.

| Durum | Merkez | Yayılım | Dayanak |
|---|---|---|---|
| Yaklaşık simetrik, ince kuyruklu (ör. düzgün bir endeksin log-farkı) | Ortalama | Standart sapma | Normal dağılımda en etkin tahminciler (PSDS böl. 1; SKL StandardScaler) |
| Kalın kuyruk ya da outlier (ör. aylık kur değişimi, kriz ayları) | Medyan | MAD × 1,4826 | Birkaç uç değerden etkilenmez; 1,4826 katsayısı normal dağılımda MAD'ı σ'ya eşitler (PSDS böl. 1; NIST §1.3.5.17) |
| Scaler için robust yayılım gerekiyorsa | Medyan | IQR (Ç3 − Ç1) | RobustScaler'ın tanımı (SKL) |
| 0–1 aralığındaki paylar | Logit dönüşümünden sonra ortalama | Logit üzerinde standart sapma | Sınırlı oranlar (FES §6.1) |

**Karar kuralı (varsayılan):** |çarpıklık| > 1, aşırı basıklık > 3 ya da robust outlier oranı > %2 ise medyan ve
IQR; aksi halde ortalama ve standart sapma kullanılır. |çarpıklık| > 1 bandı genellikle Bulmer'e (1979) atfedilir;
basıklık ve outlier oranı eşikleri uygulamadan gelen pratik kesim noktalarıdır. Daha katı bir ölçüt olarak Kim
(2013), belirgin normallik dışılığı |çarpıklık| > 2 ya da aşırı basıklık > 4 ile tanımlar ve küçük örneklemde
(n < 50) z testlerini önerir.

**Robust z-score:** M = 0,6745 · (x − medyan) / MAD; |M| > 3,5 olan gözlemler outlier sayılır (Iglewicz ve
Hoaglin, NIST §1.3.5.17). Küçük örneklemde klasik z-score'dan daha güvenilirdir.

## 2. Outlier'lar
1. **Tespit et, nedenini incele; otomatik olarak silme.** Outlier bir veri hatası da olabilir, gerçek ama olağandışı
   bir olay da (FPP §13.9).
2. **Gerçek şoklar sinyaldir.** 2018 ve 2021 kur şokları, 2020 COVID dönemi, 6 Şubat 2023 depremi ve 2023 politika
   dönüşü korunur ve müdahale değişkenleriyle (pulse ya da step göstergesi) modellenir (FPP §7.4).
3. **Hata kaynaklı outlier'lar** (birim ya da baz değişikliği, yazım hatası) boş değere çevrilir ve doldurulur
   (FPP §13.9).
4. **Winsorize yalnızca açıklayıcı değişkenlere uygulanır, hedefe asla uygulanmaz.** Eşikler eğitimde belirlenir ve
   yeni veriye aynen uygulanır (MLDP #21 Transform; FES §6.1).
5. **Uygulama:** `prep` nedensel (causal) bir Hampel filtresi kullanır. t anındaki değer, t−1'e kadarki 24 dönemin
   medyanı ve MAD'ı ile karşılaştırılır; |z| > 3,5 ise işaretlenir (HAMPEL). Merkezlenmiş pencere geleceği
   kullandığından yalnızca tanı amaçlı uygundur. Doğrusal ve yapay sinir ağı modellerinde değer bu aralığın
   sınırlarına winsorize edilir ve `__outlier` göstergesi eklenir. Ağaç modellerinde değer korunur, yalnızca gösterge eklenir.

## 3. Çarpıklık ve kalın kuyruk: dönüşümler
| Dönüşüm | Tanım aralığı | Ne zaman | Dayanak |
|---|---|---|---|
| log | x > 0 | Çarpımsal büyüyen düzeyler: kur, TÜFE, nominal TL tutarlar, kart harcaması | Farkı yaklaşık yüzde değişimdir (FPP §3.1) |
| Box–Cox | x > 0 | Varyans düzeyle artıyor ama log fazla ya da az düzeltiyor | λ Guerrero yöntemiyle seçilir (FPP §3.1) |
| Yeo–Johnson | Bütün reel sayılar | Negatif olabilen oranlar, faiz farkları, büyüme oranları | SKL PowerTransformer; FES §6.1 |
| Quantile → normal | Bütün reel sayılar | Çok kalın kuyruk, uzaklık tabanlı modeller | Korelasyonları bozar; doğrusal katsayılar yorumlanacaksa kullanılmaz; yaklaşık 100 eğitim ayının altında Yeo–Johnson tercih edilir (SKL) |
| logit | (0, 1) | Pazar payları ve oranlar | FES §6.1 |

- **Varsayılan karar:** Doğrusal ve yapay sinir ağı modellerinde |çarpıklık| > 1 ise pozitif ve mertebe farkı büyük
  (max/min > 10) düzey serilerine log, diğerlerine Yeo–Johnson uygulanır. λ, eğitim penceresinde profile likelihood
  ile tahmin edilir.
- **Geri dönüşüm:** Log ya da Box–Cox ölçeğindeki bir tahmin geri çevrildiğinde ortalamayı değil medyanı verir;
  ortalama gerekiyorsa bias düzeltmesi yapılır (FPP §5.6). Bu nedenle `prep` hedef değişkeni dönüştürmez.

## 4. Eksik veri
1. **Önce sınıflandır:** yapısal (seri henüz yok, kod durdurulmuş), rastgele ya da bilgi taşıyan eksiklik (FES §8.1).
2. **Eksiklik göstergesi:** doldurulan ya da boş kalan dönemler `__missing` kolonuyla işaretlenir (FES §8.4;
   HUYEN böl. 5).
3. **Imputation:** `prep` yalnızca ileri taşır (forward fill) ve en fazla `max_gap` (varsayılan 2) dönem doldurur.
   Geriye doldurma (`bfill`) ve merkezlenmiş interpolasyon, tahmin başlangıcından sonraki bilgiyi kullandığı için
   uygulanmaz (FES §8.5).
4. Bir kolonda eğitim penceresinin yaklaşık %20'sinden fazlası eksikse doldurmak yerine değişkeni çıkarmak
   değerlendirilmelidir (FES §8.5; katı bir kural değildir).

## 5. Durağanlık ve fark alma
1. **Sıra:** önce varyans (log/Box–Cox), sonra ortalama (fark) durağanlaştırılır (FPP §9.1).
2. **ADF ve KPSS birlikte okunur.** Sıfır hipotezleri terstir: ADF birim kökü, KPSS durağanlığı sınar
   (FPP §9.1; SM).

   | ADF | KPSS | Sonuç | İşlem |
   |---|---|---|---|
   | durağan | durağan | durağan | yok |
   | durağan değil | durağan değil | birim kök | fark al |
   | durağan değil | durağan | trend durağan | trendden arındır |
   | durağan | durağan değil | fark durağan | fark al |
3. **Aşırı fark almaktan kaçın.** Gereğinden fazla fark sahte dinamik üretir. Kısa ve structural break içeren
   örneklemlerde birim kök testlerinin gücü düşüktür; sonuçlar yol gösterici olarak kullanılır (FPP §9.1).
4. **Varsayılan karar:** `prep` birinci farkı yalnızca doğrusal modelde, katalogda düzey olarak tanımlı ve iki testin
   birlikte birim kök gösterdiği serilerde alır. Pozitif ve mertebe farkı büyük serilerde farktan önce log alınır.
   Yıllık değişim ya da log-fark olarak tanımlı serilerin yeniden farkı alınmaz.

## 6. Mevsimsellik ve takvim
- TÜİK mevsimsel düzeltmeyi TRAMO-SEATS ile yapar ve hareketli bayramları içeren ulusal takvim regresörü kullanır
  `[güncel uygulamayı doğrula]`. Mevsimsellikten arındırılmış resmî seriler yeni veriyle revize edilir; dolayısıyla
  geçmişteki bir değerin bugünkü hali o tarihte bilinmiyordu (ESS revizyon politikası).
- Model için önerilen yaklaşım, ham seriyi (log-fark ya da yıllık değişim) takvim değişkenleriyle birlikte
  kullanmaktır: iş günü, bayram günleri, Ramazan ayı günleri, bayram öncesi pencere. `feature-engineering` skill'i
  bunları üretir. Hareketli bayramlar Türkiye verisinde anlamlı bir takvim etkisi yaratır (AA04; IUE11).

## 7. Scaling
- **Scaling gerektiren modeller:** düzenlileştirilmiş doğrusal modeller (ridge, lasso), kernel tabanlı modeller,
  k-nearest neighbors, PCA ve yapay sinir ağları.
- **Scaling'den etkilenmeyenler:** ağaç tabanlı modeller (SKL; FES §6.1).
- **Scaler seçimi:** yaklaşık normal değişkende ortalama ve standart sapma, kalın kuyrukta medyan ve IQR. Min–max
  scaling robust değildir; tek bir uç değer diğer bütün değerleri sıkıştırır (SKL).
- **Yalnızca eğitimde öğren:** scaling'i train/test split'ten önce yapmak bilinen bir leakage nedenidir (HUYEN
  böl. 5). `prep` parametreleri eğitim penceresinde tahmin eder ve test dönemine aynen uygular (MLDP #21).

## 8. Yüksek enflasyon ortamına özgü kurallar
- Nominal TL tutarlar TÜFE ile reelleştirilir; hedef nominalse nominal seri korunur.
- Log-fark tercih edilir: yüksek enflasyonda basit yüzde değişimler toplanabilir değildir (FPP §3.1).
- Yıllık değişimde baz etkisi vardır: 12 ay önceki büyük bir aylık artış bugünkü yıllık değişimi mekanik olarak
  düşürür. Aylık ve yıllık değişimi birlikte kullanmak, yıllık değişimin farkını ise dikkatsizce almamak önerilir.
- Ex-ante reel faiz için enflasyon beklentisi (Piyasa Katılımcıları Anketi, PKA) kullanılır. Ex-post reel faiz,
  gerçekleşen TÜFE açıklandıktan sonra bilinir; panelin publication lag hizalaması bunu güvenceye alır.

## 9. Varsayılan ayarların özeti
| Ayar | Varsayılan | Değiştirme gerekçesi |
|---|---|---|
| `--model` | linear | Ağaç modeli kullanılacaksa `tree`: scaling, winsorize ve power transform atlanır |
| `--train-end` | örneklemin ilk %80'i | Backtest ve feature seçim penceresiyle aynı tarih verilmelidir |
| `--outliers` | auto (doğrusal: winsorize + işaretle; ağaç: işaretle) | Hata kaynaklı outlier'lar biliniyorsa önce veride düzeltilir |
| `--max-gap` | 2 dönem | Çeyreklik kaynaklı seriler aylığa çevrildiyse 2 yeterlidir |
| `--difference` | auto | Düzey ilişkisi (eşbütünleşme) önemliyse `none` |
