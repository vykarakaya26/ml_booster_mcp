# Pazar payı verisi: kaynaklar ve hazırlık

Bu belge, sektör bazında kamuya açık pazar payı verisinin nereden alınacağını ve analiz tablosuna nasıl
dönüştürüleceğini açıklar. Kaynak bilgileri katalogdaki (`src/mlbooster/catalog_data/sources/*.yaml`)
kayıtlarla tutarlıdır. Bu kaynakların hiçbiri için henüz otomatik sağlayıcı yoktur; veri kullanıcı
tarafından indirilir ya da yayımlanan tablolardan aktarılır. Yayın adları, kapsam ve gecikmeler değişebilir;
`[doğrula]` işaretli bilgiler kullanılmadan önce birincil kaynaktan kontrol edilmelidir.

## Sektör bazında kaynaklar

| Sektör | Kaynak | Ölçü | Frekans | Biçim | Dikkat edilecek noktalar |
|---|---|---|---|---|---|
| Otomotiv | ODMD aylık basın bülteni | Marka, model ve segment bazında perakende satış adedi | Aylık | PDF | Yalnızca üye distribütörleri kapsar `[doğrula]`; binek ve hafif ticari ayrı raporlanır; ÖTV ve kredi düzenlemesi öncesinde talep öne çekilebilir. |
| Sigorta | TSB prim üretimi | Şirket × branş prim üretimi (TL) | Aylık | XLSX, PDF | Nominaldir; branş tanımları (trafik, kasko, sağlık, hayat) şirketler arasında tutarlıdır. Hayat ve hayat dışı ayrı pazarlar olarak ele alınmalıdır. |
| Telekom | BTK Üç Aylık Pazar Verileri Raporu | Operatör bazında abone, gelir ve trafik payı | Çeyreklik | PDF | Abone sayısı stoktur (`--kind stock`); rapor gecikmesi uzundur. Bazı paylar yalnızca yüzde olarak verilir (`--measure share`). |
| Akaryakıt | EPDK petrol piyasası ve LPG sektör raporları | Dağıtıcı bazında satış (ton, m³) | Aylık, yıllık | PDF, XLSX | Ürün bazında (benzin, motorin, LPG) ayrı pazarlardır; segment kolonu olarak verilmelidir. |
| Bankacılık | TBB Veri Sistemi | Banka bazında aktif, nakdi kredi, mevduat, şube | Çeyreklik, yıllık | XLSX | Stoktur; kur etkisi YP kalemlerde payı değiştirir. Katılım bankaları ayrı birlikte raporlanır `[doğrula]`. |
| Perakende | KAP finansal tabloları (halka açık şirketler) | Şirket hasılatı (TL) | Çeyreklik | PDF, XBRL | Pazar toplamı yoktur; paylar listelenen şirketler arasında geçerlidir. Kart harcaması (BKM, TCMB) yalnızca talep göstergesidir, firma payı değildir. |
| Turizm, restoran | BKM ve TCMB sektörel kart harcaması | Sektör toplamı | Aylık | XLSX, EVDS | Firma bazında pay yoktur; pazar büyüklüğü ve talep göstergesi olarak kullanılır. |
| Konut | TÜİK konut satış istatistikleri | İl bazında satış adedi | Aylık | XLSX | Oyuncu değil bölge payıdır; `entity` olarak il kullanılır. |

Halka açık şirketlerin KAP'ta yayımladığı faaliyet raporları ve yatırımcı sunumları çoğu sektörde
şirketin kendi açıkladığı pazar payını içerir. Bu değerler şirket beyanıdır; analiz tablosuna değil,
nitel değerlendirmeye (`competitors.json`) kaynak olarak eklenir. KAP'ta toplu veri çekimi (scraping)
yapılmaz; yalnızca ilgili bildirim okunur.

## Tablo biçimi

```csv
entity,date,value,segment
Marka A,2026-09,12450,Binek
Marka A,2026-09,1830,Hafif ticari
TOPLAM,2026-09,98210,Binek
TOPLAM,2026-09,24877,Hafif ticari
```

- **entity**: oyuncu adı (en fazla 80 karakter). Aynı oyuncu bütün dönemlerde aynı yazımla yer almalıdır.
- **date**: `YYYY-AA` (aylık), `YYYY-Qn` (çeyreklik), `YYYY` (yıllık) ya da `YYYY-AA-GG`. Dönem sonuna çevrilir.
- **value**: adet, tutar ya da yüzde pay. Bir tabloda tek ölçü kullanılır. Negatif değer kabul edilmez.
- **segment** (isteğe bağlı): ürün, branş ya da bölge kırılımı. Segment verildiğinde kaydırma-pay ayrıştırması
  ve segment örtüşmesi hesaplanır.
- **TOPLAM** satırları (ad `--total-entity` ile değiştirilebilir): kaynak kurumun yayımladığı pazar toplamı.
  Segment varsa her segment için ayrı toplam verilir.
- Türkçe kolon adları da kabul edilir (`şirket`, `marka`, `dönem`, `tarih`, `değer`, `adet`, `kırılım`).
  Noktalı virgülle ayrılmış ve ondalık virgüllü CSV dosyaları otomatik tanınır.

## Hazırlık kontrol listesi
1. **Kapsam**: Kaynağın kapsamadığı oyuncular var mı (üye olmayanlar, ithalatçılar, küçük şirketler)?
   Varsa TOPLAM satırı kaynak toplamıdır ve kapsam oranı raporda bu bağlamda yorumlanır.
2. **Süreklilik**: Unvan değişiklikleri ve birleşmeler aynı oyuncu adı altında birleştirilmiştir; bu işlem
   `assumptions.json` dosyasına `kind: data` olarak yazılmıştır.
3. **Tanım değişiklikleri**: Kaynağın segment veya branş tanımı dönem içinde değiştiyse kırılma tarihi not edilir.
4. **Birim**: Tutar verisinin nominal olduğu, adet verisinin fiyat etkisi taşımadığı rapora yansıtılır.
5. **Kaynak atfı**: Her kaynağın adı ve yayın dönemi `--source` ile rapora eklenir; lisans koşulları
   katalogdaki `license` alanında yer alır.
