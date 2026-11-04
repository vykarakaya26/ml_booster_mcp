# ML Booster Map — Project Specification

## Purpose
The user selects a sector in Turkey; the system produces for that sector (a) a map of free data sources,
(b) macro/sector features, and (c) market share series (direct or proxy), and exports them as an ML-ready
monthly panel (Parquet). Distribution: GitHub repository, `pip install git+...`.
Usage is through the CLI (`mlbooster`), the MCP server (`mlbooster-mcp`, stdio) and a local web panel
(`mlbooster web`, bound to 127.0.0.1 only; a hosted/public site is out of scope).
The product ends at ML-ready features: forecasting, model training and model comparison are out of scope.

## Technology
- Python 3.12+, httpx, pandas, pydantic v2, duckdb ≥2, pyarrow, typer, python-dotenv
- MCP: `mcp` 2.x — `from mcp.server.mcpserver import MCPServer` (FastMCP from 1.x does not exist in 2.x)
- PDF (later phases): pdfplumber + camelot (fallback); Excel: openpyxl
- Tests: pytest + respx (mocked HTTP) + tests/fakes.py (port fakes); live contract tests with `-m live`
- CI: GitHub Actions (pytest 3.12/3.13 with coverage ≥ 85%, ruff check, pip-audit), actions pinned to SHAs, Dependabot
- Lint: `ruff check .` (rules in pyproject.toml; skills/ excluded from pyupgrade because scripts run on system Python ≥ 3.10)

## Architecture: Onion — dependencies only from the outside in (enforced by tests/test_architecture.py)
```
src/mlbooster/
  domain/            # core: model.py (frozen dataclasses), validation.py (allow-lists, DateRange), periods.py
                     # (single period-string parser), numfmt.py (Turkish number formatting for messages),
                     # observations.py (observation frame contract), services/ (frequency, deflation, features,
                     # chunking, sector_map, quality, distribution, preprocessing, feature_engineering,
                     # calendar_tr, market_share). Allowed: stdlib + pandas/numpy. NO I/O.
  application/       # ports.py (Protocols), dto.py (FetchRequest: input validation gate), errors.py,
                     # use_cases/ (catalog, series, discovery, panel, health, analysis, modeling, market_share,
                     # files: ExportTable). Depends only on domain.
  infrastructure/    # port implementations: providers/ (evds3, sdmx, tuik, bddk, tbb, bkm, tsb, odmd, epdk, btk, epias,
                     # bigquery_trends + google_auth/bigquery_rest, registry), http_client.py (SecureHttpClient),
                     # catalog_yaml.py (pydantic → domain), secret_store.py, settings.py, storage/,
                     # update_check.py (GitHub tags), table_reader.py (user CSV/JSON input; CLI only)
  interfaces/        # cli.py (typer), mcp_server.py (MCPServer), presenters.py, web/ (Starlette JSON API + static
                     # JS/CSS, no build step; labels.py derives readable feature names) — call use cases only
                     # (tests/test_architecture.py forbids container().<port> access)
  bootstrap.py       # composition root: the single wiring point
  catalog_data/      # sources/*.yaml, sectors/*.yaml (package-data); user override: ~/.mlbooster/catalog/overrides.yaml
skills/              # Claude Code plugin skills (sector-analysis, data-quality-audit, data-prep, feature-engineering,
                     # evds-series-discovery, market-share); _shared/ = stdlib-only report library
                     # + bin/mlb CLI wrapper
.claude-plugin/      # plugin.json (mcpServers: uvx --from ${CLAUDE_PLUGIN_ROOT}) + marketplace.json; there is NO
                     # .mcp.json at the root — if there were, sessions opened in the repo folder would treat it as a
                     # project MCP server and run it without ${CLAUDE_PLUGIN_ROOT}
tests/               # domain/, application/ (with fakes.py), infrastructure/ (respx), interfaces/, skills/,
                     # test_architecture.py (layering), test_drift.py (docs/packaging consistency)
```
Rules:
- Do NOT import httpx, duckdb, pyarrow, yaml, pydantic, mcp, typer or dotenv in domain/application.
- Interfaces do NOT import infrastructure directly; they use `bootstrap.build_container()`.
- Every identifier/code coming from the outside world is validated with `domain.validation` (FetchRequest.create,
  use case entry points).
