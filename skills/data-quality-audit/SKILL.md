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
python3 "${CLAUDE_SKILL_DIR}/scripts/render_audit_report.py" \
  --audit "$OUT/audit.json" --panel "$OUT/panel.csv" --sector "$SECTOR" --out "$OUT/audit.html"
```

- `health` "skipped" dönerse kullanıcıdan anahtarı `~/.mlbooster/.env` dosyasına koymasını iste.
  Anahtarı sohbete yazmasını isteme.
- Kullanıcı yalnızca tek bir seriyi soruyorsa (ör. "TP.KTF11 neden düz?") sektör paneli yerine
  `"$MLB" fetch --series <KOD> --start 2015-01` ile bak ve aynı kontrolleri sözlü uygula.

## Bulguları yorumla
`audit.json` dosyasını oku. `references/checks.md` her kontrolün anlamını, eşiğini ve tipik
düzeltmesini anlatır; rapor özetini ve önerileri buna göre yaz.
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
