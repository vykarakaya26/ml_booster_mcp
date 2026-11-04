---
name: market-share
description: Türkiye'de bir marka, şirket ya da sektör için pazar payı ve rakip analizi yapar. Marka adından sektörü, payı ve rakipleri kaynaklardan çıkarır ya da kullanıcının tablosunu alır; payları, yoğunlaşmayı (HHI, CR3/CR5), oyuncu bazında pay kazanç ve kayıplarını, segment kırılımını (shift-share) ve pazar büyümesinin makro sürücülerini hesaplar. Rakip setini hem veriden (substitution correlation, segment örtüşmesi) hem nitel ölçütlerle belirler; kaynaklı, bütünsel bir HTML rapor teslim eder. Kullanıcı "pazar payı", "rakip analizi", "rekabet analizi", "kim kimden pay alıyor", "rakiplerimiz kim", "pazar yoğunlaşması", "HHI", "pazar konumu" gibi bir şey istediğinde ya da bir şirketi rakipleriyle kıyaslamak istediğinde, skill adını anmasa bile bunu kullan. Makro feature paneli ya da sektör veri haritası için kullanma (sector-analysis); ücretli pazar araştırması verisi (Nielsen, Euromonitor) toplamak için kullanma. Also: "market share / competitor analysis Turkey", "who is taking share", "market concentration HHI".
allowed-tools:
  - Bash(${CLAUDE_SKILL_DIR}/../_shared/bin/mlb *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/render_share_report.py *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/render_brand_report.py *)
---

# Pazar payı ve rakip analizi

Bu skill bir pazarın yapısını ve bir oyuncunun bu yapıdaki konumunu ölçülebilir kanıtla ortaya koyar ve sonucu
tek bir HTML raporda teslim eder. Raporu okuyan kişi fiyat, yatırım ya da strateji kararı verebilir; bu nedenle iki
ilke önceliklidir: **her rakam bir kaynağa dayanır** ve **her yorum dayanağını belirtir**. Pazar payı verisi kaynak
kurumun kapsamına, pazar tanımına ve ölçü birimine çok duyarlıdır. Bu üçü belirsiz kalırsa en doğru hesap bile
yanıltıcı olur.

## Araçlar
- **CLI**: `MLB="${CLAUDE_SKILL_DIR}/../_shared/bin/mlb"`; marka adıyla `"$MLB" brand "<ad>"`, tabloyla ya da katalog
  serisiyle `"$MLB" market-share`.
- **MCP**: `analyze_brand` marka adıyla, `build_brand_features` marka feature paneli için, `analyze_market_share`
  doğrudan satırlarla çalışır; sohbet içindeki hızlı sorular için uygundur. Rapor üretirken dosya yazan CLI tercih edilir.
- **Bulgu araması**: `share-research` agent'ı web'de yayımlanmış pay rakamlarını kaynak politikasına göre arar ve kaydeder
  (`evidence_policy`, `record_share_evidence`, `list_share_evidence`, `reject_share_evidence`). CLI yalnızca kayıtları gösterir:
  `"$MLB" evidence list "<ad>"`, `"$MLB" evidence remove <kimlik> --reason "<gerekçe>"`.
- **Rapor**: `python3 "${CLAUDE_SKILL_DIR}/scripts/render_brand_report.py"` (marka akışı),
  `python3 "${CLAUDE_SKILL_DIR}/scripts/render_share_report.py"` (tablo akışı); ikisi de yalnızca standart kütüphane kullanır.

## Dil
Kullanıcıya görünen bütün metinleri (`narrative.md`, `assumptions.json` açıklamaları, `competitors.json` kanıtları,
sohbetteki özet) `references/report-guide.md` dosyasındaki dil standardına göre yaz: akıcı, nesnel ve kurumsal
Türkçe. Teknik terimler İngilizce kalır ve Türkçeleştirilmez (HHI, CR-k, shift-share, coverage); ek
gerekiyorsa kesme işaretiyle bağlanır ("HHI'ye", "coverage'ı"). Sayılar Türkçe biçimde yazılır
(%26,5; 1.448; +2,5 puan).

