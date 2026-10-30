# ML Booster Map — Proje Spesifikasyonu

## Amaç
Kullanıcı Türkiye'de bir sektör seçer; sistem o sektör için (a) ücretsiz veri kaynakları haritasını,
(b) makro/sektör feature'larını, (c) pazar payı serilerini (doğrudan veya proxy) üretir ve
ML'e hazır aylık panel (Parquet) olarak dışa verir. Dağıtım: GitHub reposu, `pip install git+...`.
Arayüz yok — kullanım CLI (`mlbooster`) ve MCP sunucusu (`mlbooster-mcp`, stdio) üzerinden.

## Teknoloji
- Python 3.12+, httpx, pandas, pydantic v2, duckdb ≥2, pyarrow, typer, python-dotenv
- MCP: `mcp` 2.x — `from mcp.server.mcpserver import MCPServer` (1.x'teki FastMCP 2.x'te yok)
- PDF (sonraki fazlar): pdfplumber + camelot (fallback); Excel: openpyxl
- Test: pytest + respx (mock'lanmış HTTP) + tests/fakes.py (port sahteleri); canlı contract testleri `-m live`
- CI: GitHub Actions (pytest 3.12/3.13 + pip-audit), Dependabot

## Mimari: Onion — bağımlılıklar yalnızca dıştan içe (tests/test_architecture.py zorlar)
```
src/mlbooster/
  domain/            # çekirdek: model.py (frozen dataclass'lar), validation.py (izin listeleri, DateRange),
                     # observations.py (gözlem çerçevesi sözleşmesi), services/ (frekans, deflatör, feature,
                     # chunking, sector_map). İzinli: stdlib + pandas/numpy. G/Ç YOK.
  application/       # ports.py (Protocol'ler), dto.py (FetchRequest: girdi doğrulama kapısı), errors.py,
                     # use_cases/ (ListSectors, ListSources, GetSectorMap, DiscoverSeries, FetchSeries,
                     # BuildPanel, RunHealthChecks). Yalnızca domain'e bağımlı.
  infrastructure/    # port uygulamaları: providers/ (evds3, registry), http_client.py (SecureHttpClient),
                     # catalog_yaml.py (pydantic → domain), secret_store.py, settings.py, storage/
  interfaces/        # cli.py (typer), mcp_server.py (MCPServer), presenters.py — yalnızca use case çağırır
  bootstrap.py       # composition root: tek bağlama noktası
  catalog_data/      # sources/*.yaml, sectors/*.yaml (package-data)
tests/               # domain/, application/ (fakes.py ile), infrastructure/ (respx), interfaces/, test_architecture.py
```
Kurallar:
- Domain/application içine httpx, duckdb, pyarrow, yaml, pydantic, mcp, typer, dotenv import ETME.
- Interfaces altyapıyı doğrudan import ETMEZ; `bootstrap.build_container()` kullanır.
- Dış dünyadan gelen her kimlik/kod `domain.validation` ile doğrulanır (FetchRequest.create, use case girişleri).
- Hatalar: beklenenler `DomainError` alt sınıfları (kullanıcıya gösterilebilir, sır/yol içermez);
  sağlayıcı hataları `application.errors` içinde; altyapı kendi hatalarını bunlara çevirir.

## SeriesProvider port'u (application/ports.py)
- `list_series(query) -> DataFrame[series_id, name, freq, start, end, datagroup]`
- `fetch(FetchRequest) -> gözlem çerçevesi (domain.observations.OBSERVATION_COLUMNS)`
- `healthcheck() -> HealthResult(ok|fail|skipped)`: `canary: true` serilerden son gözlemler; anahtar yoksa skipped
- `close()`
- Yeni sağlayıcı: sınıfı `infrastructure/providers/` altına yaz, `registry.PROVIDERS`'a ekle, katalogda `adapter:` ver.
  İzinli host'u sınıfta sabitle (katalogdaki base_url'e güvenme), sırrı `EnvSecretProvider` ile al.

## Güvenlik (SECURITY.md'deki tehdit modeli)
- SecureHttpClient: yalnızca https, yönlendirme takip etmez, trust_env=False, 50 MB yanıt limiti, timeout, Retry-After ≤120 sn.
- Sırlar `Secret` nesnesinde; hata mesajlarında `redact()`; URL/log/manifest'e asla.
- DuckDB'de yalnızca parametreli sorgu; dosya adları hash/slug'dan türetilir; veri dizinleri 0700.
- MCP araçları dosya yolu almaz; DomainError → ToolError, diğer hatalar SDK tarafından gizlenir.

