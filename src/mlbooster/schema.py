"""Silver katmanının uzun-format şeması."""

from __future__ import annotations

import pandas as pd
import pyarrow as pa

SILVER_SCHEMA = pa.schema(
    [
        ("source_id", pa.string()),
        ("dataset_id", pa.string()),
        ("series_id", pa.string()),
        ("entity_id", pa.string()),
        ("entity_name", pa.string()),
        ("metric", pa.string()),
        ("date", pa.date32()),  # dönem sonu
        ("freq", pa.string()),
        ("value", pa.float64()),
        ("unit", pa.string()),
        ("currency", pa.string()),
        ("is_proxy", pa.bool_()),
        ("vintage_ts", pa.timestamp("us", tz="UTC")),
        ("publication_lag_days", pa.int32()),
    ]
)
SILVER_COLUMNS = SILVER_SCHEMA.names
KEY_COLUMNS = ["source_id", "series_id", "entity_id", "metric", "freq", "date"]
REQUIRED_NON_NULL = ["source_id", "series_id", "metric", "date", "freq", "value", "vintage_ts"]


def empty_silver() -> pd.DataFrame:
    return SILVER_SCHEMA.empty_table().to_pandas()


def validate_silver(df: pd.DataFrame) -> pd.DataFrame:
    """Kolonları sıralar, tipleri zorlar, zorunlu alanları ve anahtar tekilliğini kontrol eder."""
    missing = set(SILVER_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"Silver şemasında eksik kolonlar: {sorted(missing)}")
    df = df[SILVER_COLUMNS].copy()
    nulls = [c for c in REQUIRED_NON_NULL if df[c].isna().any()]
    if nulls:
        raise ValueError(f"Boş olamayacak kolonlarda null var: {nulls}")
    df["entity_id"] = df["entity_id"].fillna("")
    dupes = df.duplicated(KEY_COLUMNS + ["vintage_ts"])
    if dupes.any():
        raise ValueError(f"Silver anahtarında {int(dupes.sum())} tekrar eden satır var")
    # pyarrow'a döndürmek tip doğrulamasını da yapar.
    return pa.Table.from_pandas(df, schema=SILVER_SCHEMA, preserve_index=False).to_pandas()
