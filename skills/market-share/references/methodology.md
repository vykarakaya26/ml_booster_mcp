# Yöntem ve yorum kuralları

Hesaplamalar `mlbooster.domain.services.market_share` modülündedir ve testlerle güvence altındadır. Bu belge her
ölçütün tanımını, hangi koşulda kullanıldığını ve nasıl yorumlanacağını açıklar.

## 1. Pay tabanı
| Durum | Taban | Gerekçe |
|---|---|---|
| En az iki yıllık aylık ya da çeyreklik **akım** verisi (satış, prim) | Son 12 ay toplamı (`ttm`) | Mevsimsellik ve kampanyadan kaynaklanan volatilite giderilir; yıllık değişim doğrudan okunur. |
| **Stok** verisi (abone, mevduat, kredi) | Dönemlik (`period`) | Stok zaten birikimlidir; toplanmaz. |
| Yalnızca yüzde pay yayımlanıyorsa | Dönemlik | Tutar olmadığı için pazar büyümesi ve shift-share ayrıştırması hesaplanmaz. |

Pazar toplamı verilmediyse paylar listelenen oyuncuların toplamına göre hesaplanır. Bu durumda "pay", "listelenen
oyuncular içindeki pay" anlamına gelir ve rapor bunu belirtir.

## 2. Yoğunlaşma
- **HHI** = Σ sᵢ² (sᵢ yüzde pay; 0–10.000). AB Yatay Birleşme Kılavuzu (2004/C 31/03) eşikleri: 1.000'in altı
  düşük, 1.000–2.000 orta, 2.000'in üstü yüksek yoğunlaşma. Rekabet Kurumu'nun yatay birleşme kılavuzu da benzer
  eşikler kullanır `[doğrula]`.
- **Aralık**: Listelenmeyen pay (coverage dışındaki kısım) bilinmiyorsa alt sınır bu kısmın çok sayıda küçük
  oyuncudan, üst sınır ise tek bir oyuncudan oluştuğunu varsayar. İki sınır farklı sınıfa düşüyorsa sınıf kesin
  olarak verilmez.
- **CR3, CR5**: en büyük üç ve beş oyuncunun pay toplamı. HHI ile birlikte okunur; aynı CR3 değeri, liderin
  ağırlığına göre çok farklı rekabet yapılarına karşılık gelebilir.
- **Eşdeğer oyuncu sayısı** = 10.000 / HHI. Pazarın kaç eşit büyüklükte oyuncuya denk geldiğini gösterir.
- **Instability index (Hymer–Pashigian)** = ½ Σ |sᵢ,t − sᵢ,t−1yıl|. Bir yıl içinde el değiştiren pay miktarıdır
  (yüzde puan). Yüksek değer, yoğunlaşma düzeyinden bağımsız olarak rekabetin canlı olduğunu gösterir.

## 3. Oyuncu dinamikleri
- **Yıllık değişim (puan)**: bir yıl önceki aynı döneme göre pay farkı.
- **Trend**: son `trend_years` yılın dönemlik paylarına doğrusal regresyon. Eğim yüzde puan/yıl olarak verilir.
  "Kazanıyor" ya da "kaybediyor" sınıflandırması için **iki koşul birlikte** aranır: |t| ≥ 2 (istatistiksel
  anlamlılık) ve |eğim| ≥ 0,2 puan/yıl (ekonomik önem). Diğer durumlar "yatay" sayılır. Trend son 12 ay paylarıyla
  değil dönemlik paylarla tahmin edilir; üst üste binen toplamlar t istatistiğini olduğundan yüksek gösterir.
- **Göreli pay**: oyuncunun payı ÷ en büyük rakibinin payı. 1'in üstü liderliği, 0,5'in altı belirgin bir ölçek
  farkını gösterir.
