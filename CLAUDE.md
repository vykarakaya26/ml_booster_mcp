# ML Booster Map — Project Specification

## Purpose
The user selects a sector in Turkey; the system produces for that sector (a) a map of free data sources,
(b) macro/sector features, and (c) market share series (direct or proxy), and exports them as an ML-ready
monthly panel (Parquet). Distribution: GitHub repository, `pip install git+...`.
No UI — usage is through the CLI (`mlbooster`) and the MCP server (`mlbooster-mcp`, stdio).

## Technology
- Python 3.12+, httpx, pandas, pydantic v2, duckdb ≥2, pyarrow, typer, python-dotenv
- MCP: `mcp` 2.x — `from mcp.server.mcpserver import MCPServer` (FastMCP from 1.x does not exist in 2.x)
- PDF (later phases): pdfplumber + camelot (fallback); Excel: openpyxl
- Tests: pytest + respx (mocked HTTP) + tests/fakes.py (port fakes); live contract tests with `-m live`
- CI: GitHub Actions (pytest 3.12/3.13 + pip-audit), Dependabot

## Architecture: Onion — dependencies only from the outside in (enforced by tests/test_architecture.py)
```
src/mlbooster/
  domain/            # core: model.py (frozen dataclasses), validation.py (allow-lists, DateRange),
                     # observations.py (observation frame contract), services/ (frequency, deflator, features,
                     # chunking, sector_map, market_share). Allowed: stdlib + pandas/numpy. NO I/O.
  application/       # ports.py (Protocols), dto.py (FetchRequest: input validation gate), errors.py,
                     # use_cases/ (ListSectors, ListSources, GetSectorMap, DiscoverSeries, FetchSeries,
                     # BuildPanel, RunHealthChecks, AnalyzeMarketShare). Depends only on domain.
  infrastructure/    # port implementations: providers/ (evds3, registry), http_client.py (SecureHttpClient),
                     # catalog_yaml.py (pydantic → domain), secret_store.py, settings.py, storage/,
                     # table_reader.py (market share CSV/JSON input; CLI only)
  interfaces/        # cli.py (typer), mcp_server.py (MCPServer), presenters.py — call use cases only
  bootstrap.py       # composition root: the single wiring point
  catalog_data/      # sources/*.yaml, sectors/*.yaml (package-data); user override: ~/.mlbooster/catalog/overrides.yaml
skills/              # Claude Code plugin skills (sector-analysis, evds-series-discovery, data-quality-audit,
                     # forecast-baseline, market-share); _shared/ = stdlib-only report library + bin/mlb CLI wrapper
.claude-plugin/      # plugin.json (mcpServers: uvx --from ${CLAUDE_PLUGIN_ROOT}) + marketplace.json; there is NO
                     # .mcp.json at the root — if there were, sessions opened in the repo folder would treat it as a
                     # project MCP server and run it without ${CLAUDE_PLUGIN_ROOT}
tests/               # domain/, application/ (with fakes.py), infrastructure/ (respx), interfaces/, test_architecture.py
```
Rules:
- Do NOT import httpx, duckdb, pyarrow, yaml, pydantic, mcp, typer or dotenv in domain/application.
- Interfaces do NOT import infrastructure directly; they use `bootstrap.build_container()`.
- Every identifier/code coming from the outside world is validated with `domain.validation` (FetchRequest.create,
  use case entry points).
- Analysis logic (quality audit, backtest) lives in the package and is tested; skill scripts only handle
  presentation, import nothing outside the stdlib (enforced by tests/skills) and escape every value written to HTML.
- Versioning: there is NO `version` in `.claude-plugin/plugin.json`. The plugin tracks the git commit of the
  installed copy, so every merge into `main` reaches users through automatic updates. The MCP server and `mlb` run
  from the plugin root (`uvx --from ${CLAUDE_PLUGIN_ROOT}`). For pip users, bump the pyproject `version` and
  `__version__` in `src/mlbooster/__init__.py`, and push a `vX.Y.Z` tag with the same name; `check_updates` reads
  these tags.