## Kaynak-özel kurallar (KRİTİK)
1. EVDS3 (yapıldı — infrastructure/providers/evds3.py):
   - Base: https://evds3.tcmb.gov.tr/igmevdsms-dis/ ; parametreler path'e eklenir, '?' YOK.
   - API key SADECE header `key`. URL'ye/loglara asla yazma. Yalnızca evds3.tcmb.gov.tr host'una gönderilir.
   - Canlı gözlem 2026-10-30: anahtarsız → 403 JSON, geçersiz anahtar → 401 text/plain, evds2 → 302.
   - Geçerli anahtarla yanıt biçimi (`items`, `Tarih`, `TP_X_Y` kolonları, formüllü kolon `KOD-<n>`) evds 0.4
     kütüphanesinden türetildi; `pytest -m live` ile doğrulanmalı.
   - Tarih aralığı parçalanır (`max_obs_per_call`, varsayılan 1000, doğrulanmadı).
   - formula != 0 ise düzey ayrıca istenir (orijinal değer None döner).
   - HTML / boş / 3xx yanıt → UnexpectedResponse.
   - Seri kodlarını kodda hard-code etme; katalogda tut, yenilerini `/serieList` veya EVDS-MCP ile keşfet.
2. TÜİK: strateji zinciri veriportali JSON → istab Excel → MEDAS (opsiyonel). nsiws SDMX'e bağımlı olma.
   TÜFE 2025=100 baz; deflatör aylık % değişimlerden chain-link.
3. EPİAŞ: POST https://giris.epias.com.tr/cas/v1/tickets (body: username, password; form-urlencoded),
   TGT 8 saat önbellek, header "TGT". ≤60 istek/sn, ≤1500/dk; 429 → 30 sn bekle.
4. BDDK / TBB / BKM: HTML tablo + Excel indirme; dosya hash'iyle değişiklik tespiti.
5. BTK / ODMD / EPDK / TSB: duyuru sayfasını izle, yeni PDF/Excel'i indir, parser'la çıkar,
   doğrula (payların toplamı 100±0.5, önceki dönemle tutarlılık), silver'a yaz.
6. KAP: kap.org.tr'yi toplu scrape ETME; MKK API Portal kullan.
7. Google Trends: yalnızca manuel CSV import adapter'ı.

## Gözlem (silver) şeması (domain/observations.py; Parquet şeması storage/observation_store.py)
source_id, dataset_id, series_id, entity_id, entity_name, metric, date (dönem sonu), freq, value, unit,
currency, is_proxy, vintage_ts, publication_lag_days. Anahtar: source_id, series_id, entity_id, metric, freq, date.
`metric`: EVDS formül adı (level, pct_change, yoy_pct, ...) + agregasyon verildiyse `_<agg>`.

## Transform kuralları
- Ortak takvim: aylık dönem sonu. Gözlem `dönem sonu + publication_lag_days` ayına yazılır (look-ahead yok).
- Günlük→aylık: seviye=avg (veya last), akım=sum; eksik son ay atılır. Çeyreklik/yıllık→aylık: ffill.
- Reel = nominal / deflatör (TÜFE aylık % değişimlerinden chain-link, referans ay parametrik).
- Pazar payı: entity_value / sector_total; proxy ise is_proxy=True ve coverage oranı ekle.

## Kabul kriterleri (MVP)
- 10 adapter (EVDS3 ✓, TÜİK, BDDK, TBB, EPİAŞ, EPDK, BTK, TSB, ODMD, BKM) healthcheck geçer.
- 5 sektör (bankacılık, sigorta, telekom, otomotiv, akaryakıt) için pazar payı serisi gold panelde.
- Her kaynak YAML'ında lisans, auth, frekans, publication_lag, verification_status dolu (test_catalog.py zorlar).
- Secrets .env'den; hiçbir anahtar log/URL'de görünmez.

## Yapma
- Ücretli kaynak entegre etme (Nielsen, Euromonitor, IQVIA yalnızca dokümanda).
- robots.txt / kullanım koşullarını ihlal eden scraping, oturum açma gerektiren alanlara erişim.
- Kişisel veri içeren alan saklama (KVKK).
- Ham veriyi repoya koyma; repo yalnızca kod + katalog içerir.
