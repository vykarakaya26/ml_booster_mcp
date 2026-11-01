---
name: sector-analysis
description: Türkiye'de bir sektör (bankacılık, sigorta, telekom, otomotiv, akaryakıt…) için uçtan uca veri ve feature analizi yapar. Ücretsiz veri kaynaklarının haritasını çıkarır, TCMB EVDS'den point-in-time aylık feature paneli üretir, veri kalitesini denetler, baseline forecast backtest'i koşar ve yorumlu, paylaşılabilir bir HTML rapor teslim eder. Kullanıcı bir sektör için "analiz", "rapor", "hangi veriler var", "ML için feature hazırla", "makro görünüm", "sektörün veri haritası" gibi bir şey istediğinde ya da Türkiye makro verisini (kur, enflasyon, faiz, kredi, güven endeksi) bir sektörle ilişkilendirmek istediğinde, skill adını anmasa bile bunu kullan. Also use for "Turkey sector macro data analysis / EVDS feature report".
allowed-tools:
  - Bash(${CLAUDE_SKILL_DIR}/../_shared/bin/mlb *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/render_sector_report.py *)
---

# Sektör analizi

Kullanıcının seçtiği sektör için veriyi baştan sona kendin hazırla ve tek bir HTML rapor teslim et.
Rapordaki her sayı bu akışın ürettiği dosyalardan gelmelidir. Okur raporu karar ve modelleme için
kullanır; kaynağı olmayan ya da tahmine dayanan tek bir rakam bile raporun güvenilirliğini zedeler.

## Araçlar

- **CLI**: `MLB="${CLAUDE_SKILL_DIR}/../_shared/bin/mlb"`. `mlbooster` kuruluysa onu, değilse `uvx` ile
  repodaki sürümü çalıştırır. Bütün komutlar dosya yazdığı için akışta CLI'yi kullan.
- **MCP** (`mlbooster` sunucusu, plugin ile gelir): `show_map`, `audit_panel` gibi araçlar sohbet içindeki
  kısa sorular içindir. Rapor üretirken CLI'yi tercih et.
- **Rapor**: `python3 "${CLAUDE_SKILL_DIR}/scripts/render_sector_report.py"`. Yalnızca standart kütüphaneyle
  çalışır.

## Akış

### 0. Hazırlık
```bash
MLB="${CLAUDE_SKILL_DIR}/../_shared/bin/mlb"
"$MLB" health --source evds3
```
- `skipped … EVDS_API_KEY tanımlı değil` sonucunu görürsen dur. Kullanıcıdan API key'i `~/.mlbooster/.env`
  dosyasına `EVDS_API_KEY=...` satırıyla eklemesini iste. Key, evds3.tcmb.gov.tr → Benim Sayfam → Profilim →
  "API KEY KOPYALA" yolundan alınır. Key'i sohbete yapıştırmasını isteme, dosyayı okuma ve key'i hiçbir
  çıktıya yazma.
- `fail` görürsen hata mesajını kullanıcıya olduğu gibi ilet. Stale seri ya da kod değişikliği gibi bilinen
  sorunlar için `evds-series-discovery` skill'ine yönlendir.

