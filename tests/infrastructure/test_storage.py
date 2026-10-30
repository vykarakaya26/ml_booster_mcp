import os
import stat
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.observations import OBSERVATION_COLUMNS
from mlbooster.infrastructure.storage.observation_store import ARROW_SCHEMA, ParquetObservationStore
from mlbooster.infrastructure.storage.panel_store import ParquetPanelStore


def _obs(value, vintage):
    return pd.DataFrame(
        {
            "source_id": "evds3", "dataset_id": "x", "series_id": "TP.TUKFIY2025.GENEL", "entity_id": "",
            "entity_name": None, "metric": "level", "date": [date(2026, 8, 31)], "freq": "M",
            "value": value, "unit": "index", "currency": None, "is_proxy": False,
            "vintage_ts": vintage, "publication_lag_days": 3,
        }
    )  # fmt: skip


def test_arrow_schema_matches_domain_contract():
    assert ARROW_SCHEMA.names == OBSERVATION_COLUMNS


def test_latest_vintage_and_as_of(tmp_path):
    store = ParquetObservationStore(tmp_path)
    v1 = datetime(2026, 9, 5, tzinfo=UTC)
    store.write(_obs(100.0, v1))
    store.write(_obs(101.0, v1 + timedelta(days=30)))  # revizyon
    assert store.read("evds3")["value"].tolist() == [101.0]
    assert store.read("evds3", as_of=v1 + timedelta(days=1))["value"].tolist() == [100.0]
    assert store.read("tuik").empty


def test_private_permissions(tmp_path):
    store = ParquetObservationStore(tmp_path / "silver")
    store.write(_obs(1.0, datetime(2026, 9, 5, tzinfo=UTC)))
    mode = stat.S_IMODE(os.stat(tmp_path / "silver" / "source_id=evds3").st_mode)
    assert mode & 0o077 == 0


def test_rejects_traversal_and_injection(tmp_path):
    store = ParquetObservationStore(tmp_path)
    with pytest.raises(InvalidInput):
        store.read("../../etc")
    with pytest.raises(InvalidInput):
        store.read("evds3", ["x') OR 1=1 --"])
    with pytest.raises(InvalidInput):
        ParquetPanelStore(tmp_path).save("../x", pd.DataFrame({"a": [1]}))


def test_rejects_duplicates_and_nulls(tmp_path):
    store = ParquetObservationStore(tmp_path)
    v = datetime(2026, 9, 5, tzinfo=UTC)
    with pytest.raises(InvalidInput, match="tekrar"):
        store.write(pd.concat([_obs(1.0, v), _obs(1.0, v)]))
    with pytest.raises(InvalidInput, match="null"):
        store.write(_obs(None, v))


def test_intermediate_dirs_are_private(tmp_path):
    from mlbooster.infrastructure.settings import ensure_private_dir

    ensure_private_dir(tmp_path / "a" / "b" / "c")
    for d in ["a", "a/b", "a/b/c"]:
        assert stat.S_IMODE(os.stat(tmp_path / d).st_mode) & 0o077 == 0, d
