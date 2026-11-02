"""Google Trends for Türkiye from the BigQuery public dataset `bigquery-public-data.google_trends`.

Dataset facts (Marketplace listing google-trends-intl, Google Cloud blog 2022-03-31, public-datasets-pipelines;
checked 2026-11-03, not yet queried live):
- `international_top_terms` / `international_top_rising_terms`: term, country_name, country_code (ISO 3166 alpha-2),
  region_name, region_code (ISO 3166-2, e.g. TR-34), week (first day of the week), refresh_date (partition key),
  score (0–100, relative within one term's series in one region), rank (1–25, the term's rank in the country on
  that refresh_date); the rising table adds percent_gain.
- Every day a new partition holds the day's Top 25 and Top 25 Rising terms per country, each with a 5-year weekly
  backfill per region. Partitions expire after 30 days. Arbitrary terms cannot be queried: a term is visible only
  while it is in one of the retained daily lists.

So the provider offers two kinds of series (catalog `remote`: `<top|rising>,<count|interest>/<KEY>[+<KEY>...]`):
- count: per refresh_date, how many terms of the Türkiye list match one of the keywords (ASCII-folded word prefix,
  `_` = space). Only the last ~30 days exist upstream; every fetch adds the new days to the local observation
  store, where the history accumulates (catalog `history.revision_days` makes FetchSeries read the store back).
- interest: the weekly search interest of one term (the KEYs are alternative spellings, matched exactly after
  folding) from the latest retained list that contains it: the mean over regions is the total (empty entity_id),
  regions are entities. The 5-year backfill is rescaled by Google on every refresh (the maximum of the series is 100),
  so older weeks are revised: the provider always returns the whole backfill up to the requested end, keeping every
  stored week on the scale of one refresh; vintages keep the revisions.
Both are search-interest proxies (is_proxy=True), not market shares; scores of different terms are not comparable.

A code without a catalog seed is read from its name: `BQT.ILGI.<KEY>` = `top,interest/<key>` lets a user track any
term ad hoc (keyword sets for count series are catalog data).

Cost: queries are billed to the user's project (BQ_PROJECT_ID; the BigQuery sandbox needs no billing account, 1 TiB
of queries per month is free). Every query filters on the partition column and Türkiye, selects only the needed
columns and carries `maximumBytesBilled` (BQ_MAX_BYTES_BILLED, default 5 GiB): BigQuery refuses a larger scan
without charging. Credentials come from Application Default Credentials only (see google_auth.py).
"""

from __future__ import annotations

import os
import re
import time
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit

import pandas as pd

from mlbooster.application.dto import FetchRequest, metric_name
from mlbooster.application.errors import CredentialsMissing, ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Frequency, HealthResult, HealthStatus, SeedSeries, Source
from mlbooster.domain.observations import OBSERVATION_COLUMNS, empty_observations
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.bigquery_rest import API_HOST, PROJECT_ENV, SCOPE, BigQueryClient, Param
from mlbooster.infrastructure.providers.google_auth import GoogleTokenSource, TokenCache, credentials_path
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.storage.raw_store import RawStore

DATASET = "bigquery-public-data.google_trends"
TABLES = {"top": "international_top_terms", "rising": "international_top_rising_terms"}
COUNTRY = "TR"
MAX_BYTES_ENV = "BQ_MAX_BYTES_BILLED"
DEFAULT_MAX_BYTES = 5 * 1024**3
MIN_MAX_BYTES = 10 * 1024**2  # BigQuery bills at least 10 MiB per query and refuses a lower cap
MAX_MAX_BYTES = 100 * 1024**3
RETENTION_DAYS = 35  # partitions expire after 30 days; a small margin for late refreshes
MAX_KEYS = 20
_HEALTH_MAX_AGE_DAYS = 4

_REMOTE = re.compile(r"^(?P<table>top|rising),(?P<metric>count|interest)/(?P<keys>[a-z0-9_]{2,40}(\+[a-z0-9_]{2,40})*)$")
_ADHOC = re.compile(r"^BQT\.ILGI\.(?P<key>[A-Z0-9_]{2,40})$")
_TR_FOLD = str.maketrans("İIıŞşĞğÜüÖöÇçÂâÎîÛû", "IIiSsGgUuOoCcAaIiUu")
_NON_WORD = re.compile(r"[^a-z0-9]+")

