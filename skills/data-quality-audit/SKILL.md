---
name: data-quality-audit
description: Türkiye makro/sektör feature panelinin veri kalitesini denetler ve önem derecesine göre sıralanmış bulgular içeren bir HTML rapor üretir. Stale ya da yayımı durdurulmuş seriler (ör. arşive taşınmış EVDS kodları), eksik aylar, robust z-score ile outlier'lar, structural break'ler (2018 ve 2021 kur şokları ve 2023 sıkılaştırmasıyla eşleştirilmiş) ve aşırı korelasyonlu feature çiftleri incelenir. Kullanıcı panelin, EVDS serilerinin ya da ML feature'larının "temiz mi", "güvenilir mi", "eksik/bozuk var mı", "model neden kötü" diye sorduğunda, modellemeden önce veri kontrolü istediğinde ya da bir seri beklenmedik davrandığında kullan. Veriyi dönüştürmek (eksik doldurma, scaling, outlier işleme) için kullanma; bunun için data-prep kullanılır. Also: "data quality check / audit Turkish macro features".
allowed-tools:
  - Bash(${CLAUDE_SKILL_DIR}/../_shared/bin/mlb *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/render_audit_report.py *)
---

# Veri kalitesi denetimi

Amaç, modellemeden önce analistin bakması gereken noktaları belirlemektir. Bulgular kesin hüküm değil, bir
inceleme listesidir. Her bulgu için uygulanabilir bir öneri de ver; kullanıcı rapordan doğrudan aksiyon
alabilmelidir.

## Akış

```bash
MLB="${CLAUDE_SKILL_DIR}/../_shared/bin/mlb"
"$MLB" health --source evds3          # API key ya da endpoint sorunu varsa önce onu çöz
SECTOR=bankacilik; OUT="mlbooster-reports/${SECTOR}-audit-$(date +%Y%m%d)"; mkdir -p "$OUT"
"$MLB" build-panel --sector "$SECTOR" --start 2012-01 --out "$OUT/panel.csv"
"$MLB" audit --sector "$SECTOR" --start 2012-01 --json "$OUT/audit.json"
# notes.md'yi aşağıdaki "Bulguları yorumla" adımında yaz, ardından:
python3 "${CLAUDE_SKILL_DIR}/scripts/render_audit_report.py" \
  --audit "$OUT/audit.json" --panel "$OUT/panel.csv" --sector "$SECTOR" --notes "$OUT/notes.md" \
  --assumptions "$OUT/assumptions.json" --out "$OUT/audit.html"
```

- `health` "skipped" dönerse kullanıcıdan API key'i `~/.mlbooster/.env` dosyasına eklemesini iste. Key'i
  sohbete yazmasını isteme.
- Kullanıcı yalnızca tek bir seriyi soruyorsa (ör. "TP.KTF11 neden düz?") sektör paneli yerine
  `"$MLB" fetch --series <KOD> --start 2015-01` ile incele ve aynı kontrolleri yanıtın içinde uygula.

### 1. Girdileri ve varsayımları topla → `$OUT/assumptions.json`
Yorum içeren her çıktı varsayımlara bağlanır; okur bir ifadenin neye dayandığını görebilmelidir. Bunun için
`${CLAUDE_SKILL_DIR}/../_shared/references/assumptions-guide.md` dosyasını oku.
- İstekten çıkarılabilenleri (sektör, hedef, horizon…) kendin doldur.
- Sonucu değiştirecek eksik girdileri kullanıcıya **tek seferde, en fazla 3–4 kısa soruyla** sor.
  AskUserQuestion aracı varsa onu kullan. Sorulacaklar: amaç ve karar, hedef değişken, horizon, nominal/reel.
  Sektör varsayımlarını `${CLAUDE_SKILL_DIR}/../_shared/references/sector-assumptions.md` dosyasındaki
  seçeneklerle sun.
- Yanıt gelmezse ya da kullanıcı "sen karar ver" derse sektör varsayılanlarıyla devam et. Bunları
  `kind: sector|default, confirmed: false` olarak kaydet; kullanıcı onaylamış gibi yazma.
- Şablon: `${CLAUDE_SKILL_DIR}/../_shared/assets/assumptions.example.json`. Rapor script'ine
  `--assumptions "$OUT/assumptions.json"` ver; dosya schema doğrulamasından geçmezse script hata verir.

