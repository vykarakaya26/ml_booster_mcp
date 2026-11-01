# Rapor yazım kılavuzu

`narrative.md` raporun yönetici özeti bölümüne yerleşir. Okur, raporun geri kalanını okumadan bu bölümden
pazarın yapısını, odak oyuncunun konumunu ve karar için önemli riskleri anlayabilmelidir.

## Yapı
```markdown
## Ana bulgu
Tek paragraf: odak oyuncunun konumu, eğilimi ve bunun en önemli nedeni.

## Pazar
Büyüklük ve büyüme (birim ve nominal/reel ayrımıyla), yoğunlaşma düzeyi ve yönü, pazardaki pay hareketliliği.

## Konum ve eğilim
Pay, sıra, göreli pay, yıllık değişim ve istatistiksel eğilim; pazara göre büyüme farkı.

## Rakipler
Doğrudan rakipler ve gerekçeleri; payın hangi oyunculardan geldiğine ilişkin kanıt (ikame korelasyonu,
segment ayrıştırması) ve potansiyel rakipler.

## Segment ve kaynak ayrıştırması
Pay değişiminin segment içi rekabetten mi, segment karmasından mı kaynaklandığı.

## Makro koşullar
Pazar büyümesiyle ilişkili makro değişkenler ve sektör varsayımları (A# kimlikleriyle).

## Riskler ve izlenecek göstergeler
Konumu değiştirebilecek gelişmeler; her biri için izlenecek somut gösterge ve kaynak.

## Veri sınırları
Kapsam, eksik dönemler, tanım değişiklikleri, birleşmeler.
```

## Dil standardı
Rapor kurumsal bir araştırma notu üslubuyla yazılır: nesnel, ölçülü, kesin tanımlı ve kaynaklı.

| Kullanılmaz | Kullanılır |
|---|---|
| "B uçuşta, C çakıldı" | "B'nin payı bir yılda 2,5 puan arttı; C'nin payı 1,2 puan azaldı." |
| "Pazar çok yoğun" | "HHI 2.150 ile yüksek yoğunlaşma eşiğinin (2.000) üzerindedir." |
| "B, C'nin payını yiyor" | "B'nin pay değişimleri C'ninkilerle ters yönlüdür (r = −0,64, n = 60); bu, doğrudan rekabetle tutarlıdır." |
| "Bayat veri", "eski veri" | "Son gözlem Haziran 2026'ya aittir; sonraki dönemler henüz yayımlanmamıştır." |
| "Kesinlikle", "açıkça görülüyor ki" | "Veriler … göstermektedir", "… ile tutarlıdır" |
| "Muhtemelen kampanya yüzünden" | "Artış, kampanya döneminde gerçekleşmiştir (yorum, düşük güven); kampanya etkisi ayrıştırılamamıştır." |

Kurallar:
- Her sayıya dönem ve birim eklenir; yüzde ile yüzde puan karıştırılmaz.
- Ondalık ayırıcı virgül, binlik ayırıcı noktadır (%26,5; 1.448).
- Ünlem, retorik soru, argo ve mecaz kullanılmaz.
- Nedensellik yalnızca birden fazla bağımsız kanıt varsa ve "… ile tutarlıdır" biçiminde ifade edilir.
- Yatırım tavsiyesi verilmez; sonuçlar karar destek bulgusu olarak sunulur.
- Kullanıcı onaylamadığı varsayımlara dayanan cümleler koşullu kipte yazılır ve varsayım kimliği verilir.
