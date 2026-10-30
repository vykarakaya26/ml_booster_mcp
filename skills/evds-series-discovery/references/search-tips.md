# EVDS arama ipuçları ve doğrulanmış kodlar

## Eş anlamlılar / sorgu önerileri
| Aranan kavram | Denenecek sorgular | Not |
|---|---|---|
| Kredi faizi | "kredi faiz", "kredi faiz oranları akım" | Akım = yeni açılan krediler; Stok = mevcut portföy |
| Mevduat faizi | "mevduat faiz", "mevduat faiz oranları akım" | Vadeye göre alt seriler var; "toplam" ya da 1–3 ay vadeyi tercih et |
| Tüketici güveni | "tüketici güven" | `bie_mbgven2` güncel grup, `bie_mbgven` arşivdir |
| Reel kesim güveni | "reel kesim güven" | `bie_rkgey2` |
| Enflasyon beklentisi | "beklenti", "sektörel enflasyon beklentileri", "hanehalkı beklenti" | PKA'nın eski grupları arşivde; güncel beklentiler başka adlarla geçiyor |
| TÜFE | "tüketici fiyat endeksi 2025" | 2003=100 grupları arşivde ya da durmuş durumda |
| Dış borç | "brüt dış borç" | `bie_dbdborc` kaldırıldı |
| Politika faizi / fonlama | "fonlama", "apifon" | `TP.APIFON4` = AOFM |
| Kart harcamaları | "kart harcama", "kartlı" | Sektörel kırılımlar alt serilerde |
| Kapasite kullanımı | "kapasite kullanım" | |
| Konut fiyat endeksi | "konut fiyat" | |

## Canlı doğrulanmış kodlar (2026-10-30)
| Kavram | Kod | Grup | Frekans | Not |
|---|---|---|---|---|
| USD/TRY alış | `TP.DK.USD.A` | bie_dkdovizgn | İş günü | `.YTL` sonekli eski kod takma addır |
| EUR/TRY alış | `TP.DK.EUR.A` | bie_dkdovizgn | İş günü | |
| TÜFE genel (2025=100) | `TP.TUKFIY2025.GENEL` | bie_tukfiy2025 | Aylık | 2005'ten itibaren |
| TÜFE genel (2003=100, devam) | `TP.GENENDEKS.T1` | bie_tukfiy2003 | Aylık | Eski baz karşılaştırması için |
| AOFM | `TP.APIFON4` | bie_apifon | İş günü | |
| Brüt dış borç, toplam | `TP.BRUTDBORCLU.G1` | bie_brutdbborclu | Çeyreklik | milyon USD |
| İhtiyaç kredisi faizi (TL, akım) | `TP.KTF10` | bie_kt100h | Haftalık (Cuma) | |
| Taşıt kredisi faizi (TL, akım) | `TP.KTF11` | bie_kt100h | Haftalık (Cuma) | |
| Konut kredisi faizi (TL, akım) | `TP.KTF12` | bie_kt100h | Haftalık (Cuma) | |
| Ticari kredi faizi (TL, akım) | `TP.KTF17` | bie_kt100h | Haftalık (Cuma) | |
| Tüketici kredisi faizi (TL, akım) | `TP.KTFTUK` | bie_kt100h | Haftalık (Cuma) | |
| Tüketici güven endeksi | `TP.TG2.Y01` | bie_mbgven2 | Aylık | 100 eşiği iyimserlik ayrımı |

## Bilinen yayın gecikmeleri (`--lag`, gün; dönem sonuna göre)
Gecikme, değerin **dönem sonundan kaç gün sonra** yayımlandığıdır. Ay içinde yayımlanan seriler için
0 verilir. Gecikme verilmezse varsayılan muhafazakârdır (aylık 30 gün → 2 ay kayma) ve feature'ı
gereksiz yere bir ay geç gösterir. Biliniyorsa mutlaka verin.

| Seri | Yayın | `--lag` |
|---|---|---|
| TÜFE (TÜİK/EVDS) | İzleyen ayın 3'ü | 3 |
| Tüketici güven endeksi (TP.TG2.*) | Aynı ayın ~20'si | 0 |
| Reel kesim güven, kapasite kullanımı | Aynı ayın ~25'i | 0 |
| Haftalık kredi/mevduat faizleri (bie_kt100h, bie_mt100h) | ~2 hafta sonra (Cuma verisi, sonraki haftanın sonunda) — 2026-10-30'da son veri 10-16 | 14 |
| Kur, AOFM (günlük) | Ertesi iş günü | 1 |
| Dış borç (çeyreklik) | ~3 ay | 90 |

## Bilinen tuzaklar
- `TP.FG.J0` 2026-01'de durdu ama hâlâ eski veri döndürür. Hata vermez, yalnızca `stale` olarak
  görünür.
- `TP.DB.B01` HTTP 400 döndürür; seri kaldırılmıştır.
- `/serieList` bazı gruplarda boş liste döner (kaldırılmış grup). Aramaya geri dön.
- Frekans etiketleri tutarsızdır: "İŞ GÜNÜ", "HAFTALIK(CUMA)", "AYDA İKİ KEZ". CLI bunları
  normalleştirir.
- Formüllü çekimde kolon adı `KOD-<n>` olur ve düzey kolonu dönmez. `mlbooster` düzeyi ayrıca ister.
