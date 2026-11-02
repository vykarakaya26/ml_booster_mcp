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
    store.write(_obs(101.0, v1 + timedelta(days=30)))  # revision
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
    with pytest.raises(InvalidInput, match="duplicate|tekrar"):
        store.write(pd.concat([_obs(1.0, v), _obs(1.0, v)]))
    with pytest.raises(InvalidInput, match="null"):
        store.write(_obs(None, v))


def test_intermediate_dirs_are_private(tmp_path):
    from mlbooster.infrastructure.settings import ensure_private_dir

    ensure_private_dir(tmp_path / "a" / "b" / "c")
    for d in ["a", "a/b", "a/b/c"]:
        assert stat.S_IMODE(os.stat(tmp_path / d).st_mode) & 0o077 == 0, d


def test_requested_start_keeps_earliest_and_survives_reopen(tmp_path):
    store = ParquetObservationStore(tmp_path / "silver")
    assert store.requested_start("evds3", "TP.APIFON4", "level") is None
    store.record_requested_start("evds3", ["TP.APIFON4"], "level", date(2015, 1, 1))
    store.record_requested_start("evds3", ["TP.APIFON4"], "level", date(2018, 1, 1))  # later: unchanged
    store.record_requested_start("evds3", ["TP.APIFON4"], "level", date(2008, 11, 22))
    reopened = ParquetObservationStore(tmp_path / "silver")
    assert reopened.requested_start("evds3", "TP.APIFON4", "level") == date(2008, 11, 22)
    assert reopened.requested_start("evds3", "TP.APIFON4", "pct_change") is None  # metrics are kept apart
    path = tmp_path / "silver" / "source_id=evds3" / "_requested_starts.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert reopened.read("evds3").empty  # the record file is not read as observations


def test_requested_start_validates_and_tolerates_corrupt_file(tmp_path):
    store = ParquetObservationStore(tmp_path / "silver")
    with pytest.raises(InvalidInput):
        store.record_requested_start("../x", ["TP.A"], "level", date(2020, 1, 1))
    with pytest.raises(InvalidInput):
        store.record_requested_start("evds3", ["TP&x=1"], "level", date(2020, 1, 1))
    with pytest.raises(InvalidInput):
        store.requested_start("evds3", "TP.A", "level/../")
    store.record_requested_start("evds3", ["TP.A"], "level", date(2020, 1, 1))
    (tmp_path / "silver" / "source_id=evds3" / "_requested_starts.json").write_text("{bozuk")
    assert store.requested_start("evds3", "TP.A", "level") is None  # corrupt record: only one extra fetch
