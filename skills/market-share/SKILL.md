---
name: market-share
description: Türkiye'de bir sektör ya da şirket için pazar payı ve rakip analizi yapar. Pazar payı verisini derler ya da kullanıcının tablosunu alır; payları, yoğunlaşmayı (HHI, CR3/CR5), oyuncu bazında pay kazanç ve kayıplarını, segment kırılımını (shift-share) ve pazar büyümesinin makro sürücülerini hesaplar. Rakip setini hem veriden (substitution correlation, segment örtüşmesi) hem nitel ölçütlerle belirler; kaynaklı, bütünsel bir HTML rapor teslim eder. Kullanıcı "pazar payı", "rakip analizi", "rekabet analizi", "kim kimden pay alıyor", "rakiplerimiz kim", "pazar yoğunlaşması", "HHI", "pazar konumu" gibi bir şey istediğinde ya da bir şirketi rakipleriyle kıyaslamak istediğinde, skill adını anmasa bile bunu kullan. Also: "market share / competitor analysis Turkey", "who is taking share", "market concentration HHI".
allowed-tools:
  - Bash(${CLAUDE_SKILL_DIR}/../_shared/bin/mlb *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/render_share_report.py *)
---

# Pazar payı ve rakip analizi

Bu skill bir pazarın yapısını ve bir oyuncunun bu yapıdaki konumunu ölçülebilir kanıtla ortaya koyar ve sonucu
tek bir HTML raporda teslim eder. Raporu okuyan kişi fiyat, yatırım ya da strateji kararı verebilir; bu nedenle iki
ilke önceliklidir: **her rakam bir kaynağa dayanır** ve **her yorum dayanağını belirtir**. Pazar payı verisi kaynak
kurumun kapsamına, pazar tanımına ve ölçü birimine çok duyarlıdır. Bu üçü belirsiz kalırsa en doğru hesap bile
yanıltıcı olur.

## Araçlar
- **CLI**: `MLB="${CLAUDE_SKILL_DIR}/../_shared/bin/mlb"`; analiz komutu `"$MLB" market-share`.
- **MCP**: `analyze_market_share` aracı satırları doğrudan alır; sohbet içindeki hızlı sorular için uygundur.
  Rapor üretirken dosya yazan CLI tercih edilir.
- **Rapor**: `python3 "${CLAUDE_SKILL_DIR}/scripts/render_share_report.py"` (yalnızca standart kütüphane).

## Dil
Kullanıcıya görünen bütün metinleri (`narrative.md`, `assumptions.json` açıklamaları, `competitors.json` kanıtları,
sohbetteki özet) `references/report-guide.md` dosyasındaki dil standardına göre yaz: akıcı, nesnel ve kurumsal
Türkçe. Teknik terimler İngilizce kalır ve Türkçeleştirilmez (HHI, CR-k, shift-share, coverage, baseline,
backtest); ek gerekiyorsa kesme işaretiyle bağlanır ("HHI'ye", "backtest'te"). Sayılar Türkçe biçimde yazılır
(%26,5; 1.448; +2,5 puan).

## Akış

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
Kullanıcıya rapor yolunu, üç ila beş satırlık özeti (konum, eğilim, ana rakip, yoğunlaşma, veri coverage'ı) ve
somut sonraki adımları ver. Örneğin eksik dönemleri tamamlamak, segment verisi eklemek ya da `forecast-baseline`
ile pay forecast'ı yapmak sonraki adımlar arasında olabilir.

## Kalite ölçütleri
- **Önce tanım ve kapsam.** Pazar tanımı, ölçü birimi ve coverage oranı rapor özetinde açıkça yer alır. Coverage
  %80'in altındaysa ya da pazar toplamı bilinmiyorsa paylar "listelenen oyuncular içinde" diye nitelenir.
- **Rakamlar yalnızca `share.json` dosyasından gelir.** Raporda hesaplanmamış bir oran ya da değişim yazılmaz.
- **Pay değişimi ile eğilim ayrı tutulur.** Tek dönemlik değişim gürültü olabilir. "Pay kazanıyor" ifadesi
  yalnızca eğilim hem istatistiksel hem ekonomik olarak anlamlıysa kullanılır (`trend` alanı).
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
`--sector` ile pazar büyümesi sektörün makro paneliyle ilişkilendirilir. Pay ya da pazar büyüklüğü forecast
edilecekse pazar toplamı serisi `forecast-baseline` ve `feature-engineering` ile aynı eğitim penceresinde
değerlendirilir. Akış: `${CLAUDE_SKILL_DIR}/../_shared/references/workflow.md`.