- Analysis logic (quality audit, market share) lives in the package and is tested; skill scripts only handle
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
- Web panel: binds to 127.0.0.1 only; non-loopback Host → 400; every `/api` call needs the per-launch
  `X-MLB-Token` header and a same-origin `Origin`; CSP without `unsafe-inline` (styles are set through
  `el.style`, never a `style` attribute); the DOM is filled with `textContent` only; no external CDN or scripts;
  the API takes and returns no file paths.

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
2. TÜİK (done — infrastructure/providers/tuik.py): strategy chain veriportali JSON → istab Excel → MEDAS (optional).
   Do not depend on nsiws SDMX. Live observation 2026-11-02:
   - veriportali.tuik.gov.tr/api/* answers non-browser clients with 403 "Erişim engellendi"; do not work around it.
   - The portal's tables open the data browser at databrowser2.tuik.gov.tr (pinned host). Its public JSON API:
     GET `/api/core/nodes/1/datasets/<agency,dataflow,version>/structure` (dimension order, `timeDimension`) and
     POST `.../data` with a criteria list (`CodeValues` per dimension + `TimeRange` from/to) → JSON-stat 2.0.
     Empty period → `id: []`; unknown dataflow → HTTP 500 `DATAFLOW_NOT_FOUND`.
   - Legacy istab Excel links redirect to the portal SPA (HTML); MEDAS is a stateful ZK UI; nsiws → 401.
   - Catalog `remote`: `<agency,dataflow,version>/<one code per non-time dimension, in structure order>`.
   CPI (TÜFE) base 2025=100; the deflator is chain-linked from monthly % changes.
3. EPİAŞ (done — infrastructure/providers/epias.py): POST https://giris.epias.com.tr/cas/v1/tickets
   (form-urlencoded username, password) → 201 `TGT-...`; documented TTL 2 hours (cached in memory only, renewed
   10 min early and once after a 401/406). Data: POST https://seffaflik.epias.com.tr/electricity-service/v1/... with
   JSON {startDate, endDate} (+03:00) and the `TGT` header. No TGT → 401 AUTH002 (generation: 406 text), bad TGT →
   401 AUTH009. Gateway limits per service (60/60 s for PTF, 3600/h others) → rps 1; 429 → wait 30 s. Hourly → daily
   by kind (price mean, flow sum). Credentials: EPIAS_USERNAME / EPIAS_PASSWORD in .env; healthcheck is skipped
   without them. Verified live 2026-11-03: range per call is 1 year (PTF, SMF, consumption) or 3 months (generation),
   end ≤ start + N calendar months, else 400 `(BUS)SEF1117`; requests are cut at calendar month ends. The end day is
   inclusive (T00:00 returns all its hours) and responses are not paged. Dates carry the Europe/Istanbul offset of
   that day (+02:00 in winters before Sep 2016; +03:00 there returns the previous day). Generation `total` = sources
   + `importExport`; domestic generation (`generation`, the `by_source` total) is the sum of the sources.
   History: catalog `history: {default_years: 5, revision_days: 7}` (opt-in per source; only EPİAŞ): `fetch` without
   a start goes back 5 years; later fetches request only history before the earliest earlier request plus the tail
   from (last cached day − revision_days); `--refresh` / `refresh=true` refetches everything.
4. BDDK / TBB / BKM: HTML tables + Excel downloads; change detection by file hash.
   BDDK (done — infrastructure/providers/bddk.py), live observation 2026-11-02: no robots.txt (404); the terms of use
   allow quotation with attribution, commercial use needs BDDK's written permission.
   - The Aylık Bülten table is filled from POST `www.bddk.org.tr/BultenAylik/tr/Home/BasitRaporGetir` (pinned host;
     form-urlencoded `tabloNo`, `yil`, `ay`, `paraBirimi`, repeated `taraf`) → JSON `{success, Json: {colModels,
     data.rows[].cell}}`; one month per call, all requested bank groups at once, sorted by group code. The Excel
     download (BasitExceleAktar) carries the same values. Raw responses go to bronze (content hash).
   - Unpublished months → `success: false` with a message naming the available months (parsed to skip requests).
   TBB (done — infrastructure/providers/tbb.py), live observation 2026-11-02: no robots.txt; terms allow republishing
   with attribution, commercial use needs TBB's written permission. The Mali Tablolar page of verisistemi.tbb.org.tr
   reads POST `/api/router` (JSON `route` body, `LANG` header, no login): `donemler`, `bankalar`, `bankaGruplari`,
   `degerler` (TL). The server omits its intermediate certificate; it is shipped in infrastructure/certs (never a
   trust anchor). Catalog `remote`: `5/<item key>[+<older key>...][.<column>]` (TFRS9-SOLO key first, then SOLO); the
   system total gets an empty `entity_id`, banks are entities (entity_id = TBB bank code). Closed banks are not
   listed; sub-group memberships are incomplete, so only group 5 is used.
   - Row labels change over time; rows are matched by `row_key()` (ASCII upper case, no stars or parentheses).
   - Catalog `remote`: `<table>[,<total group>+<group>...]/<ROW_KEY>[.<column>]`; the total gets an empty
     `entity_id`, the groups are entities (entity_id = group code). Panels use the total only; `market-share
     --source bddk --series BDDK.AKTIF` turns the groups into shares (total → `TOPLAM`). The sector's
     `market_share_sources[].series` lists the series offered for this (web panel "Kaynaktan çek" mode).
   BKM (done — infrastructure/providers/bkm.py), live observation 2026-11-02: robots.txt blocks only /wp-admin/. The
   reports require attribution; the site legal notice reserves copying, redistribution, storage for later use and
   commercial use to BKM's written permission, so the catalog sets `storage: {raw: false, observations: false}`
   (honoured by `ProviderRegistry` and `FetchSeries`: nothing goes to bronze or silver, panels refetch BKM on every
   build) and the test fixtures carry synthetic numbers. Each "Dönemsel Bilgiler" report is a server-rendered HTML
   page (pinned host bkm.com.tr) with one table per year since 2017; the `?xls=1` Excel is the same HTML. Merchant
   groups via `?filter_group=<n>`. No bank-level data. Catalog `remote`: `<report slug>[,<filter_group>]/<COLUMN>
   [+<COLUMN>...]`, COLUMN = header keys top to bottom joined with '.' (e.g. `ISLEM_TUTARI_MILYON_TL.TOPLAM`); '+'
   sums columns. Lag 40 days.
5. BTK / ODMD / EPDK / TSB: monitor the announcement page, download the new PDF/Excel, extract with a parser,
   validate (shares sum to 100±0.5, consistency with the previous period), write to silver.
   ODMD (done — infrastructure/providers/odmd.py), live observation 2026-11-02: no robots.txt (404), no terms page
   (footer "Tüm Hakları Saklıdır"); catalog records attribution required, no redistribution, no commercial use. Brand
   table = "Pazar - Perakende Satışlar" list (`neuralnetwork.aspx?type=36`); the download
   `wf_docudownload.aspx?primary_id=<id>&type=36&...` needs the anonymous ASP.NET session cookie of the list page and
   answers 302 to `/folders/2837/categorial1docs/<id>/<file>` (pinned host, Location checked by regex). xlsx from
   2015-01 (2017-02 is xls → skipped; 2014 xls and 2011–2013 PDF not integrated). Catalog `remote`:
   `perakende/<OTOMOBIL|HAFIF_TICARI|TOPLAM>.<TOPLAM|YERLI|ITHAL>`; market total = empty entity_id, brands are entities.
   TSB (done — infrastructure/providers/tsb.py), live observation 2026-11-02: no robots.txt (404), no open licence (all
   rights reserved). Listing: GET `/Statistic/GetStatistics` (JSON, per year/month); monthly "Prim Üretimleri
   Sıralama" xlsx under /content/Statistics/ (pinned host), cumulative YTD TL per branch sheet, company code =
   entity_id, SEKTÖR TOPLAMI = total. `/Statistic/GetStatisticReportStatus` switches downloads to an e-mail/OTP form;
   if it is on, the provider stops (never bypassed). Catalog `remote`: `PRIM_URETIMLERI_SIRALAMA/<SHEET>[.YTD]`;
   without .YTD the monthly premium is the YTD difference. Shares: use the .YTD series.
   EPDK (done — infrastructure/providers/epdk.py + epdk_reports.py), live observation 2026-11-02: no robots.txt (302 to
   home), no terms-of-use page. Monthly report lists (/Detay/Icerik/3-0-104 petrol, /3-0-105 LPG) are static HTML;
   files at /Detay/DownloadDocument?id=<id> (pinned host). 2016–2025 Word .docx (petrol table 3.2, LPG table 3.5),
   2026+ PDF + Excel annex (table 12, long format); .doc/PDF before 2016 are not read. Catalog `remote`:
   `petrol|lpg/<COLUMN>`; the total gets an empty `entity_id`, holders are keyed by the first two words of their title
   (renames in `epdk.ALIASES`). Checks: holders sum to the total ±0.5 %, published shares 100±0.5, month-on-month total
   < 3×. Office files: size-capped before decompression, XML with a DTD refused.
   BTK (done — infrastructure/providers/btk.py), live observation 2026-11-02: robots.txt allows all but /api/ and
   /_next/; no terms page (footer "Tüm Hakları Saklıdır": attribution, no redistribution, no commercial use). Report
   list /elektronik-haberlesme-pazar-verileri (server-rendered HTML, one PDF per quarter, 2009-Q1 →; 2018-Q2/Q4
   missing); PDFs on the same host under /s3/, 40–46 MB (`max_response_mb: 150` for this source). Only the text layer
   is read through PDFium (signature check, nothing rendered): Çizelge 1-3 (operator revenue), 3-1, 3-2 (fixed
   broadband) and the mobile share sentence (charts are images, so mobile subscriber counts by operator are not
   available). Tables must add up (shares 100±0.5, operators = printed total) or they are rejected (2025-Q4 fixed
   broadband, 2021-Q1 unreadable text layer: known_breaks). Lag 99 days. Catalog `remote`: `<table>/<column>`.
6. KAP: do NOT bulk-scrape kap.org.tr (its copyright notice forbids storing content without MKK's written consent).
   Research 2026-11-03: the only API is MKK's "KAP Veri Yayın Servisleri" (apiportal.mkk.com.tr; disclosures,
   disclosureDetail with XBRL flatData, members); access needs a Borsa İstanbul data distribution agreement (fee
   tariff), MKK IP whitelisting and an e-mailed API key, so it is a paid source and not integrated. Listed-company
   revenues enter only through user-supplied tables (market-share input).
7. Google Trends (done — infrastructure/providers/bigquery_trends.py, adapter bq_trends): BigQuery public dataset
   bigquery-public-data.google_trends (international_top_terms / international_top_rising_terms, country_code 'TR')
   via jobs.query on bigquery.googleapis.com (pinned; tokens from oauth2.googleapis.com, ADC only: authorized_user or
   service_account, no google-auth). Billing project BQ_PROJECT_ID; every query filters refresh_date (partition) and
   country, selects named columns and carries maximumBytesBilled (BQ_MAX_BYTES_BILLED, default 5 GiB). Upstream keeps
   30 daily partitions of Top 25 / Top 25 Rising terms (5-year weekly backfill per region); arbitrary terms cannot be
   queried. Catalog `remote`: `<top|rising>,<count|interest>/<KEY>[+<KEY>...]`; count = daily keyword hits in the list
   (history accumulates in silver), interest = weekly score of a listed term (total = mean over regions, regions are
   entities); ad hoc `BQT.ILGI.<TERM>`. All values is_proxy=True (search interest). No CSV import, no pytrends/SerpAPI.
   Live 2026-11-03 (verified): tables are not clustered (~0.25 GiB billed per listed day) → lists are read in 7-day
   chunks; each term has 261 weeks × 81 provinces, no country-level row, null = low volume (0 in the total);
   partitions appear 2 days late (publication_lag_days 2, history revision_days 3).

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
- 10 adapters (EVDS3 ✓, TÜİK ✓, BDDK ✓, TBB ✓, EPİAŞ ✓ code / healthcheck needs the user's account, EPDK ✓, BTK ✓, TSB ✓,
  ODMD ✓, BKM ✓) pass the healthcheck. Extra: Google Trends via BigQuery (bq_trends ✓; needs the user's GCP
  project and ADC).
- Market share series for 5 sectors (banking, insurance, telecom, automotive, fuel retail; sector IDs bankacilik,
  sigorta, telekom, otomotiv, akaryakit) in the gold panel.
- Every source YAML has license, auth, frequency, publication_lag and verification_status filled in (enforced by
  test_catalog.py).
- Secrets come from .env; no key ever appears in logs or URLs.

## Do not
- Integrate paid sources (Nielsen, Euromonitor, IQVIA are documentation-only).
- Add forecasting, backtests or model comparison; the product stops at ML-ready features.
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
  "feature'lar", "outlier'ları". Examples: not "bayat seri" but "stale seri"; not "aykırı değer
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