# Sent as-is; only parameters vary. Every query prunes partitions with a constant refresh_date range or value.
_LIST_SQL = """
SELECT DISTINCT refresh_date, term, rank
FROM `{dataset}.{table}`
WHERE refresh_date BETWEEN @since AND @until
  AND country_code = @country
ORDER BY refresh_date, rank
"""
_INTEREST_SQL = """
SELECT week, region_code, region_name, score
FROM `{dataset}.{table}`
WHERE refresh_date = @refresh
  AND country_code = @country
  AND term IN UNNEST(@terms)
  AND week <= @end
"""


def fold(text: str) -> str:
    """Lower-case ASCII form with single spaces: 'Türk Telekom' → 'turk telekom'."""
    return " ".join(_NON_WORD.split(text.translate(_TR_FOLD).casefold())).strip()


def parse_remote(remote: str) -> tuple[str, str, tuple[str, ...]]:
    """(table key, metric, folded keys) of a catalog `remote` field."""
    m = _REMOTE.fullmatch(remote)
    if m is None:
        raise InvalidInput(
            "Google Trends remote alanı '<top|rising>,<count|interest>/<anahtar>[+<anahtar>...]' biçiminde olmalıdır "
            "(anahtarlar küçük ASCII harf, rakam ve '_')"
        )
    keys = tuple(dict.fromkeys(k.replace("_", " ") for k in m["keys"].split("+")))
    if len(keys) > MAX_KEYS:
        raise InvalidInput(f"Google Trends remote alanında en fazla {MAX_KEYS} anahtar olabilir")
    return m["table"], m["metric"], keys


def max_bytes_billed(value: str | None) -> int:
    if value is None or not value.strip():
        return DEFAULT_MAX_BYTES
    if not value.strip().isdigit() or not MIN_MAX_BYTES <= int(value) <= MAX_MAX_BYTES:
        raise InvalidInput(
            f"{MAX_BYTES_ENV} {MIN_MAX_BYTES} ile {MAX_MAX_BYTES} arasında bir bayt sayısı olmalıdır (ör. 5368709120 = 5 GiB)"
        )
    return int(value)


def _matches_keyword(term: str, keyword: str) -> bool:
    return re.search(rf"(^| ){re.escape(keyword)}", term) is not None


