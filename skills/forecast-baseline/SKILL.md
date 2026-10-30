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

1. **Hedef ve çapa**: hedef, `panel.csv` başlığındaki kolonlardan biri olmalıdır. Hedef günlük bir
   serinin **aylık ortalamasıysa** (aofm, kur, haftalık faizler), ay sonu değeri çok daha güncel bilgi
   taşır. Panelde `<hedef>_son` kolonu varsa (ör. `aofm_son`) `--anchor aofm_son` ver. Gerçek çıta
   "çapa naive" modelidir ve düz naive'i geçmek bir başarı sayılmaz. Çapa kolonu yoksa bunu rapordaki
   sınırlar kısmında belirt. Kullanıcı ham bir seri istiyorsa
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
   python3 "${CLAUDE_SKILL_DIR}/scripts/render_backtest_report.py" --backtest "$OUT/bt.json" --notes "$OUT/notes.md" \
     --assumptions "$OUT/assumptions.json" --out "$OUT/backtest.html"
   ```

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

## Yorum ilkeleri
- **Çıta en güçlü naive'dir.** Çapa verildiyse çapa naive, verilmediyse naive. Bir model yalnızca
  bu çıtayı ortak örneklemde geçtiğinde "çıtayı geçti" denebilir.
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

## Dayanak kuralı (yanıltıcı değil, güçlendirici)
Her ifade dört dayanaktan birine bağlanır ve bu dayanak metinde görünür (ayrıntı:
`${CLAUDE_SKILL_DIR}/../_shared/references/assumptions-guide.md`):
- **Veri**: yayımlanmış gözlem; ay ve kaynakla.
- **Varsayım**: `(A2)` gibi kimlikle. Onaysız ya da bayat varsayıma dayanan sonuç koşullu yazılır.
- **Model**: tahmin veya backtest sonucu; yöntem, örneklem ve belirsizlikle.
- **Yorum**: analistin çıkarımı; güven düzeyiyle ("muhtemel, orta güven"). Nedensellik iddia edilmez.
Bulguyu zayıflatan kanıtı da yaz: dönem seçimi, kısa rejim örneklemi, baz etkisi, nominal yanılsama.
Belirsizliği gizlemek okuru yanıltır; doğru ölçüde vermek ise kararı güçlendirir.
