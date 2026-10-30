"""Silver: Parquet + DuckDB. Her yazım yeni bir vintage dosyasıdır; okumada her anahtar için
`as_of` anına kadarki en son vintage seçilir (revizyonlar korunur, look-ahead yok)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from mlbooster.domain.observations import KEY_COLUMNS, OBSERVATION_COLUMNS, empty_observations, validate_observations
from mlbooster.domain.validation import series_codes, slug
from mlbooster.infrastructure.settings import ensure_private_dir

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
        return self._root / f"source_id={slug(source_id, 'kaynak')}"

    def write(self, observations: pd.DataFrame) -> None:
        if observations.empty:
            return
        df = validate_observations(observations)
        table = pa.Table.from_pandas(df, schema=ARROW_SCHEMA, preserve_index=False)  # tip doğrulaması
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
        codes = series_codes(series_ids) if series_ids else ()  # önce doğrula, sonra dosyaya bak
        if not any(directory.glob("*.parquet")):
            return empty_observations()
        where, params = ["TRUE"], [str(directory / "*.parquet")]
        if codes:
            where.append(f"series_id IN ({', '.join('?' * len(codes))})")
            params += codes
        if as_of is not None:
            where.append("vintage_ts <= ?")
            params.append(as_of)
        # Tüm değerler parametre olarak bağlanır; SQL'e yalnızca sabit kolon adları girer.
        query = f"""
            SELECT {", ".join(OBSERVATION_COLUMNS)}
            FROM read_parquet(?, union_by_name = true, hive_partitioning = false)
            WHERE {" AND ".join(where)}
            QUALIFY row_number() OVER (PARTITION BY {", ".join(KEY_COLUMNS)} ORDER BY vintage_ts DESC) = 1
            ORDER BY series_id, metric, date
        """
        with duckdb.connect() as con:
            return con.sql(query, params=params).to_arrow_table().to_pandas()
