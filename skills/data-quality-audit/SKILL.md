---
name: data-quality-audit
description: Türkiye makro/sektör feature panelinin veri kalitesini denetler ve önem derecesine göre sıralanmış bulgular içeren HTML rapor üretir. Durmuş/bayat seriler (ör. arşive taşınmış EVDS kodları), eksik aylar, robust z ile aykırı değerler, yapısal kırılmalar (2018 ve 2021 kur şokları ve 2023 sıkılaştırması ile eşleştirilmiş) ve aşırı korelasyonlu feature çiftleri incelenir. Kullanıcı panelin, EVDS serilerinin veya ML feature'larının "temiz mi", "güvenilir mi", "eksik/bozuk var mı", "neden model kötü" diye sorduğunda, modellemeden önce veri kontrolü istediğinde ya da bir seri garip göründüğünde kullan. Also: "data quality check / audit Turkish macro features".
allowed-tools:
  - Bash(${CLAUDE_SKILL_DIR}/../_shared/bin/mlb *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/render_audit_report.py *)
---

# Veri kalitesi denetimi

Amaç, modelden önce insanın bakması gereken noktaları çıkarmaktır. Bulgular kesin hüküm değil,
inceleme listesidir. Her bulgu için "ne yapılmalı" önerisi de ver; kullanıcı rapordan doğrudan
aksiyona geçebilmelidir.

## Akış

```bash
MLB="${CLAUDE_SKILL_DIR}/../_shared/bin/mlb"
"$MLB" health --source evds3          # anahtar/uç sorunu varsa önce onu çöz
SECTOR=bankacilik; OUT="mlbooster-reports/${SECTOR}-audit-$(date +%Y%m%d)"; mkdir -p "$OUT"
"$MLB" build-panel --sector "$SECTOR" --start 2012-01 --out "$OUT/panel.csv"
"$MLB" audit --sector "$SECTOR" --start 2012-01 --json "$OUT/audit.json"
# notes.md'yi aşağıdaki "Bulguları yorumla" adımında yaz, sonra:
python3 "${CLAUDE_SKILL_DIR}/scripts/render_audit_report.py" \
  --audit "$OUT/audit.json" --panel "$OUT/panel.csv" --sector "$SECTOR" --notes "$OUT/notes.md" \
  --assumptions "$OUT/assumptions.json" --out "$OUT/audit.html"
```

- `health` "skipped" dönerse kullanıcıdan anahtarı `~/.mlbooster/.env` dosyasına koymasını iste.
  Anahtarı sohbete yazmasını isteme.
- Kullanıcı yalnızca tek bir seriyi soruyorsa (ör. "TP.KTF11 neden düz?") sektör paneli yerine
  `"$MLB" fetch --series <KOD> --start 2015-01` ile bak ve aynı kontrolleri sözlü uygula.

### 1. Girdileri ve varsayımları al → `$OUT/assumptions.json`
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

## Bulguları yorumla → `$OUT/notes.md`
`audit.json` dosyasını oku. `references/checks.md` her kontrolün anlamını, eşiğini ve tipik
düzeltmesini anlatır. `diagnostics` alanındaki durağanlık, kuyruk ve oynaklık teşhislerini ve ön işleme
önerilerini `${CLAUDE_SKILL_DIR}/../_shared/references/analysis-playbook.md` ile yorumla.
Panelde sektörün hedef değişkeni yoksa bunu en üst sıradaki bulgu yap. `notes.md` dosyasına kısa bir özet ve öncelik sıralı aksiyon listesi yaz.
Bunlar raporun "Yorum ve öneriler" bölümünde görünür.
Denetimin eşik altında bıraktığı ama modellemeyi etkileyen noktaları da ekle: 0,95 eşiğinin altında
kalan yüksek korelasyonlar (ör. USD ile EUR kuru ≈ 0,9) ve TÜFE'nin yayın gecikmesiyle bir ay sonraki
satırda durması. Gecikme kasıtlıdır; hedefle hizalarken yeniden kaydırılmamalıdır.
Öncelik sırası:
1. **high / stale**: seri durmuş. Kod büyük olasılıkla arşive taşınmıştır (TP.FG.J0 → TP.TUKFIY2025.GENEL
   gibi). `evds-series-discovery` skill'iyle güncel kodu bul.
2. **high / empty, constant**: feature panelde işe yaramıyor. Kodu ya da dönüşümü kontrol et.
3. **medium / structural_break (eşleşmeyen)**: veri hatası mı, gerçek rejim değişimi mi? Seriye
   bakarak ayırt et ve bulduğunu kullanıcıya söyle.
4. **medium / outliers**: şok ayları (2018-08, 2021-12, 2023-06) genelde gerçek veridir. Silme;
   winsorize et ya da dummy ekle.
5. **medium / collinearity**: iki feature'dan birini çıkar ya da farkını kullan.
6. **info**: bilinen şoklarla eşleşen kırılmalar. Rejim dummy'si önerisiyle birlikte not düş.

## Teslim
Rapor yolu, bulgu sayıları (yüksek/orta/bilgi) ve önem sırasına göre en fazla 5 aksiyon maddesini
ilet. Panelde atlanan (`skipped_features`) feature'lar varsa ayrıca belirt.

## Dayanak kuralı (yanıltıcı değil, güçlendirici)
Her ifade dört dayanaktan birine bağlanır ve bu dayanak metinde görünür (ayrıntı:
`${CLAUDE_SKILL_DIR}/../_shared/references/assumptions-guide.md`):
- **Veri**: yayımlanmış gözlem; ay ve kaynakla.
- **Varsayım**: `(A2)` gibi kimlikle. Onaysız ya da bayat varsayıma dayanan sonuç koşullu yazılır.
- **Model**: tahmin veya backtest sonucu; yöntem, örneklem ve belirsizlikle.
- **Yorum**: analistin çıkarımı; güven düzeyiyle ("muhtemel, orta güven"). Nedensellik iddia edilmez.
Bulguyu zayıflatan kanıtı da yaz: dönem seçimi, kısa rejim örneklemi, baz etkisi, nominal yanılsama.
Belirsizliği gizlemek okuru yanıltır; doğru ölçüde vermek ise kararı güçlendirir.
