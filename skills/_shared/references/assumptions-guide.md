# Girdi ve varsayım rehberi

Amaç: yorum içeren her çıktıda okur, neyin **veri**, neyin **varsayım**, neyin **model sonucu** ve neyin
**yorum** olduğunu ayırt edebilsin. Yanıltıcı olan belirsizliği gizlemektir; güçlendirici olan, onu
ölçülü ve izlenebilir vermektir.

## 1. Analizden önce sor (tek seferde en fazla 3–4 soru)
İstekten çıkarılabilenleri sorma. Yalnızca sonucu değiştirecek eksikleri sor ve seçenekli sun.

1. **Karar ve kullanım.** Bu analiz hangi kararı besliyor (fiyatlama, bütçe, karşılık, hedge, model
   seçimi)? Fazla ya da eksik tahminin maliyeti simetrik mi? Asimetrikse kantil kaybı kullanılır.
2. **Hedef.** Tam olarak hangi seri? Seviye mi değişim mi? Ay ortalaması mı dönem sonu mu? Nominal mi
   reel mi (deflatör ne)?
3. **Ufuk ve yenileme.** Kaç ay ileri? Ne sıklıkla güncellenecek?
4. **Senaryo girdileri** (kullanıcının sahip olduğu patikalar): politika faizi/AOFM, USD/TRY ve EUR/TRY,
   enflasyon beklentisi (kullanıcının kendisi ya da PKA), Brent ve ürün fiyatları, asgari ücret ve idari
   fiyatlar, vergi (ÖTV, KDV, ÖİV), makroihtiyati ayarlar.
5. **Sektöre özgü varsayımlar.** `sector-assumptions.md` dosyasındaki seçeneklerden. Bilinen tek seferlik
   olayları da sor (planlı tarife artışı, düzenleme değişikliği).
6. **Çıktı biçimi ve kabul edilebilir belirsizlik.** Nokta, aralık, senaryo ya da olasılık?
7. **Fikrini ne değiştirir?** Değerlendirme ölçütünü baştan belirle.

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
| `as_of` | önerilir | Bilginin tarihi (YYYY-AA-GG). 3 aydan eskiyse rapor "bayat — doğrula" diye işaretler |
| `confidence` | önerilir | `low` / `medium` / `high` |
| `impact` | önerilir | Varsayım yanlışsa sonuç nasıl değişir? |
| `check` | önerilir | Nasıl doğrulanır? |

Regülasyon rakamlarını (kredi büyüme sınırı, ÖTV dilimi, trafik sigortası tavanı, eşel mobil oranı)
**sabit olarak gömme**. Bunları `source` ve `as_of` ile birlikte kaydet ve kullanıcıdan güncel değeri
onaylamasını iste.

## 3. Raporda ifadeleri etiketle
- **Veri**: yayımlanmış gözlem, EVDS kodu ya da ay ile. Örnek: "TÜFE yıllık %31,5 (2026-09 satırı)".
- **Varsayım (A#)**: "A2 geçerliyse…". Onaysız varsayıma dayanan sonuç koşullu yazılır.
- **Model**: tahmin ya da backtest sonucu; yöntem, örneklem ve belirsizlikle birlikte.
- **Yorum**: analistin çıkarımı, güven düzeyiyle. Örnek: "muhtemelen (orta güven)".

Bölüm sırası (merkez bankası pratiğine yakın):
1. Veri ve kalite notları
2. Varsayımlar
3. Ufuk başına sonuçlar ve çıtalar
4. Senaryolar ve duyarlılık
5. Riskler ve "bunu ne çürütür"
6. Sınırlar

## 4. Senaryo ve duyarlılık
- Üç ila beş **iç tutarlı** senaryo kur: baz, olumsuz kur, dezenflasyon, politika şoku. Bir kur şoku,
  TÜFE'ye geçişkenliği ve politika tepkisiyle birlikte düşünülür.
- Senaryo tahmin aralıkları, girdinin kendi belirsizliğini içermez; bu yüzden **dardır**. Bunu açıkça
  yaz ([FPP3 §7.6](https://otexts.com/fpp3/forecasting-regression.html)).
- **Duyarlılık.** Varsayımları tek tek makul aralıkta oynat. Çıktıya etkisine göre sırala ve bu sırayı
  kayda `impact` olarak yaz.
- **Belirsizlik gösterimi.** Varsayılan, ufuk başına backtest hata bandıdır. Rejim değiştiyse bandı
  genişlet ve bunu belirt (BoE fan chart, IMF WEO 50/70/90 bantları, Fed RMSE bantları).

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

Belirsizlik yüksekken temkinli ol: Armstrong vd.'nin
["Golden Rule"](https://faculty.wharton.upenn.edu/wp-content/uploads/2016/10/Golden-Rule-of-Forcasting_Be-Conservative_2.pdf)
kuralı. Bunu mekanik uygulama.

## 6. Abartıya karşı kontrol listesi
- **Nedensellik yok.** Korelasyondan ya da Granger testinden sonra "etkiler" deme; "ilişkili",
  "örneklem içinde k ay önden gidiyor" de.
- **Sahte regresyon.** Durağan olmayan seviyelerle regresyonu eşbütünleşme testi olmadan yorumlama.
- **Çoklu deneme.** Denenen tüm spesifikasyonların sayısını raporla; final testi bir kez yap.
- **Dönem seçimi.** Başlangıç tarihini sonuçlara bakarak seçme. Tam dönemi ve rejim alt dönemlerini göster.
- **Nominal yanılsama.** Nominal TL büyümesine reel karşılığı olmadan "büyüme" deme.
- **Baz etkisi.** Mekanik YoY düşüşünü politikaya bağlama.
- **Küçük rejim örneklemi.** Bir rejimde 36 aydan az gözleme dayanan sonucu "gösterge niteliğinde" diye
  etiketle `[sezgisel]`.
- **İyileşme iddiası.** Yalnızca çıtaya karşı, ufuk başına ve mümkünse DM testiyle yap.
- **Gelecek değer.** Kullanıcı açıkça senaryo istemedikçe nokta tahmin ya da yatırım tavsiyesi verme.