- The build backend is hatchling: it does not write egg-info/build into the source tree (nothing must be written
  into the plugin directory).
- Errors: expected ones are `DomainError` subclasses (safe to show to the user, contain no secrets or paths);
  provider errors live in `application.errors`; infrastructure translates its own errors into these.

## SeriesProvider port (application/ports.py)
- `list_series(query) -> DataFrame[series_id, name, freq, start, end, datagroup]`
- `fetch(FetchRequest) -> observation frame (domain.observations.OBSERVATION_COLUMNS)`
- `healthcheck() -> HealthResult(ok|fail|skipped)`: latest observations of `canary: true` series; skipped if no key
- `close()`
- New provider: write the class under `infrastructure/providers/`, add it to `registry.PROVIDERS`, set `adapter:`
  in the catalog. Pin the allowed host in the class (do not trust the base_url in the catalog), and obtain the
  secret with `EnvSecretProvider`.

## Security (threat model in SECURITY.md)
- SecureHttpClient: https only, does not follow redirects, trust_env=False, 50 MB response limit, timeout,
  Retry-After ≤120 s.
- Secrets live in a `Secret` object; `redact()` in error messages; never in URLs, logs or the manifest.
- DuckDB: parameterized queries only; file names are derived from hashes/slugs; data directories are 0700.
- MCP tools take no file paths; DomainError → ToolError, other errors are hidden by the SDK.

## Source-specific rules (CRITICAL)
1. EVDS3 (done — infrastructure/providers/evds3.py):
   - Base: https://evds3.tcmb.gov.tr/igmevdsms-dis/ ; parameters are appended to the path, NO '?'.
   - The API key goes ONLY in the `key` header. Never write it to URLs or logs. It is sent only to the
     evds3.tcmb.gov.tr host.
   - Live observation 2026-10-30: no key → 403 JSON, invalid key → 401 text/plain, evds2 → 302.
   - The response format with a valid key (`items`, `Tarih`, `TP_X_Y` columns, formula column `KOD-<n>`) was
     derived from the evds 0.4 library; it must be verified with `pytest -m live`.
   - The date range is chunked (`max_obs_per_call`, default 1000, not verified).
   - If formula != 0, the level is requested separately (the original value is returned as None).
   - HTML / empty / 3xx response → UnexpectedResponse.
   - Do not hard-code series codes in code; keep them in the catalog, and discover new ones with `/serieList` or
     EVDS-MCP.
2. TÜİK: strategy chain veriportali JSON → istab Excel → MEDAS (optional). Do not depend on nsiws SDMX.
   CPI (TÜFE) base 2025=100; the deflator is chain-linked from monthly % changes.
3. EPİAŞ: POST https://giris.epias.com.tr/cas/v1/tickets (body: username, password; form-urlencoded),
   TGT cached for 8 hours, header "TGT". ≤60 requests/s, ≤1500/min; 429 → wait 30 s.
4. BDDK / TBB / BKM: HTML tables + Excel downloads; change detection by file hash.
5. BTK / ODMD / EPDK / TSB: monitor the announcement page, download the new PDF/Excel, extract with a parser,
   validate (shares sum to 100±0.5, consistency with the previous period), write to silver.
6. KAP: do NOT bulk-scrape kap.org.tr; use the MKK API Portal.
7. Google Trends: manual CSV import adapter only.

## Observation (silver) schema (domain/observations.py; Parquet schema in storage/observation_store.py)
source_id, dataset_id, series_id, entity_id, entity_name, metric, date (period end), freq, value, unit,
currency, is_proxy, vintage_ts, publication_lag_days. Key: source_id, series_id, entity_id, metric, freq, date.
`metric`: EVDS formula name (level, pct_change, yoy_pct, ...) + `_<agg>` if an aggregation was given.

## Transform rules
- Common calendar: monthly period end. An observation is written to the month of
  `period end + publication_lag_days` (no look-ahead).
