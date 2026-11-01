"""Silver: Parquet + DuckDB. Every write is a new vintage file; on read, the latest vintage up to
`as_of` is selected for each key (revisions are preserved, no look-ahead)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from mlbooster.domain.observations import KEY_COLUMNS, OBSERVATION_COLUMNS, empty_observations, validate_observations
from mlbooster.domain.validation import series_code, series_codes, slug
from mlbooster.infrastructure.settings import ensure_private_dir

REQUESTS_FILE = "_requested_starts.json"  # *.parquet glob'una girmez
MAX_REQUESTS_BYTES = 1024 * 1024

ARROW_SCHEMA = pa.schema(
    [
        ("source_id", pa.string()),
        ("dataset_id", pa.string()),
        ("series_id", pa.string()),
        ("entity_id", pa.string()),
        ("entity_name", pa.string()),
        ("metric", pa.string()),
        ("date", pa.date32()),
        ("freq", pa.string()),
        ("value", pa.float64()),
        ("unit", pa.string()),
        ("currency", pa.string()),
        ("is_proxy", pa.bool_()),
        ("vintage_ts", pa.timestamp("us", tz="UTC")),
        ("publication_lag_days", pa.int32()),
    ]
)

class ParquetObservationStore:
    def __init__(self, root: Path):
        self._root = root

    def _dir(self, source_id: str) -> Path:
        return self._root / f"source_id={slug(source_id, 'source')}"

    def write(self, observations: pd.DataFrame) -> None:
        if observations.empty:
            return
        df = validate_observations(observations)
        table = pa.Table.from_pandas(df, schema=ARROW_SCHEMA, preserve_index=False)  # type validation
        for source_id in df["source_id"].unique():
            part = table.filter(pc.equal(table["source_id"], source_id))
            directory = ensure_private_dir(self._dir(source_id))
            stamp = df["vintage_ts"].max().strftime("%Y%m%dT%H%M%S")

            pq.write_table(part, directory / f"part-{stamp}-{uuid4().hex[:8]}.parquet")

    def read(
        self,
        source_id: str,
        series_ids: list[str] | None = None,
        as_of: datetime | None = None,
    ) -> pd.DataFrame:
        directory = self._dir(source_id)
        codes = series_codes(series_ids) if series_ids else ()  # validate first, then look at the files
        if not any(directory.glob("*.parquet")):
            return empty_observations()
        where, params = ["TRUE"], [str(directory / "*.parquet")]
        if codes:
            where.append(f"series_id IN ({', '.join('?' * len(codes))})")
            params += codes
        if as_of is not None:
            where.append("vintage_ts <= ?")
            params.append(as_of)
        # All values are bound as parameters; only fixed column names enter the SQL.
        query = f"""
            SELECT {", ".join(OBSERVATION_COLUMNS)}
            FROM read_parquet(?, union_by_name = true, hive_partitioning = false)
            WHERE {" AND ".join(where)}
            QUALIFY row_number() OVER (PARTITION BY {", ".join(KEY_COLUMNS)} ORDER BY vintage_ts DESC) = 1
            ORDER BY series_id, metric, date
        """
        with duckdb.connect() as con:
            return con.sql(query, params=params).to_arrow_table().to_pandas()

    # --- requested-start records ------------------------------------------------------------------------------
    # Per series: "the provider was asked from this date onwards". Series that start later than the requested
    # start are then not refetched on every panel build. A lost record only costs one extra fetch.

    def _requests_path(self, source_id: str) -> Path:
        return self._dir(source_id) / REQUESTS_FILE

    def _load_requests(self, source_id: str) -> dict[str, str]:
        path = self._requests_path(source_id)
        try:
            if path.stat().st_size > MAX_REQUESTS_BYTES:
                return {}
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(data, dict) else {}

    def record_requested_start(self, source_id: str, series_ids: list[str], metric: str, start: date) -> None:
        codes = series_codes(series_ids)
        metric = slug(metric, "metrik")
        requests = self._load_requests(source_id)
        changed = False
        for code in codes:
            key = f"{code}|{metric}"
            if key not in requests or start.isoformat() < requests[key]:
                requests[key] = start.isoformat()
                changed = True
        if not changed:
            return
        directory = ensure_private_dir(self._dir(source_id))
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".requests-", suffix=".tmp")  # 0600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(requests, fh, sort_keys=True)
            os.replace(tmp, directory / REQUESTS_FILE)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def requested_start(self, source_id: str, series_id: str, metric: str) -> date | None:
        value = self._load_requests(source_id).get(f"{series_code(series_id)}|{slug(metric, 'metrik')}")
        try:
            return date.fromisoformat(value) if value else None
        except ValueError:
            return None
