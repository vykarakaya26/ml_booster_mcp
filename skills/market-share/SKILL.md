---
name: market-share
description: Türkiye'de bir sektör ya da şirket için pazar payı ve rekabet analizi yapar. Pazar payı verisini derler ya da kullanıcının tablosunu alır; payları, yoğunlaşmayı (HHI, CR3/CR5), oyuncu bazında kazanç ve kayıpları, segment kırılımını ve pazar büyümesinin makro sürücülerini hesaplar; rakip setini hem veriden (ikame korelasyonu, segment örtüşmesi) hem nitel ölçütlerle belirler ve bütünsel, kaynaklı bir HTML rapor teslim eder. Kullanıcı "pazar payı", "rakip analizi", "rekabet analizi", "kim kimden pay alıyor", "rakiplerimiz kim", "pazar yoğunlaşması", "HHI", "pazar konumu" gibi bir şey istediğinde ya da bir şirketi rakipleriyle kıyaslamak istediğinde, skill adını anmasa bile bunu kullan. Also use for "market share / competitor analysis Turkey".
allowed-tools:
  - Bash(${CLAUDE_SKILL_DIR}/../_shared/bin/mlb *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/render_share_report.py *)
---

# Pazar payı ve rekabet analizi

Bu skill, bir pazarın yapısını ve bir oyuncunun bu yapı içindeki konumunu ölçülebilir kanıtla ortaya
koyar ve sonucu tek bir HTML raporda teslim eder. Raporu okuyan kişi fiyat, yatırım ya da strateji kararı
verebileceği için iki ilke önceliklidir: **her rakam bir kaynağa dayanır** ve **her yorum dayanağını
belirtir**. Pazar payı verisi kaynak kurumun kapsamına, pazar tanımına ve ölçü birimine çok duyarlıdır;
bu üçü belirsiz bırakılırsa en doğru hesap bile yanıltıcı olur.

## Araçlar
- **CLI**: `MLB="${CLAUDE_SKILL_DIR}/../_shared/bin/mlb"`; analiz komutu `"$MLB" market-share`.
- **MCP**: `analyze_market_share` aracı satırları doğrudan alır; sohbet içi hızlı sorular için uygundur.
  Rapor üretirken dosya yazan CLI tercih edilir.
- **Rapor**: `python3 "${CLAUDE_SKILL_DIR}/scripts/render_share_report.py"` (yalnızca standart kütüphane).

## Akış

