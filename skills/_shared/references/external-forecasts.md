# Dış tahminler: derleme ve kullanım

Büyük bankaların (JPMorgan, Morgan Stanley, Goldman Sachs, Citi, HSBC, Deutsche Bank…), uluslararası
kuruluşların (IMF, OECD, Dünya Bankası) ve TCMB'nin tahminleri modele **girdi** ya da **karşılaştırma
noktası** olur. Bunlar **veri değil, varsayımdır**: kaynak, yayın tarihi ve birincil/ikincil ayrımıyla
kaydedilir.

## Ne zaman kullanılır
- Kullanıcı yıl sonu kur, enflasyon ya da faiz görüşü istediğinde, ya da senaryo kurarken.
- Backtest çıtası ile piyasa görüşünü yan yana koymak için. "Model ne diyor, kurumlar ne diyor?"
- Kontrollü kur rejiminde (`managed_path`): kurumların ima ettiği yıllık değer kaybı, mevcut aylık
  patikayla karşılaştırılır.

## Derleme (WebSearch ile, her çalıştırmada taze)
1. **Resmi ve birincil kaynaklar öncelikli:**
   - TCMB Piyasa Katılımcıları Anketi: yıl sonu TÜFE, kur ve politika faizi beklentisi. EVDS'de 12 ay
     sonrası TÜFE beklentisi `TP.ENFBEK.PKA12ENF` (bankacılık panelinde `pka_enflasyon_beklentisi`).
   - TCMB Enflasyon Raporu: ara hedefler ve tahmin.
   - IMF WEO (Nisan/Ekim), OECD Economic Outlook.
2. **Banka notları** çoğunlukla ücretli ya da kapalıdır. Haber ajansı (Reuters, Bloomberg, AA) ve aracı
   kurum özetlerinden gelen değerleri `primary: false` olarak kaydet.
3. **Örnek sorgular:**
   - "JPMorgan Turkey lira year-end forecast 2026"
   - "Goldman Sachs Türkiye enflasyon tahmini yıl sonu"
   - "Reuters poll Turkish lira forecast"
   - "Piyasa Katılımcıları Anketi yıl sonu dolar beklentisi"
4. **Her kayıt için alanlar:** kurum, değişken, ufuk (YYYY-AA), değer, birim, yayın tarihi, URL,
   birincil mi, not. Şema `../mlb_report/external.py` içinde, örnek dosya
   `../assets/external_forecasts.example.json`.
5. **Uydurma yok.** Bulunamayan kurum için satır ekleme; "bulunamadı" de. Rakamı yalnızca kaynağında
   gördüğün haliyle yaz.

## Raporda nasıl özetlenir
- Değişken ve ufuk başına kurum sayısı, medyan, ortalama, en düşük ve en yüksek.
- **Konsensüs kuralı:** çeyrekler arası açıklık medyanın %10'unu aşıyorsa ya da 3'ten az tahmin
  varsa konsensüs = **medyan**; aksi halde ortalama. Ortalama tek bir aykırı kuruma çok duyarlıdır.
- **Bayatlık:** 92 günden eski tahminler "bayat" diye işaretlenir. TR'de görüşler bir kur şokundan
  sonra hızla değişir.
- **İma edilen değişim:** `--latest usdtry=YYYY-AA:değer` verilirse yıl sonu konsensüsünün son değere
  göre % farkı hesaplanır. Bunu mevcut aylık patikanın yıllıklandırılmış haliyle karşılaştır.

## Yorumlarken
- Kurum tahminleri rejim varsayımı içerir; bu genellikle "kontrollü değer kaybı sürer" varsayımıdır.
  Bu varsayımı açıkça yaz: `assumptions.json` içinde `kind: external`, `confirmed: false`.
- Tahminler arasındaki dağılım bir belirsizlik göstergesidir ama gerçek belirsizliği eksik gösterir:
  kurumlar sürü davranışı gösterebilir. Kantil aralığıyla birlikte sun.
- Kurumların geçmiş isabeti bilinmeden "en iyi kurum" seçme.
- Kullanıcı açıkça istemedikçe kendi nokta tahminini kurum tahminlerinin yerine koyma.
