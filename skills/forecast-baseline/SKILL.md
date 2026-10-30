---
name: forecast-baseline
description: Türkiye makro/sektör panelindeki bir hedef seri (enflasyon, kur, faiz, kredi faizi, güven endeksi…) için look-ahead korumalı rolling-origin backtest yapar. Naive, mevsimsel naive, drift ve ridge referans modellerini MAE/RMSE/sMAPE/MASE ile karşılaştırır ve grafikli HTML rapor üretir. Kullanıcı "tahmin", "forecast", "baseline", "backtest", "hangi model", "bu seriyi tahmin edebilir miyiz", "modelim iyi mi, neyle kıyaslayayım" dediğinde ya da bir ML modelinin geçmesi gereken çıtayı belirlemek istediğinde kullan. Also: "baseline forecast / backtest for Turkish macro series".
allowed-tools:
  - Bash(${CLAUDE_SKILL_DIR}/../_shared/bin/mlb *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/render_backtest_report.py *)
---

# Referans tahmin (baseline) backtest'i

Bu skill bir üretim modeli kurmaz, **çıtayı** ölçer. Kullanıcının modeli bu referansları geçmiyorsa
modelin değeri yoktur. Bu yüzden sonuçları abartmadan, olduğu gibi raporla.

## Akış

```bash
MLB="${CLAUDE_SKILL_DIR}/../_shared/bin/mlb"
SECTOR=otomotiv; OUT="mlbooster-reports/${SECTOR}-forecast-$(date +%Y%m%d)"; mkdir -p "$OUT"
"$MLB" build-panel --sector "$SECTOR" --start 2012-01 --out "$OUT/panel.csv"   # hedef adayları = kolonlar
"$MLB" backtest --sector "$SECTOR" --target <hedef> --horizon <h> --start 2012-01 --json "$OUT/bt.json"
```

1. **Hedef**: `panel.csv` başlığındaki kolonlardan biri olmalıdır. Kullanıcı ham bir seri istiyorsa
   (ör. "taşıt kredisi faizi") panelde hangi feature olduğunu bul. Panelde yoksa
   `evds-series-discovery` skill'iyle önce bağla.
2. **Ufuk**: kullanıcı belirtmediyse kararın sıklığına göre seç. Fiyatlama ve likidite için 1 ay,
   bütçe ve planlama için 3–6 ay. Gerekirse birkaç ufku ayrı JSON'larla koş.
3. **Varyantlar** (gerekirse): `--no-exog` ile ridge'i yalnızca hedefin geçmişiyle dene. Dış
   değişkenler fayda sağlamıyorsa raporda bunu söyle. Tek modeli görmek için `--models naive,ridge`
   kullan.
4. **Yorum** → `$OUT/notes.md` (kısa Markdown): en iyi model, naive'e göre MAE farkı, ortak nokta sayısı,
   hatanın büyüdüğü dönemler (tahmin tablosundan) ve üretim modeli için çıta. Ayrıntı
   `references/methodology.md` dosyasında.
5. **Rapor**:
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/render_backtest_report.py" --backtest "$OUT/bt.json" --notes "$OUT/notes.md" --out "$OUT/backtest.html"
   ```

## Yorum ilkeleri
- **Naive kazanırsa** bu bir bulgudur. Kısa vadede kur ya da YoY enflasyon için olağandır. "Model
  başarısız" deme; "bu ufukta seri, son değerden daha iyi tahmin edilemiyor; çıta naive MAE = X" de.
- **MASE > 1** TR verisinde sık görülür, çünkü test dönemi ilk eğitim dönemine göre daha oynaktır.
  Bunu açıkla.
- Metrikleri yalnızca `common_origins` noktada karşılaştır. Rapor bunu otomatik yapar; sen de
  `n` değerini belirt.
- Tek bir backtest'e dayanarak "X modeli daha iyidir" genellemesi yapma. Dönem ve ufuk bağımlılığını
  söyle.
- Gelecek değer tahmini (ör. "gelecek ay kur şu olur") verme. Bu skill geçmiş performansı ölçer;
  yatırım tavsiyesi değildir.
