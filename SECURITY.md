# Security

## Reporting a vulnerability

Vulnerabilities must not be reported as public issues. Report them through **Security → Report a vulnerability** (private advisory) on GitHub.

## Threat model and mitigations

| Risk | Mitigation | Where |
|---|---|---|
| API key leakage | The key is sent only in an HTTP header. It is never written to URLs, logs or the raw data manifest. The `Secret` object is not exposed through repr/str/pickle. If the server echoes the key back in a response, it is masked in the error message. | `infrastructure/secret_store.py`, `providers/evds3.py` |
| Key sent to another server through a modified catalog | Each provider pins its allowed host in code (EVDS3: `evds3.tcmb.gov.tr`). The catalog accepts only `https`. The client does not follow redirects and does not use environment proxy settings (`trust_env`). | `providers/evds3.py`, `http_client.py`, `catalog_yaml.py` |
| URL parameter injection (EVDS appends parameters to the path without encoding) | Series and data group codes pass an allow-list: `^[A-Za-z0-9][A-Za-z0-9_.]{0,63}$`. | `domain/validation.py` |
| Path traversal | Source and sector identifiers must match `^[a-z0-9_]+$`. Raw file names are derived from the content hash. MCP tools take no file path parameter. | `domain/validation.py`, `storage/*` |
| SQL injection | All values in DuckDB queries are bound as parameters. | `storage/observation_store.py` |
| Untrusted YAML | `yaml.safe_load` is used with a 256 KB size limit, and a strict schema is enforced with pydantic `extra="forbid"`. | `catalog_yaml.py` |
| Memory exhaustion (DoS) | Response size limit of 50 MB and an item limit. Timeouts, rate limiting and a `Retry-After` of at most 120 s are applied. | `http_client.py` |
| Information leakage through MCP | Expected errors are returned with safe text. The SDK does not send the content of unexpected errors to the client. Tools are marked with read-only/destructive annotations. | `interfaces/mcp_server.py` |
| Local web panel used by other sites (CSRF, DNS rebinding) | The server binds to `127.0.0.1` only. Requests whose Host header is not loopback are rejected. Every `/api` request must carry the random per-launch session token in the `X-MLB-Token` header, and a cross-origin `Origin` is rejected. No CORS. | `interfaces/web/app.py` |
| Script injection (XSS) and information leaks in the web panel | The CSP allows scripts and styles from the same origin only (no `unsafe-inline`) and forbids framing. The frontend writes data with `textContent` only. The API takes and returns no file paths, request bodies are capped at 64 KB, and unexpected errors return no details. | `interfaces/web/` |
| Local data | The data directory is created with `0700` permissions. The repository contains no data and no secrets (`.gitignore`). | `settings.py` |
| Dependency vulnerabilities | `pip-audit` runs in CI; Dependabot proposes weekly updates. | `.github/` |

Each of these mitigations is verified by tests under `tests/` (e.g. `test_injection_attempts_rejected`, `test_refuses_untrusted_host`, `test_key_only_in_header_never_in_url_or_bronze`, `test_rejects_non_loopback_host`, `test_api_requires_token_and_same_origin`).