### 1. Kapsamı belirle → `$OUT/assumptions.json`
```bash
OUT="mlbooster-reports/pazar-payi-$(date +%Y%m%d)"; mkdir -p "$OUT"
```
Analizin sonucunu belirleyen dört girdiyi netleştir. İstekten çıkarılabilenleri kendin doldur; eksik
olanları **tek seferde, en fazla dört kısa soruyla** sor (AskUserQuestion varsa onu kullan):
1. **Pazar tanımı**: ürün/hizmet kapsamı ve coğrafya (ör. "Türkiye binek ve hafif ticari araç perakende
   satışı", "hayat dışı sigorta prim üretimi"). Tanım, rakip setini ve payları doğrudan belirler.
2. **Odak oyuncu**: raporun kimin bakış açısıyla yazıldığı. Yoksa analiz pazar geneli için yapılır.
3. **Ölçü**: adet, tutar (TL), abone, aktif büyüklüğü vb. ve akım (dönemlik satış) mı stok (abone, mevduat)
   mı olduğu.
4. **Amaç ve dönem**: karar bağlamı (yatırım, fiyatlama, bütçe, rakip izleme) ve incelenecek dönem.

Yanıt gelmezse makul varsayımlarla devam et; bunları `kind: default`, `confirmed: false` olarak kaydet.
Şablon ve kurallar: `${CLAUDE_SKILL_DIR}/../_shared/assets/assumptions.example.json`,
`${CLAUDE_SKILL_DIR}/../_shared/references/assumptions-guide.md`. Pazar tanımını mutlaka
kaydet; raporda okur ilk olarak bunu görmelidir.

### 2. Veriyi edin → `$OUT/share.csv`
Biçim: `entity,date,value[,segment]`. `date` `YYYY-AA`, `YYYY-Qn`, `YYYY` ya da `YYYY-AA-GG` olabilir.
Pazar toplamı biliniyorsa `entity=TOPLAM` satırlarıyla ekle; bu durumda kapsam oranı hesaplanır ve
listelenmeyen oyuncular HHI'de aralık olarak yansıtılır. Ayrıntı: `references/data-sources.md`.
- Kullanıcı dosya verdiyse onu kullan; kolon adlarını ve birimi kontrol et.
- Vermediyse sektörün kamuya açık kaynağını `references/data-sources.md` tablosundan seç. Kaynağı
  okuyabiliyorsan (WebFetch vb.) değerleri dönem dönem aktar ve her dönemin kaynağını not et;
  okuyamıyorsan kullanıcıdan dosyayı indirmesini iste ve hangi tablonun gerektiğini tam olarak söyle.
- **Değer üretme.** Bulunamayan dönem ya da oyuncu boş kalır. Tahmini ya da hatırlanan rakamlar tabloya
  girmez; gerekiyorsa ayrı bir varsayım olarak (`kind: external`) kaydedilir.
- Birleşme, unvan değişikliği ve marka devri varsa geçmişi pro forma birleştir ve bunu varsayım olarak yaz.

Örnek veri: `assets/ornek_pazar_payi.csv` **sentetiktir** (gerçek markalar değildir); yalnızca akışı
denemek içindir ve gerçek bir raporda kullanılmaz.

### 3. Analizi çalıştır → `$OUT/share.json`
```bash
"$MLB" market-share --input "$OUT/share.csv" --focus "<odak>" --unit adet \
  --kind flow --sector <sektör> --json "$OUT/share.json"
```
- `--kind stock` abone, mevduat ve kredi stoku gibi birikimli büyüklükler içindir.
- `--measure share`, kaynak yalnızca yüzde pay yayımlıyorsa kullanılır (segment kırılımı bu durumda yapılmaz).
- `--sector` verilirse pazar büyümesi, sektörün EVDS makro paneliyle ilişkilendirilir (EVDS anahtarı
  gerekir; yoksa bu adımı atla ve raporda belirt).
- Komutun yazdırdığı uyarıları (kapsam, eksik dönem) kaydet; rapora girer.

### 4. Rakip setini belirle → `$OUT/competitors.json`
`share.json` içindeki `competitors.candidates` veriye dayalı adayları ve gerekçelerini verir. Bunu nitel
değerlendirmeyle tamamla: ürün ve fiyat bandı örtüşmesi, müşteri segmenti, kanal, coğrafya, iş modeli,
ithalat ve yeni girenler. Çerçeve ve dosya şeması: `references/competitor-framework.md`, örnek:
`assets/competitors.example.json`. Her nitel yargıya kaynak (şirket açıklaması, KAP, sektör raporu) ve
güven düzeyi ekle. Veride görünmeyen ama rekabet baskısı yaratan oyuncuları (`tier: potansiyel`) atlama.

### 5. Bütünsel değerlendirmeyi yaz → `$OUT/narrative.md`
`references/report-guide.md` dosyasındaki yapıya ve dil standardına uy; yöntem ve yorum kuralları için
`references/methodology.md` dosyasına başvur. Önce `share.json` dosyasını oku. Her bulguda dönem ve değer
ver (ör. "Eylül 2026 itibarıyla son 12 ay payı %26,5; bir yıl önceye göre +2,5 puan"). Bulguları birbirine
bağla: pazar büyümesi, yoğunlaşma, odak oyuncunun konumu, payın hangi rakipten ve hangi segmentten geldiği,
makro koşullar ve riskler tek bir tutarlı tablo oluşturmalıdır. Yatırım tavsiyesi verme.

### 6. Raporu üret ve teslim et
```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/render_share_report.py" --analysis "$OUT/share.json" \
  --competitors "$OUT/competitors.json" --narrative "$OUT/narrative.md" \
  --assumptions "$OUT/assumptions.json" --source "<kaynak kurum ve yayın>" --out "$OUT/report.html"
```
Kullanıcıya rapor yolunu, üç ila beş satırlık özeti (konum, eğilim, ana rakip, yoğunlaşma, veri kapsamı)
ve somut sonraki adımları ver. Sonraki adımlara, örneğin eksik dönemleri tamamlamak, segment verisi eklemek
ya da `forecast-baseline` ile pay tahmini yapmak girebilir.

## Kalite ölçütleri
- **Tanım ve kapsam önce gelir.** Pazar tanımı, ölçü birimi ve kapsam oranı raporun özetinde açıkça yer alır.
  Kapsam %80'in altındaysa ya da pazar toplamı bilinmiyorsa paylar "listelenen oyuncular içinde" diye nitelenir.
- **Rakamlar yalnızca `share.json` dosyasından gelir.** Raporda hesaplanmayan bir oran ya da değişim yazılmaz.
- **Pay değişimi ile eğilim ayrılır.** Tek dönemlik değişim gürültü olabilir; "pay kazanıyor" ifadesi yalnızca
  eğilim istatistiksel ve ekonomik olarak anlamlıysa kullanılır (`trend` alanı).
- **İkame bir kanıttır, ispat değildir.** Ters yönlü pay korelasyonu "doğrudan rekabet ile tutarlı" diye
  yazılır; "pay X'ten alındı" gibi kesin ifadeler ancak segment ayrıştırması ve nitel kanıt da destekliyorsa kullanılır.
- **Nominal ve reel ayrılır.** Tutar verisinde pazar büyümesi enflasyon içerir; adetle karşılaştırılırken bu belirtilir.

## Dayanak kuralı
Her ifade dört dayanaktan birine bağlanır ve bu dayanak metinde görünür: **veri** (dönem ve kaynakla),
**varsayım** (`(A2)` gibi kimlikle), **model** (yöntem ve örneklemle; ör. ikame korelasyonu, n=60) ya da
**yorum** (analistin çıkarımı, güven düzeyiyle). Bulguyu zayıflatan kanıt da yazılır: kısa örneklem, kapsam
değişikliği, birleşmeler, kampanya ve vergi değişikliği kaynaklı öne çekilmiş talep.

## Bütünsel akıştaki yeri
`--sector` ile pazar büyümesi sektörün makro paneliyle ilişkilendirilir. Pay ya da pazar büyüklüğü tahmin edilecekse
pazar toplamı serisi `forecast-baseline` ve `feature-engineering` ile aynı eğitim penceresinde değerlendirilir. Akış:
`${CLAUDE_SKILL_DIR}/../_shared/references/workflow.md`.
