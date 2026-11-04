# Pazar payı verisi: kaynaklar ve hazırlık

Bu belge, kamuya açık pazar payı verisinin sektör bazında nereden alınacağını ve analiz tablosuna nasıl
dönüştürüleceğini açıklar. Kaynak bilgileri katalogdaki (`src/mlbooster/catalog_data/sources/*.yaml`) kayıtlarla
tutarlıdır. Bankacılıktaki BDDK banka grubu verisi dışında bu kaynaklar için henüz otomatik provider yoktur; veri
kullanıcı tarafından indirilir ya da yayımlanan tablolardan aktarılır. Yayın adları, kapsam ve publication lag değişebilir; `[doğrula]` işaretli bilgiler
kullanılmadan önce birincil kaynaktan kontrol edilmelidir.

## Sektör bazında kaynaklar

| Sektör | Kaynak | Ölçü | Frekans | Biçim | Dikkat edilecek noktalar |
|---|---|---|---|---|---|
| Otomotiv | ODMD perakende satışlar (otomatik: `market-share --source odmd --series ODMD.OTOMOBIL`) | Marka bazında otomobil ve hafif ticari araç satış adedi, yerli/ithal kırılımıyla; pazar toplamı dahil | Aylık (2015-01'den bu yana; 2017-02 eksik) | xlsx (provider), PDF bülten | Akımdır. `ODMD.TOPLAM`, `ODMD.HAFIF_TICARI`, `ODMD.OTOMOBIL.YERLI`/`.ITHAL` serileri de vardır. Tablo bütün pazarı kapsar; Tesla verileri ODMD tahminidir. Ham dosyaların yeniden dağıtımı ve ticari kullanım için ODMD'nin izni gerekir. Publication lag 1–4 gündür. ÖTV ve kredi düzenlemelerinden önce talep öne çekilebilir. |
| Sigorta | TSB prim üretimi | Şirket × branş prim üretimi (TL) | Aylık | XLSX, PDF | Nominaldir; branş tanımları (trafik, kasko, sağlık, hayat) şirketler arasında tutarlıdır. Hayat ve hayat dışı ayrı pazarlar olarak ele alınmalıdır. |
| Telekom | BTK Üç Aylık Pazar Verileri Raporu | Operatör bazında abone, gelir ve trafik payı | Çeyreklik | PDF | Abone sayısı stoktur (`--kind stock`); publication lag uzundur. Bazı paylar yalnızca yüzde olarak verilir (`--measure share`). |
| Akaryakıt | EPDK petrol piyasası ve LPG sektör raporları | Dağıtıcı bazında satış (ton, m³) | Aylık, yıllık | PDF, XLSX | Her ürün (benzin, motorin, LPG) ayrı bir pazardır; segment kolonu olarak verilmelidir. |
| Bankacılık | BDDK Aylık Bülten (otomatik: `market-share --source bddk --series BDDK.AKTIF`) | Banka grubu bazında aktif, kredi, mevduat, özkaynak (milyon TL); sektör toplamı dahil | Aylık (2002'den bu yana) | JSON (provider) | Stoktur. Gruplar: kamu, yerli özel ve yabancı mevduat bankaları, katılım, kalkınma ve yatırım; `*.SAHIPLIK` serileri kamu, yerli özel ve yabancı kırılımını verir. Ticari kullanım BDDK'nın yazılı iznine tabidir. Publication lag yaklaşık 30 gündür. |
| Bankacılık | TBB Veri Sistemi | Banka bazında aktif, nakdi kredi, mevduat, şube | Çeyreklik, yıllık | XLSX | Stoktur; kur etkisi YP kalemlerde payı değiştirir. Katılım bankaları ayrı bir birlik tarafından raporlanır `[doğrula]`. |
| Perakende | KAP finansal tabloları (halka açık şirketler) | Şirket hasılatı (TL) | Çeyreklik | PDF, XBRL | Pazar toplamı yoktur; paylar listelenen şirketler arasında geçerlidir. Kart harcaması (BKM, TCMB) firma payı değil, yalnızca talep göstergesidir. |
| Turizm, restoran | BKM ve TCMB sektörel kart harcaması | Sektör toplamı | Aylık | XLSX, EVDS | Firma bazında pay yoktur; pazar büyüklüğü ve talep göstergesi olarak kullanılır. |
| Konut | TÜİK konut satış istatistikleri | İl bazında satış adedi | Aylık | XLSX | Oyuncu değil bölge payıdır; `entity` olarak il kullanılır. |

Halka açık şirketlerin KAP'ta yayımladığı faaliyet raporları ve yatırımcı sunumları çoğu sektörde şirketin kendi
açıkladığı pazar payını içerir. Bu değerler şirket beyanıdır; analiz tablosuna değil, nitel değerlendirmeye
(`competitors.json`) ya da yayımlanmış pay bulgularına kaynak olarak eklenir. KAP'ta toplu veri çekimi (scraping) yapılmaz;
yalnızca ilgili bildirim okunur.

## Yayımlanmış pay bulguları

Oyuncu düzeyinde kaynak yoksa ya da hesaplanan pay başka bir kaynakla sınanacaksa `share-research` agent'ı web'de yayımlanmış
rakamları arar. Politika `src/mlbooster/catalog_data/evidence_sources.yaml` dosyasındadır; kaynak türünü alan adı belirler ve listede
olmayan site reddedilir.

| Kaynak türü | Kapsam | Koşul |
|---|---|---|
| Resmî (Tier 1) | Rekabet Kurumu kararları ve sektör raporları, BDDK, EPDK, BTK, SPK, TCMB, TÜİK, SEDDK, Resmî Gazete | Güven düzeyi yüksek olabilir |
| Şirket ya da dernek (Tier 2) | Faaliyet raporu, yatırımcı sunumu, sektör dernekleri | Şirket beyanıdır; güven en fazla orta. Katalogda olmayan şirket sitesi yalnızca alan adı marka adıyla başlıyorsa kabul edilir ve güven düşük kalır |
| Basın (Tier 3) | Bilinen yayın organları | Özgün kaynak ve tarihi yazılmalıdır; güven en fazla orta |
| Reddedilir | Anonim bloglar, SEO ve yapay zekâ üretimi listeler, kullanıcı üretimi sayfalar, kaynaksız sayılar | Kaydedilmez |
| Ücretli araştırma | Nielsen, Euromonitor, IQVIA vb. | İçeriğe erişilmez. Tier 1–3 bir sayfada herkese açık alıntılanan rakam, alıntılayan sayfa kaynak gösterilerek ikinci el işaretiyle kaydedilir |
| kap.org.tr | Tek bir bildirimin bağlantısı ve kısa atıflı iddia | Alıntı saklanmaz, toplu okuma yapılmaz |

Her kayıtta rakam, dönem, ölçü, kapsam, coğrafya, yayım tarihi, yayıncı, kaynak türü, bağlantı ve en fazla 15 sözcüklük alıntı bulunur; sayfa
metni saklanmaz. Dönemi 730 günden eski bulgular "güncel" olmaktan çıkar, ancak listede kalır. Bulgular analiz tablosuna, gözlem deposuna
ve feature'lara girmez.

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
- **segment** (isteğe bağlı): ürün, branş ya da bölge kırılımı. Segment verildiğinde shift-share ayrıştırması ve
  segment örtüşmesi hesaplanır.
- **TOPLAM** satırları (`TOTAL` da kabul edilir; ad `--total-entity` ile değiştirilebilir): kaynak kurumun
  yayımladığı pazar toplamı. Segment varsa her segment için ayrı toplam verilir.
- Türkçe kolon adları da kabul edilir (`şirket`, `marka`, `dönem`, `tarih`, `değer`, `adet`, `kırılım`).
  Noktalı virgülle ayrılmış ve ondalık virgüllü CSV dosyaları otomatik tanınır.

## Hazırlık kontrol listesi
1. **Kapsam**: Kaynağın kapsamadığı oyuncular (üye olmayanlar, ithalatçılar, küçük şirketler) var mı? Varsa TOPLAM
   satırı kaynak toplamıdır ve coverage oranı raporda bu bağlamda yorumlanır.
2. **Süreklilik**: Unvan değişiklikleri ve birleşmeler aynı oyuncu adı altında birleştirilmiş ve
   `assumptions.json` dosyasına `kind: data` olarak yazılmıştır.
3. **Tanım değişiklikleri**: Kaynağın segment ya da branş tanımı dönem içinde değiştiyse tanım değişikliğinin tarihi not edilir.
4. **Birim**: Tutar verisinin nominal olduğu ve adet verisinin fiyat etkisi taşımadığı rapora yansıtılır.
5. **Kaynak atfı**: Her kaynağın adı ve yayın dönemi `--source` ile rapora eklenir; lisans koşulları katalogdaki
   `license` alanında yer alır.
