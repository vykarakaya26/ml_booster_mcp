# Yönetici özeti yazım rehberi

Okur bir veri bilimci, ürün yöneticisi ya da analisttir. İki dakikalık bir okumada iki sorunun yanıtını
almak ister: bu veriyle ne yapılabilir ve nelere dikkat etmek gerekir.

## Yapı (narrative.md)

```markdown
## Ana bulgular
- 3–5 madde. Her madde bir sayı, bir ay ve "neden önemli" sorusunun yanıtını içerir.

## Veri durumu
- Panel kapsamı (ay sayısı, başlangıç–bitiş), panele giren ve atlanan feature'lar.
- Audit'in yüksek ve orta önemdeki bulgularından modellemeyi etkileyenler.

## Modelleme için öneriler
- Hangi dönüşüm ve hedef uygun, hangi rejim dummy'leri gerekli, hangi feature'lar aynı bilgiyi taşıyor (collinear).

## Sınırlar
- Seri kodu bekleyen ve provider'ı olmayan kaynaklar, proxy uyarıları.
```

## Türkiye verisini yorumlarken
- **Rejimler**: 2018-08 kur şoku, 2020-03 COVID, 2021-12 kur şoku ve KKM, 2023-06 sonrası parasal
  sıkılaştırma, 2026-01 TÜFE baz değişikliği. Audit'teki structural break'leri bu dönemlerle ilişkilendir;
  nedensellik iddia etme ("bu dönemle eşzamanlı" ifadesini kullan).
- **Kur (`log_diff`)**: aylık log değişim ≈ aylık % değişim. Ortalama düşük, kuyruklar kalındır; outlier'lar
  genellikle şok aylarına denk gelir ve veri hatası değildir.
- **Enflasyon (`yoy_pct`)**: 12 aylık değişim olduğu için yön değişimleri gecikmeli görünür. 2022'deki zirve
  outlier gibi durur ama gerçektir; modelde bir rejim değişkeniyle ele alınmalıdır.
- **Faizler (seviye)**: politika döngüleri structural break olarak görünür. Ağaç tabanlı modellerde seviye,
  doğrusal modellerde fark tercih edilebilir.
- **Güven endeksleri**: seviyenin kendisi anlamlıdır; 100 eşiği iyimserliği kötümserlikten ayırır
  (TÜİK/TCMB tanımı).

## Dil
- Kısa, açık ve etken cümleler kur; resmi ve analitik bir üslup kullan. Teknik terimleri İngilizce bırak,
  eki kesme işaretiyle ekle (feature'lar, outlier'lar); Türkçe karşılık uydurma.
- Rakamları Türkçe biçimde yaz (%31,5; 2,4 puan; 2026-09). Yüzde ile yüzde puanı ayır.
- Belirsizliği açıkça yaz: "seri 2026-06'dan beri güncellenmiyor" net bir ifadedir, "veride sorun var" ise muğlaktır.
- Yatırım ya da finansal tavsiye verme; analizi veri ve modelleme bağlamında tut.
