# Rapor yazım kılavuzu

`narrative.md` raporun yönetici özeti bölümüne yerleşir. Okur, raporun geri kalanını okumadan bu bölümden pazarın
yapısını, odak oyuncunun konumunu ve karar için önemli riskleri anlayabilmelidir.

Bu dosya aynı zamanda projede kullanıcıya görünen bütün metinler (raporlar, hata mesajları, CLI/MCP açıklamaları,
skill belgeleri) için kanonik dil standardıdır.

## Yapı
```markdown
## Ana bulgu
Tek paragraf: odak oyuncunun konumu, trendi ve bunun en önemli nedeni.

## Pazar
Büyüklük ve büyüme (birim ve nominal/reel ayrımıyla), yoğunlaşmanın düzeyi ve yönü, pazardaki pay hareketliliği.

## Konum ve trend
Pay, sıra, göreli pay, yıllık değişim ve istatistiksel trend; pazara göre büyüme farkı.

## Rakipler
Doğrudan rakipler ve gerekçeleri; payın hangi oyunculardan geldiğine ilişkin kanıt (substitution correlation,
shift-share ayrıştırması) ve potansiyel rakipler.

## Segment ve kaynak ayrıştırması
Pay değişiminin segment içi rekabetten mi, segment karmasından mı kaynaklandığı.

## Makro koşullar
Pazar büyümesiyle ilişkili makro değişkenler ve sektör varsayımları (A# kimlikleriyle).

## Riskler ve izlenecek göstergeler
Konumu değiştirebilecek gelişmeler; her biri için izlenecek somut gösterge ve kaynağı.

## Veri sınırları
Coverage, eksik dönemler, tanım değişiklikleri, birleşmeler.
```

## Dil standardı
Rapor kurumsal bir araştırma notu üslubuyla yazılır: nesnel, ölçülü, kesin tanımlı ve kaynaklı. Metin, Türk bir
veri bilimcinin doğal olarak yazacağı akıcı Türkçedir; çeviri kokan, kalıp cümleler kurulmaz.

**Teknik terimler İngilizce kalır ve Türkçeleştirilmez.** feature, feature engineering, look-ahead, leakage,
outlier, winsorize, scaling, imputation, lag, rolling mean, EWM, coverage, HHI, CR-k, shift-share gibi terimler için uydurma Türkçe karşılık kullanılmaz. Ek gerekiyorsa kesme işaretiyle
bağlanır: "feature'lar", "outlier'ları", "HHI'ye". Alanın gündelik sözcükleri ise Türkçedir: pazar
payı, oyuncu, sektör, segment, dönem, enflasyon, kur, faiz, varsayım, bulgu. Kurum ve veri seti adları (TCMB, EVDS,
TÜİK, ODMD, TSB, BTK, EPDK, KAP) olduğu gibi yazılır.

| Kullanılmaz | Kullanılır |
|---|---|
| "B uçuşta, C çakıldı" | "B'nin payı bir yılda 2,5 puan arttı; C'nin payı 1,2 puan azaldı." |
| "Pazar çok yoğun" | "HHI 2.150 ile yüksek yoğunlaşma eşiğinin (2.000) üzerindedir." |
| "B, C'nin payını yiyor" | "B'nin pay değişimleri C'ninkilerle ters yönlüdür (r = −0,64, n = 60); bu, doğrudan rekabetle tutarlıdır." |
| "Bayat veri", "ölü seri" | "Son gözlem Haziran 2026'ya aittir; sonraki dönemler henüz yayımlanmamıştır." |
| "Kesinlikle", "açıkça görülüyor ki" | "Veriler … göstermektedir", "… ile tutarlıdır" |
| "Muhtemelen kampanya yüzünden" | "Artış kampanya döneminde gerçekleşmiştir (yorum, düşük güven); kampanya etkisi ayrıştırılamamıştır." |
| "Pay %2,5 arttı" (pay farkı için) | "Pay 2,5 puan artarak %24,0'dan %26,5'e yükseldi." |
| "Aykırı değerler kırpıldı", "kaydırma-pay etkisi" | "Outlier'lar winsorize edildi", "shift-share ayrıştırmasında pay etkisi" |
| "Öznitelik mühendisliği" | "Feature engineering" |
| "Pazar yoğunlaşmasında bir artış görülmüştür" (çeviri kokan) | "HHI bir yılda 120 puan arttı." |

Kurallar:
- Her sayıya dönem ve birim eklenir; yüzde (%) ile yüzde puan (puan) karıştırılmaz.
- Ondalık ayırıcı virgül, binlik ayırıcı noktadır (%26,5; 1.448; −0,64; 2,4 puan).
- Ünlem, retorik soru, argo ve mecaz kullanılmaz. Cümleler kısa ve açıktır.
- Nedensellik yalnızca birden fazla bağımsız kanıt varsa ve "… ile tutarlıdır" biçiminde ifade edilir.
- Yatırım tavsiyesi verilmez; sonuçlar karar destek bulgusu olarak sunulur.
- Kullanıcının onaylamadığı varsayımlara dayanan cümleler koşullu kipte yazılır ve varsayım kimliği verilir
  (ör. "Pazar tanımı (A1) geçerliyse …").
