# Kalite kontrolleri: anlam, eşik, düzeltme

Kaynak: `mlbooster.domain.services.quality.audit_panel`. Eşikler `QualityThresholds` içindedir.

| Kontrol | Önem | Eşik | Ne anlama gelir | Tipik düzeltme |
|---|---|---|---|---|
| `stale` | high | Son gözlem > 3 ay önce | Seri durmuş, arşive taşınmış ya da kodu değişmiş | `mlb catalog search` ile güncel grubu bul, `catalog set-feature` ile bağla |
| `empty` | high | Hiç gözlem yok | Kod yanlış ya da dönem dışı | Kodu `catalog series --datagroup` ile doğrula |
| `constant` | high | Tek benzersiz değer | Bilgi taşımaz (ör. sabit politika faizi dönemi + kısa panel) | Çıkar ya da daha uzun dönem al |
| `missing` | medium | Aralık içi eksik > %10 | Kesintili yayın, frekans uyumsuzluğu | Frekansı kontrol et; ffill yalnızca seyrek seride |
| `outliers` | medium | Robust z (MAD) > 5 | Kuyruk olayı; TR'de çoğunlukla gerçek şoktur | Silme. Winsorize et, şok dummy'si ekle, log-fark kullan |
| `structural_break` | medium/info | Ortalama kayması t > 4, pencere 24 ay | Rejim değişimi. Bilinen bir şokla eşleşirse info, eşleşmezse medium | Rejim dummy'si, kırılma sonrası örneklem ya da fark dönüşümü |
| `collinearity` | medium | \|korelasyon\| ≥ 0,95 | İki feature aynı bilgiyi taşıyor | Birini çıkar, oran/fark kullan |

## Bilinen şok takvimi (eşleştirme ±3 ay)
- 2018-08 kur şoku · 2020-03 COVID-19 · 2021-12 kur şoku / KKM · 2023-06 para politikasında normalleşme · 2026-01 TÜFE 2025=100 baz değişikliği

## Dikkat
- Kırılma testi seviyeler üzerinde çalışır. Faiz gibi seviye serilerinde politika döngüleri doğal
  olarak kırılma üretir; bunlar veri hatası değil, modelin bilmesi gereken rejimlerdir.
- Ortalama kayması basit ve açıklanabilir bir testtir (Chow/Bai-Perron değil). Kesin tarih için
  bulguyu bir başlangıç noktası olarak kullan.
- Panel nokta-zamanlıdır: bir değer, yayın gecikmesi nedeniyle bir sonraki aya kaymış olabilir.
  Bu kayma bir hata değildir.
