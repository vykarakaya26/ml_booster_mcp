# Sektör varsayımları (nitel)

Bu tablo **ne sorulacağını** ve **nereden kontrol edileceğini** söyler; güncel değerleri vermez.
Regülasyon rakamları sık değişir. Kullanıcıya sor ya da birincil kaynaktan doğrula, sonra
`assumptions.json` dosyasına `source` ve `as_of` ile kaydet. 2025–26 politika bilgilerinin bir kısmı
haber ve aracı kurum özetlerine dayanır `[doğrula]`.

## Tüm sektörler için ortak varsayımlar
- **Politika faizi ve efektif fonlama maliyeti patikası.** Repo ihalesine ara verilen dönemlerde AOFM
  politika faizinin üstündedir.
- **USD/TRY ve EUR/TRY patikası, kurdan TÜFE'ye geçişkenlik.** Geçişkenlik duruma bağlıdır ve yüksek
  enflasyon rejiminde daha yüksektir
  ([TCMB blog](https://tcmb.gov.tr/wps/wcm/connect/blog/en/main+menu/analyses/exchange+rate+pass-through)).
- **Asgari ücret kararları.** Yıllık yapılır, ara artış olabilir.
- **İdari fiyatlar.** Enerji, tütün ve alkol, yeniden değerleme oranıyla endekslenen harçlar.
- **Enflasyon beklentilerinin çapalanması.** PKA'dan izlenir.

## Sektörler

| Sektör | Ana belirleyiciler | Açıkça yazılacak varsayımlar | Kontrol kaynağı |
|---|---|---|---|
| **Bankacılık** | Mevduat ve kredinin yeniden fiyatlanma hızı; AOFM; TL kredi büyüme sınırları (dönem dönem değişen pencereler ve KOBİ/ihracat/yatırım istisnaları); zorunlu karşılıklar; KKM'nin kalıntısı; BDDK tüketici/taşıt kredisi vade ve LTV sınırları, kart taksitleri; TÜFE'ye endeksli menkul kıymet geliri; aktif kalitesinin faiz döngüsünü gecikmeli izlemesi | Sınırlar ve istisnaların patikası; mevduat betası; NPL gecikmesi; enflasyon muhasebesinin (TMS 29) uygulanıp uygulanmadığı `[doğrula]` | TCMB makroihtiyati basın duyuruları; [BDDK](https://www.bddk.org.tr) kurul kararları |
| **Sigorta** | Trafik sigortası primleri SEDDK tavanıyla ve aylık azami artışla sınırlı; hasar enflasyonu (ithal parça → kur, işçilik); kasko ↔ araç fiyatları; yatırım getirisi ↔ faiz; sağlık enflasyonu | Tavan ve aylık artış patikası; hasar maliyetine geçişkenlik gecikmesi; yatırım getirisi patikası | [SEDDK](https://www.seddk.gov.tr) genelgeleri |
| **Telekom** | BTK, Türk Telekom'un *toptan* tarifelerini düzenler (yılda iki güncelleme `[doğrula]`); *perakende* tarifeler onaya tabi değildir; döviz cinsi yatırım harcaması; ÖİV; 2026 TÜFE sepetinde haberleşme grubunun ağırlığı değişti | Perakende fiyat artışı ↔ TÜFE (fiyatlama gücü); toptan güncelleme patikası; yatırım harcamasının kur payı | [BTK kurul kararları](https://www.btk.gov.tr) |
| **Otomotiv** | ÖTV dilimleri (matrah, motor hacmi, elektrikli araç; matrah eşikleri dönemsel güncellenir); ithal araçta EUR/TRY geçişkenliği; BDDK taşıt kredisi vade/LTV kademeleri (fatura tutarına göre); kredi maliyeti; ihracat talebi; menşe bazlı ek gümrük vergisi `[doğrula]` | Dilim güncellemelerinin zamanlaması (öne çekme ve uçurum etkileri); LTV/vade kademeleri; kur patikası | Resmî Gazete; [GİB](https://www.gib.gov.tr) ÖTV (II) listeleri; BDDK |
| **Akaryakıt** | Pompa fiyatı = uluslararası *ürün* fiyatı (doğrudan Brent değil) × kur + litre başına sabit ÖTV + KDV + dağıtıcı ve bayi marjı; **eşel mobil** uygulandığında geçişkenlik kırılır `[doğrula]`; EPDK marj denetimi; ÖTV endeksleme takvimi | Eşel mobilin aktif olup olmadığı ve oranı; ÖTV güncelleme tarihleri; marj rejimi; talep esnekliği | [EPDK](https://www.epdk.gov.tr); Resmî Gazete |

## Birincil kaynaklar
- **TCMB Enflasyon Raporu** (üç ayda bir): varsayımlar, ara hedefler, risk senaryoları.
- **Piyasa Katılımcıları Anketi** (aylık): TÜFE, politika faizi ve kur beklentileri; 12 ve 24 ay
  sonrası TÜFE olasılık dağılımları. Birincil veri tcmb.gov.tr ve EVDS'dedir.
- **TÜİK**: TÜFE. 2026-01'den itibaren 2025=100 bazı ve COICOP-2018 kullanılır; geriye dönük seriler
  2005'ten başlar.
- **BDDK, SEDDK, BTK, EPDK** kurul kararları; **GİB** ÖTV tabloları; **Resmî Gazete** Cumhurbaşkanı
  Kararları.

## Varsayılan kullanım (kullanıcı yanıt vermediğinde)
Sektörün satırındaki her "açıkça yazılacak varsayım" için kayda bir satır ekle:
`kind: sector`, `confirmed: false`, `confidence: low`. `statement` alanına sayısal değer değil, yön
yaz. Örnek: "Kredi büyüme sınırları sürüyor; kredi hacmi feature'ları bu dönemde baskılı." Raporda
bu varsayımlara dayanan her yorum koşullu yazılır.
