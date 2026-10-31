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

## Örnek anlık görüntü (2026-10-31)
`../assets/external_forecasts.example.json` dosyasında 33 kayıt var: TCMB PKA (Ekim), Matriks anketi,
TCMB Enflasyon Raporu, IMF WEO, JPMorgan, Deutsche Bank, ING, Garanti BBVA Yatırım ve Bloomberg kur
derlemesi. Değerler ikincil kaynaklıdır; yalnızca Garanti BBVA Yatırım notu birincildir.

Konsensüs çıktısı:

| Değişken | Ufuk | Konsensüs | Hesap yöntemi |
|---|---|---|---|
| TÜFE | 2026 yıl sonu | %29,3 | ortalama |
| TÜFE | 2027 yıl sonu | %23,6 | medyan: TCMB'nin %15'i aykırı, ortalama %21,8'e iner |
| Politika faizi | 2026 yıl sonu | %35 | |
| USD/TRY | 2026 yıl sonu | 51,4 | |

Bu dosya hızla bayatlar; her analizde güncellenmelidir.

Bilinen tuzaklar:
- Haziran 2026 tarihli bazı Bloomberg derlemelerinde, aynı gün spot kurun altında kalan yıl sonu
  tahminleri var (ör. 43–45). Bunlar eski girişlerdir; kullanma.
- PKA'da yıl sonu 2027 TÜFE ve kur sorusu yoktur, yalnızca 12 ve 24 ay sonrası vardır. Yaklaşık 2 aylık
  ufuk farkını not et.
- Repo ihaleleri durdurulduğundan beri efektif fonlama (AOFM) politika faizinin üstündedir. Hangi
  faizi tahmin ettiğini açıkça yaz.

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
