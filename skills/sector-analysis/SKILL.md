---
name: sector-analysis
description: Türkiye'de bir sektör (bankacılık, sigorta, telekom, otomotiv, akaryakıt…) için uçtan uca veri ve feature analizi yapar. Ücretsiz veri kaynağı haritasını çıkarır, TCMB EVDS'den nokta-zamanlı aylık feature paneli üretir, veri kalitesini denetler, referans tahmin backtest'i koşar ve yorumlu, paylaşılabilir bir HTML rapor teslim eder. Kullanıcı bir sektör için "analiz", "rapor", "hangi veriler var", "ML için feature hazırla", "makro görünüm", "sektörün veri haritası" gibi bir şey istediğinde ya da Türkiye makro verisini (kur, enflasyon, faiz, kredi, güven endeksi) bir sektörle ilişkilendirmek istediğinde, skill adını anmasa bile bunu kullan. Also use for "Turkey sector macro data analysis / EVDS feature report".
allowed-tools:
  - Bash(${CLAUDE_SKILL_DIR}/../_shared/bin/mlb *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/render_sector_report.py *)
---

# Sektör analizi

Kullanıcının seçtiği sektör için veriyi baştan sona otonom hazırla ve tek bir HTML rapor teslim et.
Rapordaki her sayı, bu akışın ürettiği dosyalardan gelmelidir. Okuyucu bu raporla karar veya
modelleme yapacağı için uydurma ya da tahmini bir rakam güveni bozar.

## Araçlar

- **CLI**: `MLB="${CLAUDE_SKILL_DIR}/../_shared/bin/mlb"`. Kuruluysa `mlbooster`'ı, değilse `uvx` ile
  repodan çalıştırır. Bütün komutlar dosya yazdığı için iş akışında CLI kullanılır.
- **MCP** (`mlbooster` sunucusu, plugin ile gelir): `show_map`, `audit_panel` gibi araçlar hızlı
  sohbet içi sorular içindir. Rapor üretirken CLI'yi tercih et.
- **Rapor**: `python3 "${CLAUDE_SKILL_DIR}/scripts/render_sector_report.py"`. Yalnızca standart
  kütüphaneyle çalışır.

## Akış

### 0. Hazırlık
```bash
MLB="${CLAUDE_SKILL_DIR}/../_shared/bin/mlb"
"$MLB" health --source evds3
```
- `skipped … EVDS_API_KEY tanımlı değil` görürsen dur. Kullanıcıya anahtarı `~/.mlbooster/.env`
  dosyasına `EVDS_API_KEY=...` satırıyla koymasını söyle. Anahtar evds3.tcmb.gov.tr → Benim Sayfam →
  Profilim → "API KEY KOPYALA" yolundan alınır. Anahtarı sohbete yapıştırmasını isteme, dosyayı okuma
  ve anahtarı hiçbir çıktıya yazma.
- `fail` görürsen hata mesajını kullanıcıya aynen ilet. Durmuş seri ve kod değişikliği gibi bilinen
  sorunlar için `evds-series-discovery` skill'ine yönlendir.

