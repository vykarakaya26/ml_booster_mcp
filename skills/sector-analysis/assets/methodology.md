## Veri ve hizalama
- Veriler resmi kaynaklardan (öncelikle TCMB EVDS) kullanıcının kendi API key'iyle çekilir; her çekim ayrı bir vintage olarak saklanır.
- Panel aylık dönem sonu takvimini kullanır. Her gözlem, **dönem sonu + publication lag** tarihinin düştüğü ayın satırına yazılır. Bu nedenle bir ayın satırında yalnızca o ayın sonunda bilinen veri bulunur (look-ahead yok).
- Günlük seriler aylık ortalamaya, akım seriler toplama indirgenir; tamamlanmamış son ay atılır. Çeyreklik seriler yalnızca ileriye doğru doldurulur (forward fill).
- Enflasyon düzeltmesi gerekiyorsa deflator, TÜFE'nin aylık değişimlerinden chain-link yöntemiyle elde edilir; 2025=100 baz değişikliği seriyi kırmaz.

## Dönüşümler
- `log_diff`: aylık log değişim (≈ % değişim / 100). Kur gibi seviyesi sürekli artan serilerde kullanılır.
- `yoy_pct`: 12 aylık % değişim. Enflasyon gibi mevsimsellik içeren serilerde kullanılır.
- `level`: seviye (faiz oranları, endeksler).

## Sınırlar
- Audit'teki structural break eşleştirmesi bilinen şok tarihlerine yakınlığa dayanır; nedensellik iddiası değildir.
- Backtest modelleri baseline niteliğindedir. Türkiye verisindeki 2018, 2021 ve 2023 gibi rejim değişimleri hata metriklerini büyütür.
- Katalogda durumu "reported" olan kaynakların henüz provider'ı yoktur; bu kaynaklar haritada görünür ama panele girmez.
- Proxy pazar payları (KAP gelirleri, arama ilgisi) gerçek pazar payı değildir.