## Hangi akış
Kullanıcı bir marka ya da şirket adı veriyorsa **marka akışı** ile başla; tablo istemek gerekmez. Kullanıcının kendi tablosu
varsa, pazar tanımı kaynakların kapsamı dışındaysa ya da marka akışı sonuç vermediyse **tablo akışına** (aşağıdaki 1–6)
geç. Forecast bu skill'in kapsamı dışındadır.

## Marka akışı
```bash
OUT="mlbooster-reports/marka-$(date +%Y%m%d)"; mkdir -p "$OUT"
"$MLB" brand "<marka ya da şirket adı>" --features --json "$OUT/brand.json"
```
Ad, oyuncu düzeyinde veri veren kaynaklarda aranır: ODMD (otomotiv markaları), TBB (bankalar, katılım bankaları hariç),
BDDK (banka grupları), TSB (sigorta şirketleri), EPDK (akaryakıt ve LPG lisans sahipleri), BTK (telekom işletmecileri).
İlk çalıştırma kaynakları okuduğu için birkaç dakika sürebilir. Sonucu `status` alanına göre yorumla:
- **`resolved`**: `headline`, `position`, `series`, `concentration`, `competitors`, `movers`, `segments`, `features` ve
  `sources` alanları hazırdır. `others` listesindeki benzer adları kullanıcıya bildir.
- **`ambiguous`**: `matches` adaylarını kullanıcıya göster ve seçimini sor (AskUserQuestion varsa onu kullan). Tahmin etme.
  Seçimden sonra `"$MLB" brand "<ad>" --choice <aday id> --features --json "$OUT/brand.json"` ile devam et. Grup adlarında
  (Koç, Sabancı) her üye ayrı adaydır; birden fazlası isteniyorsa her biri için ayrı rapor üret.
