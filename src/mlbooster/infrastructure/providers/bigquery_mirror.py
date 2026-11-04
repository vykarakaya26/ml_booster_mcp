"""Google Trends lists and backfills read from the local mirror, which BigQuery fills one day at a time.

Used only after the user said yes (`TrendsMirror.choice() == 'enabled'`). Every missing retained day is read once: its
list (`_LIST_SQL`) and the backfill of its terms (`_MIRROR_SQL`), in runs of at most 7 days under the query cap. Only
Türkiye rows and the columns the package uses are kept. Without Google credentials the existing copy still answers.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from mlbooster.application.errors import ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Source
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.bigquery_rest import Param
from mlbooster.infrastructure.providers.bigquery_terms import ROW_COLUMNS, BigQueryTermLookup, open_term_lookup
from mlbooster.infrastructure.providers.bigquery_trends import (
    _LIST_SQL,
    COUNTRY,
    DATASET,
    RETENTION_DAYS,
    TABLES,
)
from mlbooster.infrastructure.providers.google_auth import credentials_path
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.storage.trends_mirror import TrendsMirror

SYNC_CHUNK_DAYS = 7  # live 2026-11-04: ~1.75 GiB for lists and ~2.7 GiB for backfills per query, under the default cap
SETTLED_DAYS = 3  # a day older than this without a Türkiye list is recorded as empty (new days come ~2 days late)

# Newest refresh of every term listed in the run; one scan serves the national rows (mean over all provinces, null = 0)
# and the province rows with a published score.
_MIRROR_SQL = """
SELECT term, refresh_date, week, region_code, region_name, AVG(IFNULL(raw_score, 0)) AS score
FROM (
  SELECT term, refresh_date, week, region_code, region_name, score AS raw_score
  FROM `{dataset}.{table}`
  WHERE refresh_date BETWEEN @since AND @until
    AND country_code = @country
  QUALIFY refresh_date = MAX(refresh_date) OVER (PARTITION BY term)
)
GROUP BY GROUPING SETS ((term, refresh_date, week), (term, refresh_date, week, region_code, region_name))
HAVING GROUPING(region_code) = 1 OR COUNTIF(raw_score IS NOT NULL) > 0
"""


def _runs(days: list[date], limit: int = SYNC_CHUNK_DAYS) -> list[tuple[date, date]]:
    """Consecutive days grouped into (start, end) ranges of at most `limit` days."""
    out: list[tuple[date, date]] = []
    for day in sorted(days):
        if out and day == out[-1][1] + timedelta(days=1) and (day - out[-1][0]).days < limit:
            out[-1] = (out[-1][0], day)
        else:
            out.append((day, day))
    return out


class BigQueryMirror(BigQueryTermLookup):
    """TrendsLookup on the local copy plus the download of the days it misses."""

    def __init__(self, *args, mirror: TrendsMirror, **kwargs):
        super().__init__(*args, **kwargs)
        self._mirror = mirror
        self._sync_note: str | None = None

    def missing_days(self, table: str) -> list[date]:
        today, have = self._now(), self._mirror.days(table)
        return [d for i in range(RETENTION_DAYS + 1) if (d := today - timedelta(days=RETENTION_DAYS - i)) not in have]

    def _params(self, since: date, until: date) -> list[Param]:
        return [Param("since", "DATE", since), Param("until", "DATE", until), Param("country", "STRING", COUNTRY)]

    def estimate(self) -> dict:
        """What a download of the missing days would read, from free dry runs."""
        total, days = 0, 0
        for table, name in TABLES.items():
            for start, end in _runs(self.missing_days(table), RETENTION_DAYS + 1):  # a dry run bills nothing: no chunks needed
                for sql in (_LIST_SQL, _MIRROR_SQL):
                    total += self._bq().query(sql.format(dataset=DATASET, table=name), self._params(start, end), dry_run=True).bytes_processed
            days = max(days, len(self.missing_days(table)))
        return {"bytes": total, "days": days}

    def sync(self) -> dict:
        """Downloads the missing retained days; returns what it read."""
        before_bytes, before_queries = self.bytes_billed, self.queries
        added: set[date] = set()
        for table in sorted(TABLES):
            for start, end in _runs(self.missing_days(table)):
                added |= self._sync_run(table, start, end)
        return {"days_added": len(added), "bytes_billed": self.bytes_billed - before_bytes, "queries": self.queries - before_queries}

    def _sync_run(self, table: str, start: date, end: date) -> set[date]:
        lists = self._read_lists(table, start, end)
        if not lists.empty:  # backfills first: a list file means "day synced"
            self._mirror.write_backfill(table, self._read_backfill(table, start, end))
        added = set()
        settled = self._now() - timedelta(days=SETTLED_DAYS)
        for i in range((end - start).days + 1):
            day = start + timedelta(days=i)
            rows = lists[lists["refresh_date"] == day]
            if not rows.empty or day <= settled:
                self._mirror.write_list(table, day, rows)
            if not rows.empty:
                added.add(day)
        return added

    def _read_lists(self, table: str, since: date, until: date) -> pd.DataFrame:
        result = self._bq().query(_LIST_SQL.format(dataset=DATASET, table=TABLES[table]), self._params(since, until))
        if result.columns != ["refresh_date", "term", "rank"]:
            raise UnexpectedResponse("BigQuery Google Trends listesi beklenen kolonları döndürmedi")
        frame = pd.DataFrame(result.rows, columns=result.columns)
        try:
            frame["refresh_date"] = pd.to_datetime(frame["refresh_date"], format="%Y-%m-%d").dt.date
            frame["rank"] = pd.to_numeric(frame["rank"]).astype("int32")
        except (ValueError, TypeError):
            raise UnexpectedResponse("BigQuery Google Trends listesinde tanınmayan değer var") from None
        return frame[frame["term"].notna()]

    def _read_backfill(self, table: str, since: date, until: date) -> pd.DataFrame:
        result = self._bq().query(_MIRROR_SQL.format(dataset=DATASET, table=TABLES[table]), self._params(since, until))
        if result.columns != ROW_COLUMNS:
            raise UnexpectedResponse("BigQuery Google Trends terim sorgusu beklenen kolonları döndürmedi")
        frame = pd.DataFrame(result.rows, columns=ROW_COLUMNS)
        try:
            frame["refresh_date"] = pd.to_datetime(frame["refresh_date"], format="%Y-%m-%d").dt.date
            frame["week"] = pd.to_datetime(frame["week"], format="%Y-%m-%d").dt.date
            frame["score"] = pd.to_numeric(frame["score"]).astype(float)
        except (ValueError, TypeError):
            raise UnexpectedResponse("BigQuery Google Trends terim sorgusunda tanınmayan değer var") from None
        if not frame["score"].between(0, 100).all():
            raise UnexpectedResponse("Google Trends skoru 0–100 aralığının dışında")
        frame["region_code"] = frame["region_code"].fillna("").astype(str)
        return frame[frame["term"].notna()]

    # --- TrendsLookup ---------------------------------------------------------------------

    def listed_terms(self) -> pd.DataFrame:
        """Every list in the copy, after fetching the days it misses. Without a working Google setup the copy answers."""
        self._sync_note = None
        if credentials_path() is None:
            self._sync_note = "Google kimlik bilgisi tanımlı değil; yeni günler alınmadı."
        else:
            try:
                self.sync()
            except (ProviderError, InvalidInput) as exc:
                self._sync_note = f"Yeni günler alınamadı: {exc}"
        frames = [self._mirror.lists(t, date.min, date.max).assign(table=t) for t in sorted(TABLES)]
        return pd.concat(frames, ignore_index=True)[["table", "refresh_date", "term", "rank"]]

    def interest(self, table: str, terms: dict[str, date]) -> pd.DataFrame:
        """Rows of the terms from the copy, always the newest refresh the copy holds for a term."""
        return self._mirror.backfill(table, sorted(terms))

    def data_source(self) -> dict:
        label = "Yerel kopya; yalnızca eksik günler BigQuery'den alındı."
        return {"mode": "local_copy", "label": f"{label} {self._sync_note}" if self._sync_note else label}


class TermLookups:
    """The lookup a search-interest run should use, decided on every run from the user's answer."""

    def __init__(self, load_source, mirror: TrendsMirror):
        self._load_source = load_source
        self._mirror = mirror
        self._direct: BigQueryTermLookup | None = None
        self._local: BigQueryMirror | None = None

    def current(self) -> BigQueryTermLookup:
        return self.local() if self._mirror.choice() == "enabled" else self.direct()

    def direct(self) -> BigQueryTermLookup:
        if self._direct is None:
            self._direct = open_term_lookup(self._load_source())
        return self._direct

    def local(self) -> BigQueryMirror:
        if self._local is None:
            source: Source = self._load_source()
            http = SecureHttpClient(rps=source.rate_limit.rps, max_bytes=source.max_response_mb * 1024 * 1024)
            self._local = BigQueryMirror(source, http=http, secrets=EnvSecretProvider(), mirror=self._mirror)
        return self._local
