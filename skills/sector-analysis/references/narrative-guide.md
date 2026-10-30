# Yönetici özeti yazım rehberi

Okur bir veri bilimci, ürün yöneticisi ya da analisttir. Raporu iki dakikada okuyup "bu veriyle
ne yapabilirim, nelere dikkat etmeliyim" sorusunun cevabını almak ister.

## Yapı (narrative.md)

```markdown
## Ana bulgular
- 3–5 madde. Her madde bir sayı, bir ay ve "neden önemli" içerir.

## Veri durumu
- Panel kapsamı (ay sayısı, başlangıç-bitiş), panele giren / atlanan feature'lar.
- Kalite denetiminin yüksek ve orta bulgularından modellemeyi etkileyenler.

## Modelleme için öneriler
- Hangi dönüşüm ve hedef mantıklı, hangi rejim dummy'leri gerekli, hangi feature'lar gereksiz (kolineer).
- Backtest sonucu: en iyi referans model ve naive'e göre fark; üretim modeli için çıta.

## Sınırlar
- Keşif bekleyen ve sağlayıcısı olmayan kaynaklar, proxy uyarıları.
```

## Türkiye verisini yorumlarken
- **Rejimler**: 2018-08 kur şoku, 2020-03 COVID, 2021-12 kur şoku ve KKM, 2023-06 sonrası parasal
  sıkılaştırma, 2026-01 TÜFE baz değişikliği. Denetimdeki kırılmaları bu dönemlerle ilişkilendir;
  nedensellik iddia etme ("bu döneme denk geliyor" de).
- **Kur (`log_diff`)**: aylık log değişim ≈ aylık % değişim. Ortalama düşük, kuyruklar kalındır;
  aykırı değerler genelde şok aylarıdır ve veri hatası değildir.
- **Enflasyon (`yoy_pct`)**: 12 aylık değişim olduğu için yavaş döner. 2022'deki zirve aykırı
  görünür ama gerçektir; modelde rejim değişkeni ile ele alınmalıdır.
- **Faizler (seviye)**: politika döngüleri yapısal kırılma olarak görünür. Ağaç tabanlı
  modellerde seviye, doğrusal modellerde fark tercih edilebilir.
- **Güven endeksleri**: seviyesi anlamlıdır; 100 eşiği iyimserlik/kötümserlik ayrımıdır
  (TÜİK/TCMB tanımı).

## Dil
- Kısa, aktif cümleler kur. Rakamları Türkçe biçimde yaz (%31,5; 2026-09).
- Belirsizliği açık yaz: "backtest'te naive modeli geçemedi" doğrudur, "iyi sonuç" muğlaktır.
- Yatırım ya da finansal tavsiye verme; analizi veri ve modelleme bağlamında tut.