class BigQueryTrendsProvider:
    def __init__(
        self,
        source: Source,
        *,
        http: SecureHttpClient,
        secrets: EnvSecretProvider,
        raw_store: RawStore | None = None,
        tokens: TokenCache | None = None,
        today: date | None = None,
    ):
        parts = urlsplit(source.base_url)
        if parts.scheme != "https" or parts.hostname != API_HOST:
            raise ProviderError(f"{source.id} provider'ı yalnızca https://{API_HOST} ile çalışır")
        self.source = source
        self._http = http
        self._secrets = secrets
        self._tokens = GoogleTokenSource(http, SCOPE, tokens)
        self._client: BigQueryClient | None = None
        self._today = today
        self._lists: dict[tuple[str, date, date], pd.DataFrame] = {}

    # --- Setup ---------------------------------------------------------------------------

    def _bq(self) -> BigQueryClient:
        if self._client is None:
            configured = self._secrets.get(PROJECT_ENV)
            credentials = self._tokens.credentials  # CredentialsMissing without an ADC file
            project = configured.reveal() if configured else credentials.quota_project_id or credentials.project_id
            self._client = BigQueryClient(
                self._http, self._tokens, project or "", max_bytes_billed(os.environ.get(MAX_BYTES_ENV))
            )
        return self._client

    def _now(self) -> date:
        return self._today or datetime.now(UTC).date()

    # --- Catalog -------------------------------------------------------------------------

    def _seed(self, code: str) -> SeedSeries:
        seed = self.source.seed(code)
        if seed is not None and seed.remote:
            return seed
        if (m := _ADHOC.fullmatch(code)) is not None:
            return SeedSeries(
                code=code, name=f"Google Trends arama ilgisi: {m['key'].lower().replace('_', ' ')}", freq=Frequency.WEEKLY,
                unit="index_0_100", remote=f"top,interest/{m['key'].lower()}", publication_lag_days=2,
            )  # fmt: skip
        known = ", ".join(s.code for s in self.source.series)
        raise InvalidInput(
            f"{self.source.id} için bilinmeyen seri: {code}. Katalogdaki seriler: {known}. Herhangi bir terimi izlemek "
            "için BQT.ILGI.<TERIM> biçimini kullanın (ör. BQT.ILGI.TOGG; boşluk yerine '_')"
        )

    def list_series(self, query: str | None = None) -> pd.DataFrame:
        needle = fold(query or "")
        rows = [
            (s.code, s.name, s.freq.value, None, None, s.remote)
            for s in self.source.series
            if not needle or needle in fold(f"{s.code} {s.name}")
        ]
        return pd.DataFrame(rows, columns=["series_id", "name", "freq", "start", "end", "datagroup"])

    # --- Queries -------------------------------------------------------------------------

    def _list(self, table: str, since: date, until: date) -> pd.DataFrame:
        """The Türkiye list (refresh_date, term, folded, rank) of every retained refresh in [since, until]."""
        key = (table, since, until)
        if key not in self._lists:
            result = self._bq().query(
                _LIST_SQL.format(dataset=DATASET, table=TABLES[table]),
                [Param("since", "DATE", since), Param("until", "DATE", until), Param("country", "STRING", COUNTRY)],
            )
            if result.columns != ["refresh_date", "term", "rank"]:
                raise UnexpectedResponse("BigQuery Google Trends listesi beklenen kolonları döndürmedi")
            frame = pd.DataFrame(result.rows, columns=result.columns)
            try:
                frame["refresh_date"] = pd.to_datetime(frame["refresh_date"], format="%Y-%m-%d").dt.date
                frame["rank"] = pd.to_numeric(frame["rank"]).astype("Int64")
            except (ValueError, TypeError):
                raise UnexpectedResponse("BigQuery Google Trends listesinde tanınmayan değer var") from None
            frame = frame[frame["term"].notna()]
            frame["folded"] = frame["term"].astype(str).map(fold)
            self._lists[key] = frame
        return self._lists[key]

    def _window(self, request: FetchRequest) -> tuple[date, date] | None:
        today = self._now()
        since, until = max(request.period.start, today - timedelta(days=RETENTION_DAYS)), min(request.period.end, today)
        return (since, until) if since <= until else None

    def _count(self, seed: SeedSeries, table: str, keys: tuple[str, ...], request: FetchRequest) -> pd.DataFrame:
        window = self._window(request)
        if window is None:  # upstream keeps only ~30 days; older days live in the local store only
            return pd.DataFrame(columns=["series_id", "entity_id", "entity_name", "date", "value"])
        listing = self._list(table, *window)
        if listing.empty:
            return pd.DataFrame(columns=["series_id", "entity_id", "entity_name", "date", "value"])
        hits = listing["folded"].map(lambda t: any(_matches_keyword(t, k) for k in keys))
        days = sorted(set(listing["refresh_date"]))
        counts = listing[hits].groupby("refresh_date")["folded"].nunique()
        rows = [(seed.code, "", None, d, float(counts.get(d, 0))) for d in days]
        return pd.DataFrame(rows, columns=["series_id", "entity_id", "entity_name", "date", "value"])

    def _interest(self, seed: SeedSeries, table: str, keys: tuple[str, ...], request: FetchRequest) -> pd.DataFrame:
        columns = ["series_id", "entity_id", "entity_name", "date", "value"]
        today = self._now()
        listing = self._list(table, today - timedelta(days=RETENTION_DAYS), today)
        found = listing[listing["folded"].isin(keys)]
        if found.empty:  # the term is not in any retained list: nothing new upstream
            return pd.DataFrame(columns=columns)
        latest = max(found["refresh_date"])
        terms = sorted(set(found.loc[found["refresh_date"] == latest, "term"]))
        result = self._bq().query(
            _INTEREST_SQL.format(dataset=DATASET, table=TABLES[table]),
            [
                Param("refresh", "DATE", latest), Param("country", "STRING", COUNTRY), Param("terms", "STRING", terms),
                Param("end", "DATE", request.period.end),
            ],
        )  # fmt: skip
        if result.columns != ["week", "region_code", "region_name", "score"]:
            raise UnexpectedResponse("BigQuery Google Trends ilgi sorgusu beklenen kolonları döndürmedi")
        frame = pd.DataFrame(result.rows, columns=result.columns)
        frame = frame[frame["score"].notna()]
        if frame.empty:
            return pd.DataFrame(columns=columns)
        try:
            week_start = pd.to_datetime(frame["week"], format="%Y-%m-%d")
            frame["score"] = pd.to_numeric(frame["score"]).astype(float)
        except (ValueError, TypeError):
            raise UnexpectedResponse("BigQuery Google Trends ilgi sorgusunda tanınmayan değer var") from None
        if not frame["score"].between(0, 100).all():
            raise UnexpectedResponse("Google Trends skoru 0–100 aralığının dışında")
        frame["date"] = (week_start + pd.Timedelta(days=6)).dt.date  # observation date = end of the week
        frame["region_code"] = frame["region_code"].fillna("").astype(str)
        # Several spellings of the same term in one list: the mean per region and week.
        by_region = frame.groupby(["date", "region_code"], as_index=False).agg(
            value=("score", "mean"), region_name=("region_name", "first")
        )
        national = by_region[by_region["region_code"].isin(["", COUNTRY])]
        regions = by_region[~by_region["region_code"].isin(["", COUNTRY])]
        # A country-level row (if the table has one) is the total; otherwise the mean over regions.
        total = national.groupby("date")["value"].mean() if not national.empty else regions.groupby("date")["value"].mean()
        rows = [(seed.code, "", None, d, float(v)) for d, v in total.items()]
        rows += [(seed.code, r.region_code, r.region_name, r.date, float(r.value)) for r in regions.itertuples()]
        out = pd.DataFrame(rows, columns=columns)
        # The whole backfill is returned even if the request starts later: Google rescales the series on every
        # refresh, so all weeks in the store must come from the same refresh (one vintage, one scale).
        return out[out["date"] <= request.period.end]

    # --- Data ----------------------------------------------------------------------------

    def fetch(self, request: FetchRequest) -> pd.DataFrame:
        if request.formula or request.agg or request.freq is not None:
            raise InvalidInput(
                f"{self.source.id}: formül, agregasyon ve frekans dönüşümü desteklenmiyor; dönüşümü yerelde yapın"
            )
        seeds = [(seed, parse_remote(seed.remote or "")) for seed in map(self._seed, request.series_ids)]
        self._bq()  # fail before any request when credentials or the project are missing
        vintage, frames = datetime.now(UTC), []
        for seed, (table, metric, keys) in seeds:
            run = self._count if metric == "count" else self._interest
            frames.append(run(seed, table, keys, request))
        frame = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(not f.empty for f in frames) else None
        if frame is None:
            return empty_observations()
        by_code = {seed.code: (seed, remote) for seed, remote in seeds}
        frame["source_id"] = self.source.id
        frame["dataset_id"] = frame["series_id"].map(lambda c: TABLES[by_code[c][1][0]])
        frame["metric"] = metric_name(0)
        frame["freq"] = frame["series_id"].map(
            lambda c: (Frequency.DAILY if by_code[c][1][1] == "count" else Frequency.WEEKLY).value
        )
        frame["unit"] = frame["series_id"].map(lambda c: by_code[c][0].unit)
        frame["currency"] = None
        frame["is_proxy"] = True  # search interest, not a market measure
        frame["vintage_ts"] = vintage
        frame["publication_lag_days"] = frame["series_id"].map(
            lambda c: by_code[c][0].publication_lag_days
            if by_code[c][0].publication_lag_days is not None
            else self.source.publication_lag_days
        )
        return frame.sort_values(["series_id", "entity_id", "date"], ignore_index=True)[OBSERVATION_COLUMNS]

    def healthcheck(self) -> HealthResult:
        if not self.source.canaries:
            return HealthResult(self.source.id, HealthStatus.SKIPPED, "katalogda canary seri yok")
        if credentials_path() is None:
            return HealthResult(self.source.id, HealthStatus.SKIPPED, "Google kimlik bilgisi (ADC) tanımlı değil")
        today, details, started = self._now(), [], time.monotonic()
        try:
            for seed in self.source.canaries:
                df = self.fetch(FetchRequest.create(self.source.id, [seed.code], today - timedelta(days=10), today))
                total = df[df["entity_id"] == ""]
                if total.empty:
                    return HealthResult(self.source.id, HealthStatus.FAIL, f"{seed.code}: son günlerde liste yok")
                last = max(total["date"])
                if (today - last).days > _HEALTH_MAX_AGE_DAYS:
                    return HealthResult(self.source.id, HealthStatus.FAIL, f"{seed.code}: son liste {last}")
                details.append(f"{seed.code} son={last} değer={total.sort_values('date')['value'].iloc[-1]:g}")
        except CredentialsMissing as exc:
            return HealthResult(self.source.id, HealthStatus.SKIPPED, str(exc))
        except ProviderError as exc:
            return HealthResult(self.source.id, HealthStatus.FAIL, f"{type(exc).__name__}: {exc}")
        except InvalidInput as exc:
            return HealthResult(self.source.id, HealthStatus.FAIL, f"{type(exc).__name__}: {exc}")
        return HealthResult(
            self.source.id, HealthStatus.OK, "; ".join(details), (time.monotonic() - started) * 1000,
            tuple(s.code for s in self.source.canaries),
        )  # fmt: skip

    def close(self) -> None:
        self._http.close()