- **`not_found`**: Hesaplanmış pay yoktur ve **pay tahmini yazılmaz**. Önce `evidence` alanını kontrol et; güncel bulgu yoksa ve
  kullanıcı aksini söylemediyse "Yayımlanmış bulgular" adımını çalıştır. Ardından `message`, `sector_candidates` ve `available`
  alanlarını aktar: sektör makro paneli (`sector-analysis`), Google Trends terimi (`--check-trends` BigQuery sorgusu çalıştırır;
  değer pazar payı değil, arama ilgisidir ve proxy'dir), kullanıcının kendi tablosu. Bu durumda da rapor üretilebilir; rapor
  varsa bulgularla başlar, sınırları belirtir ve hesaplanmış pay içermez.
- **`no_data`**: Oyuncu kaynakta var, ancak dönemde pay hesaplanamıyor; `skipped_series` nedenini verir.

### Arama ilgisi (isteğe bağlı)

`search_interest` bloğu varsayılan olarak `status: not_run` döner; BigQuery sorgusu kullanıcının Google Cloud projesine işlendiği için
kullanıcı istemeden çalıştırma. İstenirse `"$MLB" brand "<ad>" --search-interest` kullan (feature paneli için `--features` ile birlikte).
- `terms` her terim için `found`, `related` (terimin kendisi yok, adı geçen başka terimler var) ya da `not_found` verir. Listede olmamak
  terimin aranmadığı anlamına gelmez; eksik terimi doldurma ve yorumlama.
- `withheld.arama_payi`: arama payı **hesaplanmaz**, çünkü skorlar her terim ve il için ayrı ölçeklenir. Skorları birbirine oranlama, iller
  arasında karşılaştırma. Karşılaştırma için liste günü, liste sırası ve `interest.momentum` (terimin kendi geçmişine göre) kullanılır.
- Rapor bu bölümü hesaplanmış paylardan ve yayımlanmış bulgulardan ayrı gösterir; her zaman "arama ilgisi; pazar payı değildir" diye yaz.
- `data_source` verinin canlı BigQuery'den mi, yerel kopyadan mı geldiğini söyler.

**Yerel kopya (isteğe bağlı).** Türkiye satırlarını bilgisayara indirir; liste geçmişi 30 günle sınırlı kalmaz. Varsayılan olarak kapalıdır ve
**kullanıcıya sormadan açma**. Arama ilgisi çalıştıktan sonra `trends_mirror_status` (MCP) ya da `"$MLB" trends-mirror status --offer` ile durumu
gör; `state` `off` ise teklif metnini aynen aktar ve onay iste, `declined` ise yeniden teklif etme. Onay gelirse `trends_mirror_update` (enable=true)
ya da `"$MLB" trends-mirror enable --yes` çalıştır. İlk kopya onlarca GiB okur ve kullanıcının Google Cloud kotasından düşer. Kapatıp silme
`"$MLB" trends-mirror disable` ya da panelden yapılır.

### Yayımlanmış bulgular
Kaynaklarda oyuncu düzeyinde veri yoksa ya da kullanıcı hesaplanan payı yayımlanmış rakamlarla karşılaştırmak istiyorsa
`share-research` agent'ını çalıştır (Agent aracı; `ml-booster:share-research`). Agent `evidence_policy` kurallarıyla arar, rakamı sayfada
okur ve kaydeder; bütçesi bellidir ve bulgu çıkmazsa bunu söyler. Sonra `"$MLB" brand` komutunu yeniden çalıştır: `evidence` alanı
dolu gelir.
- `evidence.current` güncel bulgular, `evidence.historical` yaşı geçmiş bulgulardır; `evidence.contradictions` aynı ölçü, kapsam ve dönemde
  çelişen rakamları, `evidence.comparison` hesaplanan payla yan yana farkı ve olası nedeni verir.
- Bulgular **derlenmiştir, hesaplanmış pay değildir**. Sohbet özetinde ve `narrative.md` dosyasında ayrı yazılır; her rakam dönem, ölçü, yayıncı
  ve bağlantıyla verilir. Rakamlar ortalanmaz, çelişki olduğu gibi aktarılır.
- Bulgular gözlem deposuna ve feature panellerine girmez. Kullanıcı açıkça isterse `"$MLB" evidence export "<ad>" --out bulgular.csv`
  seyrek, `is_proxy=True` işaretli bir tablo yazar; bu tablo seri olarak okunmaz.
- Agent'ın önerdiği aday yapılandırılmış kaynakları kullanıcıya liste olarak aktar; entegrasyon bu akışın işi değildir.

Rakip seti `competitors` alanında gerekçeleriyle gelir. Nitel değerlendirme gerekiyorsa `competitors.json` hazırla
(4. adım, `entity` adları `brand.json` ile aynı olmalıdır) ve `narrative.md` dosyasını `references/report-guide.md` yapısına göre
yaz (5. adım). Ardından:
```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/render_brand_report.py" --analysis "$OUT/brand.json" \
  --competitors "$OUT/competitors.json" --narrative "$OUT/narrative.md" --out "$OUT/report.html"
```
`--competitors` ve `--narrative` isteğe bağlıdır. Rapor marka özetiyle başlar; sektör, rakipler, pay geçmişi, yoğunlaşma, segmentler,
feature'lar, yayımlanmış pay bulguları ve kaynaklar sırayla gelir. `features` bölümündeki panel ML'e hazırdır: pay, pay değişimi, sıra, lider farkı,
pazara göre büyüme, rakip payı ve sektörün makro feature'ları; her satır o ayın sonuna kadar yayımlanmış veriyi içerir.
`sources` içindeki lisans notunu rapor dışına çıkan her alıntıda koru: ODMD, BTK, TBB, BDDK ve TSB verisi ticari kullanım
için kaynağın yazılı iznini gerektirir.

## Tablo akışı

### 1. Kapsamı belirle → `$OUT/assumptions.json`
```bash
OUT="mlbooster-reports/pazar-payi-$(date +%Y%m%d)"; mkdir -p "$OUT"
```
Analizin sonucunu belirleyen dört girdiyi netleştir. İstekten çıkarılabilenleri kendin doldur; eksikleri **tek
seferde, en fazla dört kısa soruyla** sor (AskUserQuestion varsa onu kullan):
1. **Pazar tanımı**: ürün/hizmet kapsamı ve coğrafya (ör. "Türkiye binek ve hafif ticari araç perakende satışı",
   "hayat dışı sigorta prim üretimi"). Tanım, rakip setini ve payları doğrudan belirler.
2. **Odak oyuncu**: raporun kimin bakış açısından yazıldığı. Odak oyuncu yoksa analiz pazarın geneli için yapılır.
3. **Ölçü**: adet, tutar (TL), abone, aktif büyüklüğü vb.; ayrıca verinin akım (dönemlik satış) mı stok (abone,
   mevduat) mı olduğu.
4. **Amaç ve dönem**: karar bağlamı (yatırım, fiyatlama, bütçe, rakip izleme) ve incelenecek dönem.

Yanıt gelmezse makul varsayımlarla devam et ve bunları `kind: default`, `confirmed: false` olarak kaydet. Şablon
ve kurallar: `${CLAUDE_SKILL_DIR}/../_shared/assets/assumptions.example.json`,
`${CLAUDE_SKILL_DIR}/../_shared/references/assumptions-guide.md`. Pazar tanımını mutlaka kaydet; okur raporda ilk
olarak bunu görmelidir.

### 2. Veriyi edin → `$OUT/share.csv`
Biçim: `entity,date,value[,segment]`. `date` `YYYY-AA`, `YYYY-Qn`, `YYYY` ya da `YYYY-AA-GG` olabilir. Pazar
toplamı biliniyorsa `entity=TOPLAM` satırlarıyla ekle (`TOTAL` da kabul edilir). Bu durumda coverage oranı
hesaplanır ve listelenmeyen oyuncular HHI'de aralık olarak yansıtılır. Ayrıntı: `references/data-sources.md`.
- Kullanıcı dosya verdiyse onu kullan; kolon adlarını ve birimi kontrol et.
- **Bankacılıkta banka grubu payları** için tablo hazırlamaya gerek yoktur: BDDK provider'ı veriyi doğrudan çeker.
  `"$MLB" market-share --source bddk --series BDDK.AKTIF --start 2015-01 --json "$OUT/share.json"` (aktif;
  `BDDK.KREDI`, `BDDK.MEVDUAT`, `BDDK.OZKAYNAK`; sahiplik kırılımı için `*.SAHIPLIK`) kamu, yerli özel ve yabancı
  mevduat bankaları ile katılım, kalkınma ve yatırım bankalarının paylarını sektör toplamına göre hesaplar; 3. adımı
  bu komutla birleştir. Banka bazında pay için TBB verisi gerekir.
- Dosya yoksa sektörün kamuya açık kaynağını `references/data-sources.md` tablosundan seç. Kaynağı okuyabiliyorsan
  (WebFetch vb.) değerleri dönem dönem aktar ve her dönemin kaynağını not et. Okuyamıyorsan kullanıcıdan dosyayı
  indirmesini iste ve hangi tablonun gerektiğini tam olarak söyle.
- **Değer üretme.** Bulunamayan dönem ya da oyuncu boş kalır. Tahmini ya da hatırlanan rakamlar tabloya girmez;
  gerekiyorsa ayrı bir varsayım olarak (`kind: external`) kaydedilir.
- Birleşme, unvan değişikliği ya da marka devri varsa geçmişi pro forma birleştir ve bunu varsayım olarak yaz.

Örnek veri: `assets/sample_market_share.csv` **sentetiktir** (gerçek markalar değildir). Yalnızca akışı denemek
içindir, gerçek bir raporda kullanılmaz.

### 3. Analizi çalıştır → `$OUT/share.json`
```bash
"$MLB" market-share --input "$OUT/share.csv" --focus "<odak>" --unit adet \
  --kind flow --sector <sektör> --json "$OUT/share.json"
```
- `--kind stock` abone, mevduat ve kredi stoku gibi birikimli büyüklükler içindir.
- `--measure share`, kaynak yalnızca yüzde pay yayımlıyorsa kullanılır; bu durumda segment kırılımı yapılmaz.
- `--sector` verilirse pazar büyümesi sektörün EVDS makro paneliyle ilişkilendirilir. Bunun için EVDS API key'i
  gerekir; yoksa bu adımı atla ve raporda belirt.
- Komutun yazdırdığı uyarıları (coverage, eksik dönem) kaydet; bunlar rapora girer.

### 4. Rakip setini belirle → `$OUT/competitors.json`
`share.json` içindeki `competitors.candidates`, veriye dayalı adayları ve gerekçelerini verir. Bunu nitel
değerlendirmeyle tamamla: ürün ve fiyat bandı örtüşmesi, müşteri segmenti, kanal, coğrafya, iş modeli, ithalat ve
yeni girenler. Çerçeve ve dosya schema'sı: `references/competitor-framework.md`, örnek:
`assets/competitors.example.json`. Her nitel yargıya kaynak (şirket açıklaması, KAP, sektör raporu) ve güven
düzeyi ekle. Veride görünmeyen ama rekabet baskısı yaratan oyuncuları (`tier: potential`) atlama.

### 5. Bütünsel değerlendirmeyi yaz → `$OUT/narrative.md`
`references/report-guide.md` dosyasındaki yapıya ve dil standardına uy; yöntem ve yorum kuralları için
`references/methodology.md` dosyasına başvur. Önce `share.json` dosyasını oku. Her bulguda dönem ve değer ver
(ör. "Eylül 2026 itibarıyla son 12 ay payı %26,5; bir yıl öncesine göre +2,5 puan"). Bulguları birbirine bağla:
pazar büyümesi, yoğunlaşma, odak oyuncunun konumu, payın hangi rakipten ve hangi segmentten geldiği, makro
koşullar ve riskler tek ve tutarlı bir tablo oluşturmalıdır. Yatırım tavsiyesi verme.

### 6. Raporu üret ve teslim et
```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/render_share_report.py" --analysis "$OUT/share.json" \
  --competitors "$OUT/competitors.json" --narrative "$OUT/narrative.md" \
  --assumptions "$OUT/assumptions.json" --source "<kaynak kurum ve yayın>" --out "$OUT/report.html"
```
Kullanıcıya rapor yolunu, üç ila beş satırlık özeti (konum, trend, ana rakip, yoğunlaşma, veri coverage'ı) ve
somut sonraki adımları ver. Örneğin eksik dönemleri tamamlamak ya da segment verisi eklemek sonraki adımlar arasında
olabilir.

## Kalite ölçütleri
- **Önce tanım ve kapsam.** Pazar tanımı, ölçü birimi ve coverage oranı rapor özetinde açıkça yer alır. Coverage
  %80'in altındaysa ya da pazar toplamı bilinmiyorsa paylar "listelenen oyuncular içinde" diye nitelenir.
- **Rakamlar yalnızca `share.json` dosyasından gelir.** Raporda hesaplanmamış bir oran ya da değişim yazılmaz. Tek istisna,
  ayrı bölümde bağlantısıyla gösterilen yayımlanmış pay bulgularıdır.
- **Pay değişimi ile trend ayrı tutulur.** Tek dönemlik değişim gürültü olabilir. "Pay kazanıyor" ifadesi
  yalnızca trend hem istatistiksel hem ekonomik olarak anlamlıysa kullanılır (`trend` alanı).
- **İkame bir kanıttır, ispat değildir.** Ters yönlü pay korelasyonu "doğrudan rekabetle tutarlı" diye yazılır.
  "Pay X'ten alındı" gibi kesin ifadeler ancak shift-share ayrıştırması ve nitel kanıt da destekliyorsa kullanılır.
- **Nominal ve reel ayrı tutulur.** Tutar verisinde pazar büyümesi enflasyonu da içerir; adet verisiyle
  karşılaştırırken bu belirtilir.

## Dayanak kuralı
Her ifade dört dayanaktan birine bağlanır ve bu dayanak metinde görünür: **veri** (dönem ve kaynakla),
**varsayım** (`(A2)` gibi kimlikle), **model** (yöntem ve örneklemle; ör. substitution correlation, n=60) ya da
**yorum** (analistin çıkarımı, güven düzeyiyle). Bulguyu zayıflatan kanıt da yazılır: kısa örneklem, kapsam
değişikliği, birleşmeler, kampanya ya da vergi değişikliği nedeniyle öne çekilmiş talep.

## Bütünsel akıştaki yeri
`--sector` ile pazar büyümesi sektörün makro paneliyle ilişkilendirilir. Pazar toplamı serisi `feature-engineering`
ile aynı eğitim penceresinde değerlendirilebilir; pay forecast'ı kapsam dışıdır. Akış: `${CLAUDE_SKILL_DIR}/../_shared/references/workflow.md`.
