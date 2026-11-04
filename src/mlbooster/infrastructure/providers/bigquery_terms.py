"""Term lookups of the search-interest layer on the Google Trends BigQuery tables.

Lists come from the provider's 7-day chunks and its in-process cache. Interest is asked for all terms of a table at
once: `refresh_date IN UNNEST(@days)` prunes partitions (live 2026-11-04: 0.38 GiB per day), each query covers at most
7 days and carries the cap of the BigQuery client. Every query is metered, so a run can report what it billed.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

import pandas as pd

from mlbooster.application.errors import UnexpectedResponse
from mlbooster.infrastructure.providers.bigquery_rest import BigQueryClient, Param, QueryResult
from mlbooster.infrastructure.providers.bigquery_trends import (
    COUNTRY,
    DATASET,
    RETENTION_DAYS,
    TABLES,
    BigQueryTrendsProvider,
)

MAX_DAYS_PER_QUERY = 7
ROW_COLUMNS = ["term", "refresh_date", "week", "region_code", "region_name", "score"]

# The national row of a week is the mean over all provinces with null = 0 (too little volume); province rows keep the
# published scores only. One scan serves both.
_TERMS_SQL = """
SELECT term, refresh_date, week, region_code, region_name, AVG(IFNULL(raw_score, 0)) AS score
FROM (
  SELECT term, refresh_date, week, region_code, region_name, score AS raw_score
  FROM `{dataset}.{table}`
  WHERE refresh_date IN UNNEST(@days)
    AND country_code = @country
    AND term IN UNNEST(@terms)
  QUALIFY refresh_date = MAX(refresh_date) OVER (PARTITION BY term)
)
GROUP BY GROUPING SETS ((term, refresh_date, week), (term, refresh_date, week, region_code, region_name))
HAVING GROUPING(region_code) = 1 OR COUNTIF(raw_score IS NOT NULL) > 0
"""


class _Metered:
    def __init__(self, client: BigQueryClient, owner: BigQueryTermLookup):
        self._client = client
        self._owner = owner

    def query(self, sql: str, params: Sequence[Param], *, dry_run: bool = False) -> QueryResult:
        result = self._client.query(sql, params, dry_run=dry_run)
        self._owner.bytes_billed += result.bytes_billed
        self._owner.queries += 1
        return result


class BigQueryTermLookup(BigQueryTrendsProvider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bytes_billed = 0
        self.queries = 0

    def _bq(self) -> _Metered:
        return _Metered(super()._bq(), self)

    def listed_terms(self) -> pd.DataFrame:
        """DataFrame[table, refresh_date, term, rank]: every retained Türkiye list."""
        today = self._now()
        frames = [
            self._list(table, today - timedelta(days=RETENTION_DAYS), today).assign(table=table) for table in sorted(TABLES)
        ]
        return pd.concat(frames, ignore_index=True)[["table", "refresh_date", "term", "rank"]]

    def interest(self, table: str, terms: dict[str, date]) -> pd.DataFrame:
        """Weekly rows of the given terms (term → refresh day to read); national rows have an empty region_code."""
        days = sorted(set(terms.values()))
        frames = []
        for i in range(0, len(days), MAX_DAYS_PER_QUERY):
            part = days[i : i + MAX_DAYS_PER_QUERY]
            names = sorted(t for t, d in terms.items() if d in part)
            result = self._bq().query(
                _TERMS_SQL.format(dataset=DATASET, table=TABLES[table]),
                [Param("days", "DATE", part), Param("country", "STRING", COUNTRY), Param("terms", "STRING", names)],
            )
            if result.columns != ROW_COLUMNS:
                raise UnexpectedResponse("BigQuery Google Trends terim sorgusu beklenen kolonları döndürmedi")
            frames.append(pd.DataFrame(result.rows, columns=ROW_COLUMNS))
        if not frames:
            return pd.DataFrame(columns=ROW_COLUMNS)
        frame = pd.concat(frames, ignore_index=True)
        try:
            frame["refresh_date"] = pd.to_datetime(frame["refresh_date"], format="%Y-%m-%d").dt.date
            frame["week"] = pd.to_datetime(frame["week"], format="%Y-%m-%d").dt.date
            frame["score"] = pd.to_numeric(frame["score"]).astype(float)
        except (ValueError, TypeError):
            raise UnexpectedResponse("BigQuery Google Trends terim sorgusunda tanınmayan değer var") from None
        if not frame["score"].between(0, 100).all():
            raise UnexpectedResponse("Google Trends skoru 0–100 aralığının dışında")
        frame["region_code"] = frame["region_code"].fillna("").astype(str)
        return frame
