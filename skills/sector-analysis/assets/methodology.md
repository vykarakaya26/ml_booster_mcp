## Veri ve hizalama
- Veriler resmi kaynaklardan (öncelikle TCMB EVDS) kullanıcının kendi API anahtarıyla çekilir; her çekim ayrı bir sürüm (vintage) olarak saklanır.
- Panel aylık dönem sonu takvimindedir. Her gözlem, **dönem sonu + yayın gecikmesi** tarihinin düştüğü ayın satırına yazılır; bu yüzden bir ayın satırında yalnızca o ay sonunda bilinen veri bulunur (look-ahead yok).
- Günlük seriler aylık ortalamaya, akım seriler toplama indirgenir; tamamlanmamış son ay atılır. Çeyreklik seriler yalnızca ileriye doldurulur.
- Enflasyon düzeltmesi gerekiyorsa deflatör TÜFE'nin aylık değişimlerinden zincirlenir; 2025=100 baz değişikliği seriyi kırmaz.

## Dönüşümler
- `log_diff`: aylık log değişim (≈ % değişim / 100). Kur gibi seviyesi sürekli artan serilerde kullanılır.
- `yoy_pct`: 12 aylık % değişim. Enflasyon gibi mevsimsellik içeren serilerde kullanılır.
- `level`: seviye (faiz oranları, endeksler).

## Sınırlar
- Kalite denetimindeki kırılma eşleştirmesi, bilinen şok tarihlerine yakınlığa dayanır; nedensellik iddiası değildir.
- Backtest modelleri referans (baseline) içindir. Türkiye verisinde 2018, 2021 ve 2023 gibi rejim değişimleri hata metriklerini büyütür.
- Katalogda "reported" durumundaki kaynakların henüz sağlayıcısı yoktur; bu kaynaklar haritada görünür ama panele girmez.
- Proxy pazar payları (KAP gelirleri, arama ilgisi) gerçek pazar payı değildir.
