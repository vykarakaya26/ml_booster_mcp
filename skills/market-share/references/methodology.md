# Yöntem ve yorum kuralları

Hesaplamalar `mlbooster.domain.services.market_share` modülündedir ve testlerle güvence altındadır.
Bu belge her ölçütün tanımını, hangi koşulda kullanılacağını ve nasıl yorumlanacağını açıklar.

## 1. Pay tabanı
| Durum | Taban | Gerekçe |
|---|---|---|
| Aylık ya da çeyreklik **akım** verisi (satış, prim), en az iki yıllık seri | Son 12 ay toplamı (`ttm`) | Mevsimsellik ve kampanya kaynaklı oynaklık giderilir; yıllık değişim doğrudan okunur. |
| **Stok** verisi (abone, mevduat, kredi) | Dönemlik (`period`) | Stok zaten birikimlidir; toplanmaz. |
| Yalnızca yüzde pay yayımlanıyorsa | Dönemlik | Tutar olmadığı için pazar büyümesi ve segment ayrıştırması yapılmaz. |

Pazar toplamı verilmediyse paylar listelenen oyuncuların toplamına göre hesaplanır; bu durumda "pay"
ifadesi "listelenen oyuncular içindeki pay" anlamına gelir ve rapor bunu belirtir.

## 2. Yoğunlaşma
- **HHI** = Σ sᵢ² (sᵢ yüzde pay; 0–10.000). AB Yatay Birleşme Kılavuzu (2004/C 31/03) eşikleri: 1.000'in
  altı düşük, 1.000–2.000 orta, 2.000'in üstü yüksek yoğunlaşma. Rekabet Kurumu'nun yatay birleşme
  kılavuzu da benzer eşikler kullanır `[doğrula]`.
- **Aralık**: Listelenmeyen pay (kapsam dışı kısım) bilinmiyorsa alt sınır bu kısmın çok sayıda küçük
  oyuncudan, üst sınır tek bir oyuncudan oluştuğunu varsayar. İki sınır farklı sınıfa düşüyorsa sınıf
  kesin olarak verilmez.
- **CR3, CR5**: en büyük üç ve beş oyuncunun pay toplamı. HHI ile birlikte okunur: aynı CR3 değeri, liderin
  ağırlığına göre çok farklı rekabet yapılarına karşılık gelebilir.
- **Eşdeğer oyuncu sayısı** = 10.000 / HHI. Pazarın kaç eşit büyüklükteki oyuncuya denk geldiğini gösterir.
- **İstikrarsızlık endeksi (Hymer–Pashigian)** = ½ Σ |sᵢ,t − sᵢ,t−1yıl|. Bir yıl içinde el değiştiren pay
  miktarıdır (yüzde puan). Yüksek değer, yoğunlaşma düzeyinden bağımsız olarak rekabetin canlı olduğunu gösterir.

## 3. Oyuncu dinamikleri
- **Yıllık değişim (puan)**: bir yıl önceki aynı döneme göre pay farkı.
- **Eğilim**: son `trend_years` yılın dönemlik paylarına doğrusal regresyon. Eğim yüzde puan/yıl olarak
  verilir. "Kazanıyor" ya da "kaybediyor" sınıflandırması için **iki koşul birlikte** aranır: |t| ≥ 2
  (istatistiksel anlamlılık) ve |eğim| ≥ 0,2 puan/yıl (ekonomik önem). Diğer durumlar "yatay"dır.
  Eğilim son 12 ay paylarıyla değil dönemlik paylarla tahmin edilir; üst üste binen toplamlar t
  istatistiğini olduğundan yüksek gösterir.
- **Göreli pay**: oyuncunun payı ÷ en büyük rakibinin payı. 1'in üstü liderliği, 0,5'in altı belirgin bir
  ölçek farkını gösterir.
- **Pazara göre büyüme farkı (puan)**: oyuncunun yıllık büyümesi − pazarın yıllık büyümesi. Pozitif fark,
  payın büyüme yoluyla arttığını gösterir; pazar daralırken pozitif fark savunma başarısıdır.

## 4. Rakip seti (veriye dayalı)
Veriye dayalı aday listesi şu gerekçelerden en az birini taşıyan oyunculardan oluşur:
1. **Pay sıralamasında komşuluk**: odak oyuncunun hemen üstündeki ve altındaki oyuncu.
2. **Pazar lideri**: odak oyuncu lider değilse.
3. **İkame korelasyonu**: dönemlik pay değişimleri arasındaki korelasyon r ≤ −0,2, en az 12 gözlem ve
   |r| ≥ 2/√n. Odak oyuncu pay kazandığında bu oyuncunun pay kaybettiğini gösterir. Son beş yıl kullanılır.
4. **Segment örtüşmesi**: segment karmalarının Bray–Curtis benzerliği (Σ min(pᵍ, qᵍ)) en az %60 ve en
   yüksek üç oyuncu arasında.

Adaylar gerekçe sayısına, ardından ikame korelasyonunun gücüne göre sıralanır. Bu liste bir başlangıç
noktasıdır; nihai rakip seti nitel değerlendirmeyle birlikte belirlenir (`competitor-framework.md`).

## 5. Segment ayrıştırması (kaydırma-pay)
Odak oyuncunun toplam payı S = Σ wᵍ·sᵍ (wᵍ segmentin pazardaki ağırlığı, sᵍ oyuncunun segment içindeki payı).
Bir yıllık değişim orta nokta ağırlıklarıyla tam olarak ayrışır:

ΔS = Σ w̄ᵍ·Δsᵍ (**pay etkisi**) + Σ s̄ᵍ·Δwᵍ (**karma etkisi**)

Pay etkisi oyuncunun segmentlerde rakiplerine karşı kazandığı ya da kaybettiği konumu, karma etkisi ise
güçlü olduğu segmentlerin pazarda büyüyüp küçülmesini ölçer. Karma etkisi baskınsa pay artışı rekabet
üstünlüğünden çok talep kompozisyonundaki değişimden kaynaklanır; bu ayrım raporda açıkça yazılır.

## 6. Makro sürücüler
`--sector` verildiğinde pazarın yıllık büyümesi, sektör panelindeki her değişkenle 0–2 dönem gecikmeli
korelasyonla karşılaştırılır (en az 8 ortak gözlem). Panel, pay verisinin frekansına ortalama alınarak
dönüştürülür. Bu bir ön elemedir: korelasyon nedensellik göstermez, ortak trendler ve enflasyon yüksek
korelasyon üretebilir. Güçlü bir ilişki model kurulmadan önce açıklayıcı değişken adayı olarak not edilir.

## 7. Bilinen sorunlar ve önlemler
- **Öne çekilmiş talep**: ÖTV, faiz ya da kredi düzenlemesi öncesi aylarda satışlar geçici olarak yükselir.
  Tek aylık pay sıçramaları bu dönemlerde yorumlanmaz; son 12 ay tabanı etkiyi yumuşatır.
- **Birleşme ve devralmalar**: geçmiş pro forma birleştirilmezse pay artışı organik kazanım gibi görünür.
- **Kapsam değişikliği**: kaynağa yeni üye katılması ya da ayrılması pazar toplamını değiştirir. Kapsam
  oranındaki ani değişim bu duruma işaret eder.
- **Nominal tutar**: tutar verisinde pazar büyümesi enflasyon içerir. Oyuncular arası pay karşılaştırması
  etkilenmez, ancak pazar büyümesi reel büyüme gibi yorumlanmaz.
- **Kısa örneklem**: 12 gözlemden kısa serilerde eğilim ve ikame ölçütleri hesaplanmaz ya da anlamsız sayılır.