- Daily→monthly: level=avg (or last), flow=sum; an incomplete last month is dropped. Quarterly/annual→monthly: ffill.
- Real = nominal / deflator (chain-linked from monthly CPI (TÜFE) % changes, reference month is a parameter).
- Market share: entity_value / sector_total; if proxy, is_proxy=True and a coverage ratio is added.
  The analysis is in `domain/services/market_share.py` (HHI range, trend |t|≥2 and ≥0.2 pp/year, substitution
  correlation, shift-share). Input schema: entity, date, value[, segment]; the market total is given in rows whose
  entity is `TOPLAM` (default; `TOTAL` is also accepted, case-insensitive).

## Acceptance criteria (MVP)
- 10 adapters (EVDS3 ✓, TÜİK, BDDK, TBB, EPİAŞ, EPDK, BTK, TSB, ODMD, BKM) pass the healthcheck.
- Market share series for 5 sectors (banking, insurance, telecom, automotive, fuel retail; sector IDs bankacilik,
  sigorta, telekom, otomotiv, akaryakit) in the gold panel.
- Every source YAML has license, auth, frequency, publication_lag and verification_status filled in (enforced by
  test_catalog.py).
- Secrets come from .env; no key ever appears in logs or URLs.

## Do not
- Integrate paid sources (Nielsen, Euromonitor, IQVIA are documentation-only).
- Scrape in violation of robots.txt / terms of use, or access areas that require login.
- Store fields containing personal data (KVKK).
- Put raw data in the repository; the repository contains only code and the catalog.

## Language standard
The repository uses a two-sided language split.

**Code side → English.** Identifiers, comments, docstrings, test names, code-level enum values (e.g. sector map
status `ready | pending_discovery | no_provider`, feature families `base`, `lag`, `rolling_mean`, `sector_recipe`),
dict/JSON keys, developer-facing log messages, YAML comments, and developer documents (CLAUDE.md, SECURITY.md).

**User-facing text → fluent, objective, professional Turkish.** Reports, error messages shown to users
(`DomainError` and provider errors), CLI help and output, MCP server instructions and tool descriptions, skill
documents (SKILL.md, references, evals), catalog text shown to users, and README.md.
- Write the way a Turkish data scientist writes: short, natural sentences, no translationese, no slang, metaphors,
  exclamations or casual phrasing.
- Technical terms always stay in English and are never Turkified; Turkish suffixes are attached with an apostrophe:
  "feature'lar", "backtest'te", "outlier'ları". Examples: not "bayat seri" but "stale seri"; not "aykırı değer
  tespiti" but "outlier tespiti"; not "sızıntı" but "leakage"; not "nokta-zamanlı" but "point-in-time".
- Plain domain words stay Turkish: pazar payı, oyuncu, sektör, enflasyon, kur, faiz, dönem, rapor, varsayım.
- Code-level enum values are shown to users through Turkish labels (e.g. `STATUS_LABELS` in
  `interfaces/presenters.py`).

**Domain names → Turkish.** Sector display names and sector IDs (bankacilik, sigorta, ...), source IDs, catalog
feature and recipe names (aofm, tufe_yoy, kredi_faizi, ...), sector event names, and the Turkish domain words in
generated panel columns (is_gunu, ramazan_bayrami_gun, ay_sin, olay_*, rejim_*). Technical suffixes of generated
columns are English (`__l3`, `__mean12`, `__std12`, `__chg3`, `__accel`, `__z24`, `__ewm6`, `__missing`,
`__outlier`). EVDS series codes and API field names (`Tarih`, `items`), MCP tool names and Turkish input column
aliases are kept unchanged.

**Market total marker:** market share tables mark total rows with `TOPLAM` (default); `TOTAL` is also accepted.

**Numbers** are given with period and unit, in Turkish formatting in Turkish text (%31,5; 2,4 puan); yüzde (%) and
yüzde puan (puan) are distinguished.

Examples: skills/market-share/references/report-guide.md.
