# Kalite kontrolleri: anlam, eşik, düzeltme

Kaynak: `mlbooster.domain.services.quality.audit_panel`. Eşikler `QualityThresholds` içindedir.

| Kontrol | Önem | Eşik | Anlamı | Tipik düzeltme |
|---|---|---|---|---|
| `stale` | high | Son gözlem 3 aydan eski | Serinin yayımı durmuş, seri arşive taşınmış ya da kodu değişmiş | `mlb catalog search` ile güncel grubu bul, `catalog set-feature` ile bağla |
| `empty` | high | Hiç gözlem yok | Kod yanlış ya da dönem dışında | Kodu `catalog series --datagroup` ile doğrula |
| `constant` | high | Tek bir benzersiz değer | Bilgi taşımaz (ör. sabit politika faizi dönemi + kısa panel) | Çıkar ya da daha uzun bir dönem kullan |
| `missing` | medium | Aralık içindeki eksik oranı > %10 | Kesintili yayın, frekans uyumsuzluğu | Frekansı kontrol et; ffill'i yalnızca seyrek seride kullan |
| `outliers` | medium | Robust z-score (MAD) > 5 | Kuyruk olayı; Türkiye verisinde çoğunlukla gerçek bir şoku yansıtır | Silme. Winsorize et, şok dummy'si ekle, log-fark kullan |
| `structural_break` | medium/info | Mean shift t > 4, 24 aylık pencere | Rejim değişimi. Bilinen bir şokla eşleşirse info, eşleşmezse medium | Rejim dummy'si, break sonrası örneklem ya da fark dönüşümü |
| `collinearity` | medium | \|korelasyon\| ≥ 0,95 | İki feature aynı bilgiyi taşıyor | Birini çıkar, oran ya da fark kullan |

## Bilinen şok takvimi (eşleştirme ±3 ay)
- 2018-08 kur şoku · 2020-03 COVID-19 · 2021-12 kur şoku / KKM · 2023-06 para politikasında normalleşme ·
  2026-01 TÜFE 2025=100 baz değişikliği

## Dikkat edilecek noktalar
- Structural break testi seviyeler üzerinde çalışır. Faiz gibi seviye serilerinde politika döngüleri doğal
  olarak break üretir; bunlar veri hatası değil, modelin bilmesi gereken rejimlerdir.
- Mean shift testi basit ve açıklanabilir bir testtir (Chow ya da Bai-Perron değildir). Kesin tarihi bulmak
  için bulguyu başlangıç noktası olarak kullan.
- Panel point-in-time'dır: bir değer, publication lag nedeniyle bir sonraki aya kaymış olabilir. Bu kayma hata
  değildir.
