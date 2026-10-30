# Güvenlik

## Açık bildirme

Güvenlik açığı bulduysanız lütfen herkese açık issue açmayın. GitHub'daki **Security → Report a vulnerability** (private advisory) ile bildirin.

## Tehdit modeli ve önlemler

| Risk | Önlem | Nerede |
|---|---|---|
| API anahtarının sızması | Anahtar yalnızca HTTP header'ında gider. URL'ye, loga ve ham veri manifestine yazılmaz. `Secret` nesnesi repr/str/pickle ile açığa çıkmaz. Sunucu anahtarı yanıtta geri yansıtırsa hata mesajında maskelenir. | `infrastructure/secret_store.py`, `providers/evds3.py` |
| Değiştirilmiş katalogla anahtarın başka sunucuya gönderilmesi | Her sağlayıcı izin verilen host'u kodda sabitler (EVDS3: `evds3.tcmb.gov.tr`). Katalog yalnızca `https` kabul eder. İstemci yönlendirme takip etmez ve ortamdaki proxy ayarlarını (`trust_env`) kullanmaz. | `providers/evds3.py`, `http_client.py`, `catalog_yaml.py` |
| URL parametre enjeksiyonu (EVDS path'e kodlanmadan eklenir) | Seri ve veri grubu kodları izin listesinden geçer: `^[A-Za-z0-9][A-Za-z0-9_.]{0,63}$`. | `domain/validation.py` |
| Dosya yolu geçişi (path traversal) | Kaynak ve sektör kimlikleri `^[a-z0-9_]+$` kuralına uymak zorundadır. Ham dosya adları içerik hash'inden türetilir. MCP araçları dosya yolu parametresi almaz. | `domain/validation.py`, `storage/*` |
| SQL enjeksiyonu | DuckDB sorgularında tüm değerler parametre olarak bağlanır. | `storage/observation_store.py` |
| Güvenilmeyen YAML | `yaml.safe_load` kullanılır, 256 KB boyut limiti vardır, pydantic `extra="forbid"` ile katı şema uygulanır. | `catalog_yaml.py` |
| Bellek tüketimi (DoS) | Yanıt boyutu limiti 50 MB, öğe limiti var. Zaman aşımı, rate limit ve en fazla 120 sn'lik `Retry-After` uygulanır. | `http_client.py` |
| MCP üzerinden bilgi sızması | Beklenen hatalar güvenli metinle döner. Beklenmeyen hataların içeriğini SDK istemciye göndermez. Araçlar read-only/destructive anotasyonlarıyla işaretlidir. | `interfaces/mcp_server.py` |
| Yerel veri | Veri dizini `0700` izinleriyle oluşturulur. Repoda veri ve sır yoktur (`.gitignore`). | `settings.py` |
| Bağımlılık açıkları | CI'da `pip-audit` çalışır; Dependabot haftalık güncelleme önerir. | `.github/` |

Bu önlemlerin her biri `tests/` altında testlerle doğrulanır (ör. `test_injection_attempts_rejected`, `test_refuses_untrusted_host`, `test_key_only_in_header_never_in_url_or_bronze`).
