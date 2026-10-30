"""Silver: normalize uzun format, Parquet + DuckDB. Her yazım yeni bir vintage dosyasıdır;
okumada her anahtar için `as_of` anına kadarki en son vintage seçilir (look-ahead yok)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

import duckdb
import pandas as pd

from mlbooster.schema import KEY_COLUMNS, SILVER_COLUMNS, empty_silver, validate_silver


class SilverStore:
    def __init__(self, root: Path):
        self.root = root

    def write(self, df: pd.DataFrame) -> list[Path]:
        if df.empty:
            return []
        df = validate_silver(df)
        paths = []
        for source_id, part in df.groupby("source_id"):
            directory = self.root / f"source_id={source_id}"
            directory.mkdir(parents=True, exist_ok=True)
            stamp = part["vintage_ts"].max().strftime("%Y%m%dT%H%M%S")
            path = directory / f"part-{stamp}-{uuid4().hex[:8]}.parquet"
            part.to_parquet(path, index=False)
            paths.append(path)
        return paths

    def read(
        self,
        source_id: str | None = None,
        series_ids: list[str] | None = None,
        as_of: datetime | None = None,
    ) -> pd.DataFrame:
        pattern = self.root / (f"source_id={source_id}" if source_id else "*") / "*.parquet"
        if not list(self.root.glob(str(pattern.relative_to(self.root)))):
            return empty_silver()
        where, params = ["TRUE"], []
        if series_ids:
            where.append(f"series_id IN ({', '.join('?' * len(series_ids))})")
            params += series_ids
        if as_of is not None:
            where.append("vintage_ts <= ?")
            params.append(as_of)
        keys = ", ".join(KEY_COLUMNS)
        query = f"""
            SELECT {", ".join(SILVER_COLUMNS)} FROM read_parquet(?, union_by_name = true, hive_partitioning = false)
            WHERE {" AND ".join(where)}
            QUALIFY row_number() OVER (PARTITION BY {keys} ORDER BY vintage_ts DESC) = 1
            ORDER BY series_id, metric, date
        """
        with duckdb.connect() as con:
            return con.sql(query, params=[str(pattern), *params]).to_arrow_table().to_pandas()
