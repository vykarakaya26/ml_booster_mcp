# Rakip belirleme çerçevesi

Rakip seti iki tür kanıtın birleşimidir. **Veriye dayalı kanıt** (`share.json` → `competitors.candidates`),
oyuncuların pazarda gerçekte nasıl etkileştiğini gösterir. **Nitel değerlendirme** ise bu etkileşimin nedenini ve
veride henüz görünmeyen rekabet baskısını açıklar. Yalnızca birine dayanan rakip seti eksik kalır: veri yeni
girenleri ve ikame ürünleri göstermez, nitel değerlendirme ise ölçeği ve yönü abartabilir.

## Rakip türleri
| Tür (`tier`) | Tanım | Örnek kanıt |
|---|---|---|
| `direct` (doğrudan) | Aynı müşteri ihtiyacını aynı ürün kategorisi, fiyat bandı ve kanalda karşılar. | Ters yönlü pay değişimi, yüksek segment örtüşmesi, ortak fiyat bandı. |
| `indirect` (dolaylı) | Aynı ihtiyacı farklı bir ürün, kanal ya da iş modeliyle karşılar. | Ürün ikamesi (ör. bankacılıkta katılım bankası ile mevduat; perakendede indirim marketi ile süpermarket). |
| `potential` (potansiyel) | Bugün payı düşük ya da veride yok, ancak rekabet baskısı yaratma olasılığı var. | Yeni giriş duyurusu, ithalat artışı, lisans başvurusu, yeni kapasite yatırımı. |

## Değerlendirme ölçütleri
Her aday için aşağıdaki boyutlarda örtüşmeyi değerlendir ve en güçlü iki ya da üç gerekçeyi `basis` alanına yaz:
1. **Ürün ve hizmet kapsamı**: aynı kategori ve segmentler.
2. **Fiyat bandı ve konumlandırma**: ekonomik, orta ya da üst segment.
3. **Müşteri segmenti**: bireysel, kurumsal, KOBİ; demografik ve coğrafi hedef.
4. **Kanal**: bayi, şube, çevrim içi, acente, doğrudan satış.
5. **Coğrafya**: ulusal, bölgesel ya da il bazında varlık.
6. **Ölçek ve kapasite**: pay, üretim kapasitesi, dağıtım ağı.
7. **İş modeli ve maliyet yapısı**: yerli üretim ya da ithalat, kur duyarlılığı, finansman koşulları.

Stratejik gruplar (benzer fiyat bandı ve kanal stratejisi izleyen oyuncular) belirlenirse raporda rekabetin en
yoğun olduğu grup ve odak oyuncunun bu gruptaki yeri belirtilir.

## Kaynaklar
- Şirketlerin KAP bildirimleri, faaliyet raporları ve yatırımcı sunumları (şirket beyanı olarak).
- Sektör birliklerinin yayınları (ODMD, TSB, TBB, BTK, EPDK).
- Rekabet Kurumu kararları: ilgili pazar tanımı ve rakip tespiti için özellikle değerlidir.
- Şirketlerin fiyat listeleri ve ürün sayfaları (tarihiyle birlikte).

Kaynağı olmayan nitel yargı `confidence: low` olarak işaretlenir ve raporda koşullu kipte yazılır.

## `competitors.json` schema'sı
```json
{
  "focus": "Marka B",
  "as_of": "2026-10-31",
  "competitors": [
    {
      "entity": "Marka C",
      "tier": "direct",
      "basis": ["aynı segment (C-SUV)", "benzer fiyat bandı", "ortak bayi bölgeleri"],
      "evidence": "2026 model yılı fiyat listelerinde aynı bantta konumlanıyor.",
      "source_url": "https://...",
      "confidence": "medium"
    }
  ],
  "notes": "İthal elektrikli araç markaları potansiyel rakip olarak izlenmelidir."
}
```
- `entity`, pay tablosundaki adla birebir aynı yazılır; böylece rapor veriyi ve nitel kanıtı aynı satırda birleştirir.
- `tier`: `direct` | `indirect` | `potential`. `confidence`: `high` | `medium` | `low`. Bu değerler kod tarafındaki
  sabit anahtarlardır ve İngilizce yazılır; raporda Türkçe etiketleriyle gösterilir.
- `source_url` isteğe bağlıdır; verilirse http(s) olmalıdır. Rapor script'i schema'yı doğrular ve hatalı dosyada durur.