- **Pazara göre büyüme farkı (puan)**: oyuncunun yıllık büyümesi − pazarın yıllık büyümesi. Pozitif fark payın
  büyüme yoluyla arttığını gösterir; pazar daralırken pozitif fark, konumun başarıyla savunulduğu anlamına gelir.

## 4. Rakip seti (veriye dayalı)
Veriye dayalı aday listesi, aşağıdaki gerekçelerden en az birini taşıyan oyunculardan oluşur:
1. **Pay sıralamasında komşuluk**: odak oyuncunun hemen üstündeki ve altındaki oyuncu.
2. **Pazar lideri**: odak oyuncu lider değilse.
3. **Substitution correlation**: dönemlik pay değişimleri arasındaki korelasyon r ≤ −0,2; en az 12 gözlem ve
   |r| ≥ 2/√n. Odak oyuncu pay kazandığında bu oyuncunun pay kaybettiğini gösterir. Son beş yıl kullanılır.
4. **Segment örtüşmesi**: segment karmalarının Bray–Curtis benzerliği (Σ min(pᵍ, qᵍ)) en az %60 ve oyuncu en
   yüksek benzerliğe sahip üç oyuncu arasında.

Adaylar önce gerekçe sayısına, ardından substitution correlation'ın gücüne göre sıralanır. Bu liste bir başlangıç
noktasıdır; nihai rakip seti nitel değerlendirmeyle birlikte belirlenir (`competitor-framework.md`).

## 5. Segment ayrıştırması (shift-share)
Odak oyuncunun toplam payı S = Σ wᵍ·sᵍ'dir (wᵍ segmentin pazardaki ağırlığı, sᵍ oyuncunun segment içindeki payı).
Bir yıllık değişim, orta nokta ağırlıklarıyla tam olarak ayrışır:

ΔS = Σ w̄ᵍ·Δsᵍ (**pay etkisi**) + Σ s̄ᵍ·Δwᵍ (**karma etkisi**)

Pay etkisi, oyuncunun segmentlerde rakiplerine karşı kazandığı ya da kaybettiği konumu ölçer. Karma etkisi ise
oyuncunun güçlü olduğu segmentlerin pazarda büyüyüp küçülmesini ölçer. Karma etkisi baskınsa pay artışı rekabet
üstünlüğünden çok talep kompozisyonundaki değişimden kaynaklanır; rapor bu ayrımı açıkça yazar.

## 6. Makro sürücüler
`--sector` verildiğinde pazarın yıllık büyümesi, sektör panelindeki her değişkenle 0–2 dönem lag'li korelasyonla
karşılaştırılır (en az 8 ortak gözlem). Panel, ortalama alınarak pay verisinin frekansına dönüştürülür. Bu bir ön
elemedir: korelasyon nedensellik göstermez; ortak trendler ve enflasyon yüksek korelasyon üretebilir. Güçlü bir
ilişki, model kurulmadan önce aday feature olarak not edilir.

## 7. Bilinen sorunlar ve önlemler
- **Öne çekilmiş talep**: ÖTV, faiz ya da kredi düzenlemesinden önceki aylarda satışlar geçici olarak yükselir. Bu
  dönemlerdeki tek aylık pay sıçramaları yorumlanmaz; son 12 ay tabanı etkiyi yumuşatır.
- **Birleşme ve devralmalar**: geçmiş pro forma birleştirilmezse pay artışı organik kazanım gibi görünür.
- **Kapsam değişikliği**: kaynağa yeni üye katılması ya da üyenin ayrılması pazar toplamını değiştirir. Coverage
  oranındaki ani değişim bu duruma işaret eder.
- **Nominal tutar**: tutar verisinde pazar büyümesi enflasyonu da içerir. Oyuncular arası pay karşılaştırması
  bundan etkilenmez, ancak pazar büyümesi reel büyüme gibi yorumlanmaz.
- **Kısa örneklem**: 12 gözlemden kısa serilerde trend ve substitution ölçütleri hesaplanmaz ya da anlamsız sayılır.
