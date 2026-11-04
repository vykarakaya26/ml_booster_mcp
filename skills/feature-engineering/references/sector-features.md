# Sektöre özgü feature'lar

Katalogdaki tarifler (`feature_recipes`) ve olaylar (`events`) her sektör için otomatik uygulanır. Aşağıdaki liste bu
tariflerin ekonomik gerekçesini ve henüz katalogda olmayan, sınanması önerilen **hipotezleri** listeler. Hipotezler
olgu değildir: veriyle sınanır ve seçimden geçmesi yalnızca bir ilişkiye işaret eder. Tarihi doğrulanmamış düzenlemeler
`[doğrula]` ile işaretlidir ve Resmî Gazete ya da düzenleyici kurumdan kontrol edilmeden olay olarak kodlanmaz.

## Tarif yazımı
```yaml
feature_recipes:
  - {name: reel_faiz_ex_ante, op: spread, inputs: [aofm, pka_enflasyon_beklentisi], note: "..."}
  - {name: tufe_sapma_12, op: gap, inputs: [tufe_yoy], window: 12, note: "..."}
events:
  - {name: otv_indirimi_2018, start: "2018-11", end: "2019-06", kind: step, note: "...", source: "..."}
```
İşlemler: `spread` (a − b), `ratio` (a / b), `mean`, `sum`, `product` (etkileşim), `gap` (geçmiş `window` ortalamasından
sapma), `zscore` (geçmiş `window` döneme göre z-score). Olay türleri: `pulse` (yalnızca başlangıç ayı), `step`
(başlangıç ile bitiş arası; bitiş yoksa açık uçlu). Tarif ve olay adları katalog kimliğidir ve mevcut
Türkçe yazımlarıyla kullanılır.

## Sektörler

### Bankacılık (`bankacilik`)
- **Katalogda:**
  - ex-ante ve ex-post reel faiz;
  - AOFM (ağırlıklı ortalama fonlama maliyeti) − politika faizi (efektif politika duruşu);
  - TLREF − AOFM;
  - kredi − mevduat faizi farkı (seri kodları bağlandığında);
  - enflasyon sapması.
- **Hipotezler:**
  - Mevduat kredilerden hızlı yeniden fiyatlanır; bu nedenle faiz artışlarında marj daralır. Politika faizi
    değişiminin 1 ve 3 aylık lag'leri bunu sınar.
  - Dolarizasyon (YP mevduat payı) ve kur değişimi.
  - Takipteki krediler oranının 3–6 ay lag'li etkisi.
  - KKM, kredi büyüme sınırları ve menkul kıymet tesisi rejimleri `[doğrula]`.

### Sigorta (`sigorta`)
- **Katalogda:**
  - kur sepeti (ithal parça kaynaklı hasar maliyeti);
  - enflasyon sapması;
  - binek araç üretiminin z-score'u;
  - 6 Şubat 2023 deprem göstergesi.
- **Hipotezler:**
  - Branş bazında reel prim büyümesi.
  - Trafik sigortası prim tavanı değişiklikleri `[doğrula]`.
  - Poliçe yenileme nedeniyle 12 aylık yankı (12. lag).

### Telekom (`telekom`)
- **Katalogda:**
  - haberleşme fiyatı − genel enflasyon (fiyatlama gücü);
  - cihaz ve hizmet harcaması farkı;
  - telekom harcamasının z-score'u.
- **Hipotezler:**
  - ÖİV ve IMEI kayıt ücreti değişiklikleri `[doğrula]`.
  - Numara taşıma akışları: BTK çeyreklik verisi, yalnızca yayımlandıktan sonra aylığa taşınarak kullanılır.
  - Reel ücret baskısı altında daha ucuz tarifelere geçiş.

### Otomotiv (`otomotiv`)
- **Katalogda:**
  - USD/EUR kur sepeti;
  - reel taşıt kredisi faizi (seri bağlandığında);
  - reel fonlama faizi;
  - güven sapması;
  - araç harcamasının z-score'u;
  - Kasım 2018 – Haziran 2019 ÖTV indirimi rejimi.
- **Hipotezler:**
  - Duyurulan vergi artışı ya da indirim bitişinden önceki 1–2 ayda öne çekilmiş talep, ardından düşüş.
  - Büyük kur değer kaybı sonrası fiyat artışı beklentisiyle alım.
  - Aralık ayında model yılı ve yıl sonu kampanyaları.
  - BDDK taşıt kredisi vade ve kredi-değer oranı sınırları `[doğrula]`.

### Akaryakıt (`akaryakit`)
- **Katalogda:**
  - ÜFE − TÜFE farkı;
  - akaryakıt harcamasının z-score'u;
  - sanayi üretimi sapması;
  - eşel mobil rejimleri: 2018-05 – 2021-12 ve 2026-03 – 2026-09.
- **Hipotezler:**
  - TL cinsinden Brent: Brent seri kodu bağlandığında Brent log-fark + USD/TRY log-fark olarak `sum` tarifi.
  - ÖTV'nin Ocak ve Temmuz'daki Yİ-ÜFE endekslemesi.
  - Bayram ve yaz seyahat dönemleri.
  - Eşel mobilin 2019–2022 arasındaki kısmi uygulamaları `[doğrula]`.

### Perakende (`perakende`)
- **Katalogda:**
  - market, dışarıda yemek ve çevrim içi harcamanın toplam harcamaya göre büyüme farkları;
  - güven sapması;
  - TÜİK ve OECD güven ortalaması;
  - Temmuz 2022 ve Temmuz 2023 yıl ortası asgari ücret artışları.
- **Hipotezler:**
  - Ramazan ayı ve bayram öncesi alışveriş (takvim feature'ları otomatik üretilir).
  - Kasım ayında indirim dönemi.
  - Kart taksit sınırları `[doğrula]`.
  - Temmuz 2023 KDV oranı değişikliği `[tarihi doğrula]`.

### Turizm (`turizm`)
- **Katalogda:**
  - reel efektif kur sapması (fiyat rekabetçiliği);
  - seyahat ve konaklama harcaması farkı;
  - havayolu harcamasının z-score'u.
- **Hipotezler:**
  - Yabancı kartlarla yapılan harcama ve yabancı ziyaretçi sayısı (seri bağlandığında).
  - Bayram ve idari izinle uzayan tatiller.
  - Kaynak pazar şokları: 2015–2016 Rusya yaptırımları `[tarihleri doğrula]`, 15 Temmuz 2016, 2020 salgını.

### Konut (`konut`)
- **Katalogda:**
  - reel konut fiyat artışı;
  - reel konut kredisi faizi;
  - maliyet − fiyat farkı;
  - satışların z-score'u;
  - 6 Şubat 2023 deprem göstergesi.
- **Hipotezler:**
  - Yabancıya satış ve vatandaşlık eşiği değişikliği `[tarihi doğrula]`.
  - Yapı ve iskân izinleri, 6–18 ay lag'li bir arz göstergesi olarak.
  - Kamu bankalarının konut kredisi kampanyaları `[doğrula]`.

### Restoran ve yiyecek-içecek (pazar payı ve perakende verisiyle)
- **Hipotezler:**
  - Lokanta fiyatlarının gıda fiyatlarına göre göreli artışı (marj göstergesi).
  - Reel kart harcaması.
  - Asgari ücret artış ayları (işçilik maliyeti).
  - Ramazan ayı: öğle talebinin düşmesi ve iftar talebi; net etki veriyle tahmin edilir.
  - Yiyecek ÜFE'sinin 1–3 ay lag'li maliyet etkisi.