### 1. Sektörü ve çalışma klasörünü belirle
```bash
"$MLB" catalog sectors
SECTOR=otomotiv; OUT="mlbooster-reports/${SECTOR}-$(date +%Y%m%d)"; mkdir -p "$OUT"
```
Sektörler: bankacilik, sigorta, telekom, otomotiv, akaryakit, perakende, turizm, konut ve
makro_genel (günlük, haftalık ve aylık geniş makro havuzu). Kullanıcının sektörü katalogda yoksa
en yakın sektörü öner ve farkı açıkça söyle (ör. "beyaz eşya"
için otomotiv + perakende proxy'leri). Yoksa bir sektör uydurma.

### 1b. Girdileri ve varsayımları al → `$OUT/assumptions.json`
Yorum içeren her çıktı, okurun dayanağı ayırt edebileceği biçimde varsayımlara bağlanır. Bunun için
`${CLAUDE_SKILL_DIR}/../_shared/references/assumptions-guide.md` dosyasını oku.
- İstekten çıkarılabilenleri (sektör, hedef, ufuk…) kendin doldur.
- Sonucu değiştirecek eksik girdileri kullanıcıya **tek seferde, en fazla 3–4 kısa soruyla** sor.
  AskUserQuestion aracı varsa onu kullan. Sorulacaklar: amaç ve karar, hedef değişken, ufuk,
  nominal/reel. Sektör varsayımlarını `${CLAUDE_SKILL_DIR}/../_shared/references/sector-assumptions.md`
  dosyasındaki seçeneklerle sun.
- Yanıt gelmezse ya da kullanıcı "sen karar ver" derse sektör varsayılanlarıyla devam et. Bunları
  `kind: sector|default, confirmed: false` olarak kaydet ve kullanıcı adına onaylanmış gibi yazma.
- Şablon: `${CLAUDE_SKILL_DIR}/../_shared/assets/assumptions.example.json`. Rapor script'ine
  `--assumptions "$OUT/assumptions.json"` ver; dosya şema doğrulamasından geçmezse script hata verir.

### 2. Kaynak haritası
```bash
"$MLB" map show --sector "$SECTOR" --format json > "$OUT/map.json"
```
`macro_features` içinde `kesif_bekliyor` olanları not et. Bunlar panele girmez; raporun "Sınırlar"
kısmında adlarıyla geçmelidir. Kullanıcı eksiksiz panel istiyorsa önce `evds-series-discovery`
skill'iyle kodları bağla.

### 3. Panel, kalite ve tahmin referansları
```bash
"$MLB" build-panel --sector "$SECTOR" --start 2012-01 --out "$OUT/panel.csv"
"$MLB" audit --sector "$SECTOR" --start 2012-01 --json "$OUT/audit.json"
"$MLB" backtest --sector "$SECTOR" --target <hedef> --horizon <h> --start 2012-01 --json "$OUT/bt-<hedef>.json"
```
- Kullanıcı haftalık ya da günlük panel isterse `build-panel --freq W` ya da `--freq B` ile ayrıca
  üret. Denetim ve backtest aylık panelde çalışır.
- Başlangıç 2012-01'dir, çünkü EVDS serilerinin çoğu bu tarihte ortaklaşır. Kullanıcı daha uzun
  geçmiş isterse değiştir.
- Backtest hedefini ve ufkunu `references/targets.md` tablosundan sektöre göre seç (1–2 hedef yeterli).
  Hedef, `panel.csv` kolonlarından biri olmalıdır.
- `build-panel` çıktısındaki "atlandı" satırlarını kaydet; rapora girer.

### 4. Yönetici özetini yaz → `$OUT/narrative.md`
`references/narrative-guide.md` ve `${CLAUDE_SKILL_DIR}/../_shared/references/analysis-playbook.md`
dosyalarını oku ve oradaki yapıya uy. Önce `audit.json`, `panel.csv`
(son satırlar) ve `bt-*.json` dosyalarını oku. Her bulguya ay ve değer koy (ör. "TÜFE yıllık %31,5,
2026-09"). Yatırım tavsiyesi verme.

### 5. Raporu üret ve teslim et
```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/render_sector_report.py" \
  --map "$OUT/map.json" --panel "$OUT/panel.csv" --audit "$OUT/audit.json" \
  --narrative "$OUT/narrative.md" --backtest "$OUT/bt-<hedef>.json" \
  --assumptions "$OUT/assumptions.json" --out "$OUT/report.html"
```
Kullanıcıya şunları ver: rapor yolu, 3–5 satırlık özet (en önemli bulgu, veri durumu, en iyi
referans model) ve somut sonraki adımlar (keşif bekleyen feature'lar, hedef için daha iyi model).
`panel.csv` ML'e hazır çıktıdır; bunu da belirt.
Kur, enflasyon ya da faiz görünümü sektör için kritikse (ör. otomotivde kur geçişkenliği),
`${CLAUDE_SKILL_DIR}/../_shared/references/external-forecasts.md` dosyasına göre kurumların yıl sonu
tahminlerini derle ve rapora `--external` ile ekle. Bunlar varsayımdır; konsensüs kuralı ve bayatlık
otomatik uygulanır.

## Kalite çıtası
- **Hedef değişken var mı?** Sektörün asıl hedefi (satış, prim, abone vb.) panelde yoksa, örneğin
  pazar payı kaynağının sağlayıcısı olmadığı için, bunu raporun ilk bulgusu yap ve bugün hangi
  modelin kurulamayacağını açıkça yaz.
- Rakamlar yalnızca üretilen dosyalardan gelir. Eksik veri varsa "veri yok" yaz, tahmin etme.
- Nokta-zamanlı hizalamayı bozma. Panelde bir ayın satırı, o ay sonunda bilinen bilgidir. Bunu
  okura "Eylül TÜFE'si Ekim satırında" gibi bir örnekle açıkla.
- Kaynak atfı raporda otomatik bulunur. Veriyi başka bir ürüne koymayı öneriyorsan EVDS'nin "ek
  ücret olarak yansıtılamaz" koşulunu hatırlat.

## Dayanak kuralı (yanıltıcı değil, güçlendirici)
Her ifade dört dayanaktan birine bağlanır ve bu dayanak metinde görünür (ayrıntı:
`${CLAUDE_SKILL_DIR}/../_shared/references/assumptions-guide.md`):
- **Veri**: yayımlanmış gözlem; ay ve kaynakla.
- **Varsayım**: `(A2)` gibi kimlikle. Onaysız ya da bayat varsayıma dayanan sonuç koşullu yazılır.
- **Model**: tahmin veya backtest sonucu; yöntem, örneklem ve belirsizlikle.
- **Yorum**: analistin çıkarımı; güven düzeyiyle ("muhtemel, orta güven"). Nedensellik iddia edilmez.
Bulguyu zayıflatan kanıtı da yaz: dönem seçimi, kısa rejim örneklemi, baz etkisi, nominal yanılsama.
Belirsizliği gizlemek okuru yanıltır; doğru ölçüde vermek ise kararı güçlendirir.
