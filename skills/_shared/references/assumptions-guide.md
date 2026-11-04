# Girdi ve varsayım rehberi

Amaç: yorum içeren her çıktıda okur, neyin **veri**, neyin **varsayım**, neyin **model sonucu** ve neyin
**yorum** olduğunu ayırt edebilsin. Belirsizliği gizlemek okuru yanıltır; onu ölçülü ve izlenebilir
biçimde sunmak ise analizi güçlendirir.

## 1. Analizden önce sor (tek seferde en fazla 3–4 soru)
İstekten çıkarılabilenleri sorma. Yalnızca sonucu değiştirecek eksikleri sor ve seçenekli sun.

1. **Karar ve kullanım.** Bu analiz hangi kararı besliyor (fiyatlama, bütçe, karşılık, hedge, kurulacak model)?
2. **Hedef.** Tam olarak hangi seri? Seviye mi değişim mi? Ay ortalaması mı dönem sonu mu? Nominal mi
   reel mi (deflatör ne)?
3. **Horizon ve yenileme.** Feature'lar hedefin kaç ay sonrasına göre seçilecek? Ne sıklıkla güncellenecek?
4. **Senaryo girdileri** (kullanıcının sahip olduğu patikalar): politika faizi/AOFM, USD/TRY ve EUR/TRY,
   enflasyon beklentisi (kullanıcının kendisi ya da PKA), Brent ve ürün fiyatları, asgari ücret ve idari
   fiyatlar, vergi (ÖTV, KDV, ÖİV), makroihtiyati ayarlar.
5. **Sektöre özgü varsayımlar.** `sector-assumptions.md` dosyasındaki seçeneklerden. Bilinen tek seferlik
   olayları da sor (planlı tarife artışı, düzenleme değişikliği).
6. **Sonucu değiştirecek kanıt.** Hangi bulgunun değerlendirmeyi değiştireceğini baştan belirle.

Kullanıcı yanıt vermezse ya da "sen karar ver" derse sektör varsayılanlarıyla devam et ve onları
`confirmed: false` olarak kaydet. Asla kullanıcı adına onaylanmış gibi yazma.

## 2. Varsayım kaydı (`assumptions.json`)
Şablon: `../assets/assumptions.example.json`. Her varsayım bir satırdır:

| Alan | Zorunlu | Açıklama |
|---|---|---|
| `id` | ✓ | `A1`, `A2`… Raporda yorumlar bu kimlikle atıf yapar |
| `topic`, `statement` | ✓ | Konu ve tek cümlelik varsayım |
| `kind` | ✓ | `user` (kullanıcı söyledi), `sector` (sektör bilgisi), `default` (analist varsayılanı), `data` (veriden) |
| `confirmed` | ✓ | Kullanıcı onayladı mı? `user` her zaman true |
| `source` | önerilir | Belge, URL, "kullanıcı yanıtı" |
| `as_of` | önerilir | Bilginin tarihi (YYYY-AA-GG). 3 aydan eskiyse rapor "güncel değil — doğrulanmalı" olarak işaretler |
| `confidence` | önerilir | `low` / `medium` / `high` |
| `impact` | önerilir | Varsayım yanlışsa sonuç nasıl değişir? |
| `check` | önerilir | Nasıl doğrulanır? |

Regülasyon rakamlarını (kredi büyüme sınırı, ÖTV dilimi, trafik sigortası tavanı, eşel mobil oranı)
**sabit olarak gömme**. Bunları `source` ve `as_of` ile birlikte kaydet ve kullanıcıdan güncel değeri
onaylamasını iste.

## 3. Raporda ifadeleri etiketle
- **Veri**: yayımlanmış gözlem, EVDS kodu ya da ay ile. Örnek: "TÜFE yıllık %31,5 (2026-09 satırı)".
- **Varsayım (A#)**: "A2 geçerliyse…". Onaysız varsayıma dayanan sonuç koşullu yazılır.
- **Model**: istatistiksel hesap (ör. feature taraması, substitution correlation); yöntem ve örneklemle birlikte.
- **Yorum**: analistin çıkarımı, güven düzeyiyle. Örnek: "muhtemelen (orta güven)".

Bölüm sırası (merkez bankası pratiğine yakın):
1. Veri ve kalite notları
2. Varsayımlar
3. Bulgular
4. Senaryolar ve duyarlılık
5. Riskler ve sonucu çürütebilecek gelişmeler
6. Sınırlar

## 4. Senaryo ve duyarlılık
- Üç ila beş **iç tutarlı** senaryo kur: baz, olumsuz kur, dezenflasyon, politika şoku. Bir kur şoku,
  TÜFE'ye geçişkenliği ve politika tepkisiyle birlikte düşünülür.
- **Duyarlılık.** Varsayımları tek tek makul aralıkta oynat. Çıktıya etkisine göre sırala ve bu sırayı
  kayda `impact` olarak yaz.

## 5. Ölçülü dil
Olasılık sözcüklerini tutarlı kullan ve olasılık ile güveni ayrı söyle:

| İfade | Olasılık |
|---|---|
| neredeyse hiç | %1–5 |
| çok düşük | %5–20 |
| düşük | %20–45 |
| yaklaşık eşit | %45–55 |
| muhtemel | %55–80 |
| çok muhtemel | %80–95 |
| neredeyse kesin | %95–99 |

Ölçek ICD-203 tarzıdır; kaynak ikincildir.

Belirsizlik yüksekken ihtiyatlı ol; ancak bu ilkeyi mekanik biçimde uygulama.

## 6. Abartıya karşı kontrol listesi
- **Nedensellik yok.** Korelasyondan ya da Granger testinden sonra "etkiler" ifadesini kullanma;
  "ilişkili" ya da "örneklem içinde k ay öncü" gibi ifadeler kullan.
- **Spurious regression.** Non-stationary düzeylerle kurulan regresyonu cointegration testi olmadan yorumlama.
- **Çoklu deneme.** Denenen tüm spesifikasyonların sayısını raporla.
- **Dönem seçimi.** Başlangıç tarihini sonuçlara bakarak seçme. Tam dönemi ve rejim alt dönemlerini göster.
- **Nominal yanılsama.** Nominal TL büyümesine reel karşılığı olmadan "büyüme" ifadesini kullanma.
- **Baz etkisi.** Mekanik YoY düşüşünü politikaya bağlama.
- **Küçük rejim örneklemi.** Bir rejimde 36 aydan az gözleme dayanan sonucu "gösterge niteliğinde" diye
  etiketle `[sezgisel]`.
- **Gelecek değer.** Nokta tahmin ya da yatırım tavsiyesi verme; forecast bu ürünün kapsamı dışındadır.