### 1. Sektörü ve çalışma klasörünü belirle
```bash
"$MLB" catalog sectors
SECTOR=otomotiv; OUT="mlbooster-reports/${SECTOR}-$(date +%Y%m%d)"; mkdir -p "$OUT"
```
Sektörler: bankacilik, sigorta, telekom, otomotiv, akaryakit, perakende, turizm, konut ve makro_genel
(günlük, haftalık ve aylık geniş makro havuzu). Kullanıcının sektörü katalogda yoksa en yakın sektörü öner
ve farkı açıkça söyle (ör. "beyaz eşya" için otomotiv + perakende proxy'leri). Katalogda olmayan bir sektör
tanımlama.

### 1b. Girdileri ve varsayımları topla → `$OUT/assumptions.json`
Yorum içeren her çıktı varsayımlara bağlanır; okur bir ifadenin neye dayandığını görebilmelidir. Bunun için
`${CLAUDE_SKILL_DIR}/../_shared/references/assumptions-guide.md` dosyasını oku.
- İstekten çıkarılabilenleri (sektör, hedef, ufuk…) kendin doldur.
- Sonucu değiştirecek eksik girdileri kullanıcıya **tek seferde, en fazla 3–4 kısa soruyla** sor.
  AskUserQuestion aracı varsa onu kullan. Sorulacaklar: amaç ve karar, hedef değişken, ufuk, nominal/reel.
  Sektör varsayımlarını `${CLAUDE_SKILL_DIR}/../_shared/references/sector-assumptions.md` dosyasındaki
  seçeneklerle sun.
- Yanıt gelmezse ya da kullanıcı "sen karar ver" derse sektör varsayılanlarıyla devam et. Bunları
  `kind: sector|default, confirmed: false` olarak kaydet; kullanıcı onaylamış gibi yazma.
- Şablon: `${CLAUDE_SKILL_DIR}/../_shared/assets/assumptions.example.json`. Rapor script'ine
  `--assumptions "$OUT/assumptions.json"` ver; dosya schema doğrulamasından geçmezse script hata verir.

### 2. Kaynak haritası
```bash
"$MLB" map show --sector "$SECTOR" --format json > "$OUT/map.json"
```
`macro_features` içinde durumu `pending_discovery` olanları not et. Bunlar panele girmez ve raporun "Sınırlar"
kısmında adlarıyla geçmelidir. Kullanıcı eksiksiz bir panel istiyorsa önce `evds-series-discovery` skill'iyle
seri kodlarını bağla.

### 3. Panel, kalite ve baseline'lar
```bash
"$MLB" build-panel --sector "$SECTOR" --start 2012-01 --out "$OUT/panel.csv"
"$MLB" audit --sector "$SECTOR" --start 2012-01 --json "$OUT/audit.json"
"$MLB" backtest --sector "$SECTOR" --target <hedef> --horizon <h> --start 2012-01 --json "$OUT/bt-<hedef>.json"
```
- Kullanıcı haftalık ya da günlük panel isterse `build-panel --freq W` ya da `--freq B` ile ayrıca üret.
  Audit ve backtest aylık panelde çalışır.
- Başlangıç 2012-01'dir; EVDS serilerinin çoğu bu tarihten itibaren ortak dönemi kapsar. Kullanıcı daha uzun
  geçmiş isterse değiştir.
- Backtest hedefini ve ufkunu sektöre göre `references/targets.md` tablosundan seç (1–2 hedef yeterli).
  Hedef, `panel.csv` kolonlarından biri olmalıdır.
- `build-panel` çıktısındaki "atlandı" satırlarını kaydet; bunlar rapora girer.

### 4. Yönetici özetini yaz → `$OUT/narrative.md`
`references/narrative-guide.md` ve `${CLAUDE_SKILL_DIR}/../_shared/references/analysis-playbook.md`
dosyalarını oku ve oradaki yapıya uy. Önce `audit.json`, `panel.csv` (son satırlar) ve `bt-*.json`
dosyalarını oku. Her bulguya ay ve değer koy (ör. "TÜFE yıllık %31,5, 2026-09"). Yatırım tavsiyesi verme.
Metni akıcı, nesnel ve kurumsal Türkçeyle yaz. Teknik terimleri İngilizce bırak ve eki kesme işaretiyle
ekle ("feature'lar", "backtest'te", "outlier'lar"); terimlere Türkçe karşılık uydurma. Rakamları Türkçe
biçimde yaz (%31,5; 2,4 puan), yüzde ile yüzde puanı ayır.

### 5. Raporu üret ve teslim et
```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/render_sector_report.py" \
  --map "$OUT/map.json" --panel "$OUT/panel.csv" --audit "$OUT/audit.json" \
  --narrative "$OUT/narrative.md" --backtest "$OUT/bt-<hedef>.json" \
  --assumptions "$OUT/assumptions.json" --out "$OUT/report.html"
```
Kullanıcıya şunları ver: rapor yolu, 3–5 satırlık özet (en önemli bulgu, veri durumu, en iyi baseline model)
ve somut sonraki adımlar (seri kodu bekleyen feature'lar, hedef için daha iyi bir model). `panel.csv`'nin
ML'e hazır çıktı olduğunu da belirt.
Kur, enflasyon ya da faiz görünümü sektör için kritikse (ör. otomotivde kur geçişkenliği), kurumların yıl
sonu forecast'lerini `${CLAUDE_SKILL_DIR}/../_shared/references/external-forecasts.md` dosyasına göre derle
ve rapora `--external` ile ekle. Bunlar varsayımdır; konsensüs kuralı ve güncellik kontrolü otomatik
uygulanır.

## Kalite ölçütleri
- **Hedef değişken panelde mi?** Sektörün asıl hedefi (satış, prim, abone vb.) panelde yoksa, örneğin pazar
  payı kaynağının henüz provider'ı olmadığı için, bunu raporun ilk bulgusu yap ve mevcut veriyle hangi modelin
  kurulamayacağını açıkça yaz.
- Rakamlar yalnızca üretilen dosyalardan gelir. Veri yoksa "veri yok" yaz, tahmin etme.
- Point-in-time hizalamayı bozma. Paneldeki bir ayın satırı, o ayın sonunda bilinen bilgiyi içerir. Bunu
  okura "Eylül TÜFE'si Ekim satırında" gibi bir örnekle açıkla.
- Kaynak atfı rapora otomatik eklenir. Verinin başka bir ürüne konmasını öneriyorsan EVDS'nin "ek ücret
  olarak yansıtılamaz" koşulunu hatırlat.

## Dayanak kuralı (şeffaflık ve izlenebilirlik)
Her ifade dört dayanaktan birine bağlanır ve bu dayanak metinde görünür (ayrıntı:
`${CLAUDE_SKILL_DIR}/../_shared/references/assumptions-guide.md`):
- **Veri**: yayımlanmış gözlem; ay ve kaynakla.
- **Varsayım**: `(A2)` gibi bir kimlikle. Onaylanmamış ya da güncelliğini yitirmiş bir varsayıma dayanan
  sonuç koşullu yazılır.
- **Model**: forecast ya da backtest sonucu; yöntem, örneklem ve belirsizlikle.
- **Yorum**: analistin çıkarımı; güven düzeyiyle ("muhtemel, orta güven"). Nedensellik iddia edilmez.
Bulguyu zayıflatan kanıtı da yaz: dönem seçimi, kısa rejim örneklemi, baz etkisi, nominal yanılsama.
Belirsizliği gizlemek okuru yanıltır; doğru ölçüde vermek kararı güçlendirir.

## Bütünsel akıştaki yeri
Bu skill zincirin başlangıcıdır (`${CLAUDE_SKILL_DIR}/../_shared/references/workflow.md`). Kullanıcı model
kurmak istiyorsa rapordan sonra şu sırayı öner ve aynı çalışma klasörüyle devam et: `data-quality-audit`
(veri sorunları) → `data-prep` (dağılıma göre hazırlık) → `feature-engineering` (sektöre özgü feature'lar ve
seçim) → `forecast-baseline` (`--panel-file` ve `--eval-start` ile katkı ölçümü). Oyuncu düzeyinde bir soru
varsa `market-share` skill'ini aynı sektörle çalıştır.
