---
name: forecast-baseline
description: Türkiye makro/sektör panelindeki bir hedef seri (enflasyon, kur, faiz, kredi faizi, güven endeksi…) için look-ahead'e karşı korumalı rolling-origin backtest yapar. Naive/anchor naive, drift, son 12 ay ortalaması/medyanı, ridge, quantile regression (%10–%90 aralık) ve ortalama/medyan kombinasyonlarını (skewness yüksekse medyana geçen otomatik kural dahil) MAE, RMSE, interval coverage ve pinball loss ile karşılaştırır. Büyük bankaların ve TCMB anketinin yıl sonu forecast'lerini konsensüs olarak yanına koyar ve fan chart'lı bir HTML rapor üretir. Kullanıcı "tahmin", "forecast", "baseline", "backtest", "hangi model", "bu seriyi tahmin edebilir miyiz", "modelim iyi mi, neyle kıyaslayayım" dediğinde ya da bir ML modelinin geçmesi gereken benchmark'ı belirlemek istediğinde kullan. Feature üretimi ya da seçimi için kullanma (feature-engineering); oyuncu bazında pay tahmini için kullanma (market-share). Also: "baseline forecast / backtest for Turkish macro series".
allowed-tools:
  - Bash(${CLAUDE_SKILL_DIR}/../_shared/bin/mlb *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/render_backtest_report.py *)
---

# Baseline forecast backtest'i

Bu skill bir production modeli kurmaz; **benchmark'ı** (baseline performansı) ölçer. Kullanıcının modeli bu
baseline'ları geçemiyorsa ek değer üretmiyor demektir. Bu yüzden sonuçları abartmadan, olduğu gibi raporla.

## Akış

```bash
MLB="${CLAUDE_SKILL_DIR}/../_shared/bin/mlb"
SECTOR=otomotiv; OUT="mlbooster-reports/${SECTOR}-forecast-$(date +%Y%m%d)"; mkdir -p "$OUT"
"$MLB" build-panel --sector "$SECTOR" --start 2012-01 --out "$OUT/panel.csv"   # hedef adayları = kolonlar
"$MLB" backtest --sector "$SECTOR" --target <hedef> --horizon <h> --start 2012-01 --json "$OUT/bt.json"
```

1. **Hedef ve anchor**: hedef, `panel.csv` başlığındaki kolonlardan biri olmalıdır. Hedef günlük bir serinin
   **aylık ortalamasıysa** (aofm, kur, haftalık faizler), ay sonu değeri belirgin biçimde daha güncel bilgi
   taşır. Panelde `<hedef>_son` kolonu varsa (ör. `aofm_son`) `--anchor aofm_son` ver. Asıl benchmark
   "anchor naive" modelidir; yalnızca düz naive'i geçmek yeterli sayılmaz. Anchor kolonu yoksa bunu raporun
   sınırlar kısmında belirt. Kullanıcı ham bir seri istiyorsa (ör. "taşıt kredisi faizi") paneldeki karşılığı
   olan feature'ı bul. Panelde yoksa önce `evds-series-discovery` skill'iyle bağla.
2. **Ufuk**: kullanıcı belirtmediyse kararın sıklığına göre seç: fiyatlama ve likidite için 1 ay, bütçe ve
   planlama için 3–6 ay. Gerekirse birkaç ufku ayrı JSON dosyalarıyla koş.
3. **Model seçimi ve skewness.** Varsayılan set şunları içerir: naive, drift, drift_recent, mean12, median12,
   ridge, qr (quantile regression), combo_mean, combo_median, auto_robust. Audit'te ya da `diagnostics`
   alanında heavy tail ya da yüksek outlier oranı varsa medyan tabanlı sonuçları öne çıkar
   (`references/methodology.md` → "Çarpık veride ortalama ve medyan seçimi"). Kur gibi `managed_path`
   işaretli serilerde kontrollü rejimi ve tail risk'i ayrıca yaz.
