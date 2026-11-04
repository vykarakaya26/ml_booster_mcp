import os
import stat
from datetime import UTC, date, datetime, timedelta

import duckdb
import pandas as pd
import pytest

from mlbooster.application.errors import ProviderUnavailable
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import ShareEvidence
from mlbooster.domain.observations import OBSERVATION_COLUMNS
from mlbooster.infrastructure.storage import evidence_store as module
from mlbooster.infrastructure.storage.evidence_store import DuckDbEvidenceStore
from mlbooster.infrastructure.storage.observation_store import ARROW_SCHEMA, ParquetObservationStore
from mlbooster.infrastructure.storage.panel_store import ParquetPanelStore
from mlbooster.infrastructure.storage.raw_store import RawStore


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


def test_raw_store_load_returns_the_latest_intact_copy(tmp_path):
    store = RawStore(tmp_path)
    url = "https://www.btk.gov.tr/s3/a.pdf"
    assert store.load("btk", url) is None  # nothing saved yet
    store.save("btk", url, b"%PDF-1 old", "application/pdf")
    path = store.save("btk", url, b"%PDF-1 new", "application/pdf")
    (tmp_path / "btk" / "manifest.jsonl").open("a").write("not json\n")
    assert store.load("btk", url) == b"%PDF-1 new"
    assert store.load("btk", "https://www.btk.gov.tr/s3/b.pdf") is None
    path.write_bytes(b"tampered")
    assert store.load("btk", url) is None  # the content hash no longer matches


def test_raw_store_load_ignores_file_names_it_did_not_write(tmp_path):
    store = RawStore(tmp_path)
    store.save("btk", "https://x/a.pdf", b"x", "application/pdf")
    (tmp_path / "secret.pdf").write_bytes(b"x")
    manifest = tmp_path / "btk" / "manifest.jsonl"
    manifest.write_text(manifest.read_text().replace('"file": "', '"file": "../secret.pdf#'))
    assert store.load("btk", "https://x/a.pdf") is None


# --- evidence store ---------------------------------------------------------------------------------------------------


def _evidence(**kwargs):
    base = {"id": "0123456789abcdef", "brand": "Migros", "brand_key": "migros", "sector": "perakende", "claim": "share", "value": 24.5,
            "value_high": None, "qualifier": "exact", "basis": "revenue", "geography": "Türkiye", "segment": None, "period": "2024",
            "period_end": date(2024, 12, 31), "published": "2025-03", "publisher": "Migros'; DROP TABLE share_evidence; --", "tier": "company",
            "url": "https://www.migroskurumsal.com/x?a=1", "quote": "pay %24,5", "retrieved": date(2026, 11, 4), "second_hand": False,
            "original_source": None, "original_date": None, "confidence": "medium"}  # fmt: skip
    return ShareEvidence(**{**base, **kwargs})


def test_evidence_store_round_trip_dedupes_and_lives_in_a_private_directory(tmp_path):
    root = tmp_path / "data" / "evidence"
    store = DuckDbEvidenceStore(root)
    assert store.list() == [] and store.rejection("0123456789abcdef") is None  # nothing is created by reading
    assert not root.exists()
    item = _evidence()
    assert store.add(item) is True and store.add(item) is False
    assert (root.stat().st_mode & 0o777) == 0o700
    assert store.list(["migros"]) == [item] and store.list(["togg"]) == [] and store.list() == [item]
    assert store.remove("ffffffffffffffff", "x", date(2026, 11, 4)) is None
    assert store.remove(item.id, "Yanlış yıl", date(2026, 11, 4)) == item
    assert store.list() == [] and store.rejection(item.id) == "Yanlış yıl"
    assert store.add(_evidence(id="1123456789abcdef")) is True and len(store.list()) == 1


def test_evidence_store_waits_for_another_writer_then_gives_a_safe_error(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "LOCK_WAIT_SECONDS", 0)
    calls = []

    def locked(path, read_only=False):
        calls.append(path)
        raise duckdb.IOException("Could not set lock on file /secret/path")

    monkeypatch.setattr(module.duckdb, "connect", locked)
    with pytest.raises(ProviderUnavailable) as exc:
        module.DuckDbEvidenceStore(tmp_path).add(_evidence())
    assert len(calls) == module.LOCK_RETRIES and "/secret" not in str(exc.value)