## Bulguları yorumla → `$OUT/notes.md`
`audit.json` dosyasını oku. `references/checks.md` her kontrolün anlamını, eşiğini ve tipik düzeltmesini
anlatır. `diagnostics` alanındaki stationarity, kuyruk ve volatilite teşhislerini ve preprocessing önerilerini
`${CLAUDE_SKILL_DIR}/../_shared/references/analysis-playbook.md` ile yorumla.
Panelde sektörün hedef değişkeni yoksa bunu en üst sıradaki bulgu yap. `notes.md` dosyasına kısa bir özet ve
öncelik sırasına göre bir aksiyon listesi yaz. Bunlar raporun "Yorum ve öneriler" bölümünde
(`#recommendations`) görünür.
`notes.md`'yi akıcı, nesnel ve kurumsal Türkçeyle yaz. Teknik terimleri İngilizce bırak ve eki kesme
işaretiyle ekle ("outlier'lar", "structural break'ler", "winsorize etmek"); terimlere Türkçe karşılık uydurma.
Rakamları Türkçe biçimde yaz (0,92; %12,5), yüzde ile yüzde puanı ayır.
`notable_pairs` alanındaki 0,8–0,95 arası korelasyonları (ör. USD ile EUR kuru ≈ 0,9) ve aynı seriden türeyen
çiftleri (aofm/aofm_son) öneriye çevir. TÜFE'nin publication lag nedeniyle panelde bir ay sonraki satırda
durduğunu da belirt; bu kasıtlıdır ve hedefle birleştirilirken yeniden kaydırılmamalıdır.
Öncelik sırası:
1. **high / stale**: seri stale, yani yayımı durmuş. Kod büyük olasılıkla arşive taşınmıştır
   (TP.FG.J0 → TP.TUKFIY2025.GENEL gibi). Güncel kodu `evds-series-discovery` skill'iyle bul.
2. **high / empty, constant**: feature panelde bilgi taşımıyor. Seri kodunu ya da dönüşümü kontrol et.
3. **medium / structural_break (eşleşmeyen)**: seriyi inceleyerek veri hatasını gerçek bir rejim
   değişiminden ayır ve sonucu kullanıcıya bildir.
4. **medium / outliers**: şok aylarındaki (2018-08, 2021-12, 2023-06) değerler genellikle gerçek gözlemdir.
   Silme; winsorize et ya da dummy değişken ekle.
5. **medium / collinearity**: iki feature'dan birini çıkar ya da farklarını kullan.
6. **info**: bilinen şoklarla eşleşen structural break'ler. Rejim dummy'si önerisiyle birlikte kaydet.

## Teslim
Rapor yolunu, bulgu sayılarını (yüksek/orta/bilgi) ve önem sırasına göre en fazla 5 aksiyon maddesini ilet.
Panelde atlanan feature'lar (`skipped_features`) varsa bunları ayrıca belirt.

## Dayanak kuralı (şeffaflık ve izlenebilirlik)
Her ifade dört dayanaktan birine bağlanır ve bu dayanak metinde görünür (ayrıntı:
`${CLAUDE_SKILL_DIR}/../_shared/references/assumptions-guide.md`):
- **Veri**: yayımlanmış gözlem; ay ve kaynakla.
- **Varsayım**: `(A2)` gibi bir kimlikle. Onaylanmamış ya da güncelliğini yitirmiş bir varsayıma dayanan
  sonuç koşullu yazılır.
- **Model**: istatistiksel hesap (ör. structural break testi, korelasyon); yöntem ve örneklemle.
- **Yorum**: analistin çıkarımı; güven düzeyiyle ("muhtemel, orta güven"). Nedensellik iddia edilmez.
Bulguyu zayıflatan kanıtı da yaz: dönem seçimi, kısa rejim örneklemi, baz etkisi, nominal yanılsama.
Belirsizliği gizlemek okuru yanıltır; doğru ölçüde vermek kararı güçlendirir.

## Bütünsel akıştaki yeri
Audit'ten sonra `data-prep` skill'i, burada işaretlenen outlier'ları ve structural break'leri dağılıma göre ele
alır (clipping ya da indicator, fark alma). Break tarihleri `feature-engineering` olay değişkenleriyle tutarlı
yorumlanır. Akış: `${CLAUDE_SKILL_DIR}/../_shared/references/workflow.md`.
