"""Gözlem çerçevesi (silver uzun format) sözleşmesi. Depolama biçimi altyapıya aittir."""

from __future__ import annotations

import pandas as pd

from mlbooster.domain.errors import InvalidInput

OBSERVATION_COLUMNS = [
    "source_id", "dataset_id", "series_id", "entity_id", "entity_name", "metric",
    "date",  # dönem sonu
    "freq", "value", "unit", "currency", "is_proxy", "vintage_ts", "publication_lag_days",
]  # fmt: skip
KEY_COLUMNS = ["source_id", "series_id", "entity_id", "metric", "freq", "date"]
REQUIRED_NON_NULL = ["source_id", "series_id", "metric", "date", "freq", "value", "vintage_ts"]


def empty_observations() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in OBSERVATION_COLUMNS})


def validate_observations(df: pd.DataFrame) -> pd.DataFrame:
    """Kolonları sıralar; zorunlu alanları ve (anahtar, vintage) tekilliğini kontrol eder."""
    missing = set(OBSERVATION_COLUMNS) - set(df.columns)
    if missing:
        raise InvalidInput(f"Gözlem çerçevesinde eksik kolonlar: {sorted(missing)}")
    df = df[OBSERVATION_COLUMNS].copy()
    nulls = [c for c in REQUIRED_NON_NULL if df[c].isna().any()]
    if nulls:
        raise InvalidInput(f"Boş olamayacak kolonlarda null var: {nulls}")
    df["entity_id"] = df["entity_id"].fillna("")
    dupes = df.duplicated(KEY_COLUMNS + ["vintage_ts"])
    if dupes.any():
        raise InvalidInput(f"Gözlem anahtarında {int(dupes.sum())} tekrar eden satır var")
    return df
