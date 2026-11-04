"""Published share figures (DuckDB file beside silver). Every value is bound as a parameter. A connection lives only for
one call, because the MCP server, the CLI and the panel are separate processes and DuckDB lets one of them write."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, fields
from datetime import date
from pathlib import Path

import duckdb

from mlbooster.application.errors import ProviderUnavailable
from mlbooster.domain.model import ShareEvidence
from mlbooster.infrastructure.settings import ensure_private_dir

FILE_NAME = "share_evidence.duckdb"
LOCK_RETRIES = 20
LOCK_WAIT_SECONDS = 0.1

COLUMNS = tuple(f.name for f in fields(ShareEvidence))
_SQL_TYPES = {"value": "DOUBLE", "value_high": "DOUBLE", "period_end": "DATE", "retrieved": "DATE", "second_hand": "BOOLEAN"}
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS share_evidence ("
    + ", ".join(f"{c} {_SQL_TYPES.get(c, 'VARCHAR')}{' PRIMARY KEY' if c == 'id' else ''}" for c in COLUMNS)
    + ");"
    "CREATE TABLE IF NOT EXISTS rejected_evidence (id VARCHAR PRIMARY KEY, reason VARCHAR, rejected_on DATE);"
)
_INSERT = f"INSERT INTO share_evidence ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))}) ON CONFLICT (id) DO NOTHING RETURNING id"
_SELECT = f"SELECT {', '.join(COLUMNS)} FROM share_evidence"


class DuckDbEvidenceStore:
    def __init__(self, root: Path):
        self._root = root

    @property
    def _path(self) -> Path:
        return self._root / FILE_NAME

    def _with[T](self, work: Callable[[duckdb.DuckDBPyConnection], T], read_only: bool = False) -> T:
        if not read_only:
            ensure_private_dir(self._root)
        for attempt in range(LOCK_RETRIES):
            try:
                con = duckdb.connect(str(self._path), read_only=read_only)
                break
            except duckdb.IOException:  # another process holds the file for a moment
                if attempt == LOCK_RETRIES - 1:
                    raise ProviderUnavailable("Bulgu deposu başka bir işlem tarafından kullanılıyor; biraz sonra yeniden deneyin") from None
                time.sleep(LOCK_WAIT_SECONDS)
        try:
            if not read_only:
                con.execute(_SCHEMA)
            return work(con)
        finally:
            con.close()

    def add(self, evidence: ShareEvidence) -> bool:
        values = asdict(evidence)
        return self._with(lambda con: bool(con.execute(_INSERT, [values[c] for c in COLUMNS]).fetchall()))

    def list(self, brand_keys: list[str] | None = None) -> list[ShareEvidence]:
        if not self._path.exists():
            return []
        query, params = _SELECT, []
        if brand_keys is not None:
            query += f" WHERE brand_key IN ({', '.join('?' * len(brand_keys))})"
            params = list(brand_keys)
        rows = self._with(lambda con: con.execute(query, params).fetchall(), read_only=True)
        return [ShareEvidence(*row) for row in rows]

    def remove(self, evidence_id: str, reason: str, at: date) -> ShareEvidence | None:
        def work(con: duckdb.DuckDBPyConnection) -> ShareEvidence | None:
            row = con.execute(f"{_SELECT} WHERE id = ?", [evidence_id]).fetchone()
            if row is None:
                return None
            con.execute("DELETE FROM share_evidence WHERE id = ?", [evidence_id])
            con.execute("INSERT INTO rejected_evidence VALUES (?, ?, ?) ON CONFLICT (id) DO NOTHING", [evidence_id, reason, at])
            return ShareEvidence(*row)

        return self._with(work)

    def rejection(self, evidence_id: str) -> str | None:
        if not self._path.exists():
            return None
        row = self._with(lambda con: con.execute("SELECT reason FROM rejected_evidence WHERE id = ?", [evidence_id]).fetchone(), read_only=True)
        return row[0] if row else None