4. **Dış forecast'ler (kullanıcı yıl sonu görüşü istiyorsa ya da hedef kur/enflasyon/faizse).**
   `${CLAUDE_SKILL_DIR}/../_shared/references/external-forecasts.md` dosyasına göre güncel kurum
   forecast'lerini WebSearch ile derle ve kaynak ve tarihleriyle birlikte `$OUT/external_forecasts.json`
   dosyasına yaz; kaynağı doğrulanmamış rakam ekleme. Rapora `--external "$OUT/external_forecasts.json"` ile,
   gerekirse `--latest usdtry=YYYY-AA:değer` ile ekle. Bu forecast'leri `assumptions.json` içine
   `kind: external, confirmed: false` olarak da kaydet.
5. **Varyantlar** (gerekirse): `--no-exog` ile ridge'i yalnızca hedefin kendi geçmişiyle dene. Exogenous
   değişkenler katkı sağlamıyorsa bunu raporda belirt. Tek modele bakmak için `--models naive,ridge` kullan.
6. **Yorum** → `$OUT/notes.md` (kısa Markdown): en iyi model, benchmark'a göre MAE farkı, ortak origin
   sayısı, `regime_metrics` alanına göre rejim bazında sonuçlar (2021 öncesi, 2021-12–2023, 2024+; sıralama
   rejime göre değişiyorsa bunu belirt) ve production modeli için benchmark. Ayrıntı
   `references/methodology.md` dosyasındadır. `notes.md`'yi akıcı, nesnel ve kurumsal Türkçeyle yaz. Teknik
   terimleri İngilizce bırak ve eki kesme işaretiyle ekle ("backtest'te", "baseline'ı", "pinball loss'u");
   terimlere Türkçe karşılık uydurma. Rakamları Türkçe biçimde yaz (MAE 0,45 puan; %80 coverage), yüzde ile
   yüzde puanı ayır.
7. **Rapor**:
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/render_backtest_report.py" --backtest "$OUT/bt.json" --notes "$OUT/notes.md" \
     --assumptions "$OUT/assumptions.json" [--external "$OUT/external_forecasts.json" --latest usdtry=YYYY-AA:değer] \
     --out "$OUT/backtest.html"
   ```

### 1. Girdileri ve varsayımları topla → `$OUT/assumptions.json`
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

## Yorum ilkeleri
- **Nokta ve aralık ayrı değerlendirilir.** En düşük MAE'yi veren model ile en düşük pinball loss'u veren
  model farklı olabilir; ikisini de raporla. %80 coverage belirgin biçimde düşükse aralıkların belirsizliği
  olduğundan dar gösterdiğini yaz.
- **Benchmark en güçlü naive'dir.** Anchor verildiyse anchor naive, verilmediyse naive. Bir model ancak bu
  benchmark'ı ortak örneklemde geçtiğinde "benchmark'ı geçti" denebilir.
- **Naive en iyi sonucu verirse** bu da bir bulgudur. Kısa vadede kur ya da yıllık (YoY) enflasyon için
  olağandır. "Model başarısız" ifadesini kullanma; "bu ufukta seri son değerinden daha iyi tahmin edilemiyor;
  benchmark naive MAE = X" biçiminde yaz.
- **MASE > 1** Türkiye verisinde sık görülür, çünkü test dönemi ilk training dönemine göre daha oynaktır.
  Bunu açıkla.
- Metrikleri yalnızca `common_origins` noktalarında karşılaştır. Rapor bunu otomatik yapar; yorumda da `n`
  değerini belirt.
- Tek bir backtest'e dayanarak "X modeli daha iyidir" genellemesi yapma. Sonucun döneme ve ufka bağlı
  olduğunu belirt.
- Gelecek değer tahmini verme (ör. "gelecek ay kur şu olur"). Bu skill geçmiş performansı ölçer; yatırım
  tavsiyesi değildir.

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
`feature-engineering` çıktısında seçilen feature'ların katkısı bu skill ile ölçülür:
`"$MLB" backtest ... --panel-file <features_selected.csv> --eval-start <training sonu>`. `--eval-start`,
feature seçimi ve preprocessing için kullanılan training penceresinin sonudur; metrikler yalnızca sonraki
origin'lerde hesaplanır. Sektör paneli de aynı `--eval-start` ile koşulur ve iki sonuç yan yana raporlanır.
Akış: `${CLAUDE_SKILL_DIR}/../_shared/references/workflow.md`.
