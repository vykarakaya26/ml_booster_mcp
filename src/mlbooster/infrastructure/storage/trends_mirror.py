"""Local mirror of the Türkiye rows of the Google Trends BigQuery tables: a per-user opt-in, off by default.

BigQuery keeps only the last ~30 daily partitions and bills every query by the columns it reads for all countries
(about 0.25–0.4 GiB per day and query). The mirror downloads each day once and keeps it, so lists and term backfills
are read locally and the history outlives the upstream retention. Only the columns the package uses are stored.

Layout under `<root>/<table>/` (directories 0700, files written atomically):
- `lists/day=YYYY-MM-DD.parquet`: the day's list (term, rank); an empty file marks a day without a Türkiye list.
  A list file exists only after the day's backfill rows were written, so its presence means "day synced".
- `backfill/<sha256(term)[:32]>.parquet`: the weekly province scores of one term from the newest refresh that
  listed it (term, refresh_date, week, region_code, region_name, score; null scores are not stored). A newer refresh
  replaces it, because Google rescales the whole 5-year series on every refresh.
The user's answer lives next to the copy (`<root>.json`), so deleting the copy keeps the answer.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from mlbooster.domain.validation import slug
from mlbooster.infrastructure.settings import ensure_private_dir

LIST_SCHEMA = pa.schema([("term", pa.string()), ("rank", pa.int32())])
BACKFILL_SCHEMA = pa.schema(
    [
        ("term", pa.string()),
        ("refresh_date", pa.date32()),
        ("week", pa.date32()),
        ("region_code", pa.string()),
        ("region_name", pa.string()),
        ("score", pa.float64()),
    ]
)
_PREFIX = "day="


def _term_file(term: str) -> str:
    return hashlib.sha256(term.encode("utf-8")).hexdigest()[:32] + ".parquet"


def _atomic_write(table: pa.Table, path: Path) -> None:
    ensure_private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".parquet")
    os.close(fd)
    try:
        pq.write_table(table, tmp, compression="zstd")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class TrendsMirror:
    def __init__(self, root: Path):
        self._root = root
        self._settings = root.with_suffix(".json")

    # --- The user's answer ---------------------------------------------------------------

    def choice(self) -> str | None:
        """'enabled', 'declined' or None when the user was never asked; an unreadable file counts as never asked."""
        try:
            value = json.loads(self._settings.read_text(encoding="utf-8")).get("choice")
        except (OSError, ValueError, AttributeError):
            return None
        return value if value in {"enabled", "declined"} else None

    def set_choice(self, choice: str | None, at: str) -> None:
        ensure_private_dir(self._settings.parent)
        fd, tmp = tempfile.mkstemp(dir=self._settings.parent, prefix=".tmp-", suffix=".json")  # 0600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"choice": choice, "decided_at": at}, fh)
            os.replace(tmp, self._settings)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # --- Whole copy ----------------------------------------------------------------------

    def size_bytes(self) -> int:
        if not self._root.is_dir():
            return 0
        return sum(p.stat().st_size for p in self._root.rglob("*.parquet"))

    def delete(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)

    def _dir(self, table: str, kind: str) -> Path:
        return self._root / slug(table, "table") / kind

    # --- Daily lists ---------------------------------------------------------------------

    def days(self, table: str) -> set[date]:
        directory = self._dir(table, "lists")
        if not directory.is_dir():
            return set()
        out = set()
        for path in directory.glob(f"{_PREFIX}*.parquet"):
            try:
                out.add(date.fromisoformat(path.stem.removeprefix(_PREFIX)))
            except ValueError:
                continue
        return out

    def write_list(self, table: str, day: date, frame: pd.DataFrame) -> None:
        """The day's list (term, rank); an empty frame records a day without a Türkiye list."""
        data = pa.Table.from_pandas(frame[["term", "rank"]], schema=LIST_SCHEMA, preserve_index=False)
        _atomic_write(data, self._dir(table, "lists") / f"{_PREFIX}{day.isoformat()}.parquet")

    def lists(self, table: str, since: date, until: date) -> pd.DataFrame:
        """DataFrame[refresh_date, term, rank] of the mirrored days in [since, until]."""
        frames = []
        for day in sorted(d for d in self.days(table) if since <= d <= until):
            frame = pq.read_table(self._dir(table, "lists") / f"{_PREFIX}{day.isoformat()}.parquet").to_pandas()
            frames.append(frame.assign(refresh_date=day))
        if not frames:
            return pd.DataFrame(columns=["refresh_date", "term", "rank"])
        return pd.concat(frames, ignore_index=True)[["refresh_date", "term", "rank"]]

    # --- Term backfills ------------------------------------------------------------------

    def write_backfill(self, table: str, frame: pd.DataFrame) -> int:
        """Stores each term's rows unless a newer refresh of that term is already mirrored; returns terms written."""
        if frame.empty:
            return 0
        current = self.terms(table).set_index("term")["refresh_date"].to_dict()
        written = 0
        for term, rows in frame.groupby("term", sort=False):
            refresh = max(rows["refresh_date"])
            if term in current and current[term] > refresh:
                continue
            rows = rows[rows["refresh_date"] == refresh]
            data = pa.Table.from_pandas(rows[BACKFILL_SCHEMA.names], schema=BACKFILL_SCHEMA, preserve_index=False)
            _atomic_write(data, self._dir(table, "backfill") / _term_file(str(term)))
            written += 1
        return written

    def terms(self, table: str) -> pd.DataFrame:
        """DataFrame[term, refresh_date]: every mirrored term with the refresh its backfill comes from."""
        directory = self._dir(table, "backfill")
        if not directory.is_dir() or not any(directory.glob("*.parquet")):
            return pd.DataFrame(columns=["term", "refresh_date"])
        with duckdb.connect() as con:
            frame = con.sql(
                "SELECT term, max(refresh_date) AS refresh_date FROM read_parquet(?) GROUP BY term ORDER BY term",
                params=[str(directory / "*.parquet")],
            ).df()
        frame["refresh_date"] = pd.to_datetime(frame["refresh_date"]).dt.date  # same type as the list and backfill rows
        return frame

    def backfill(self, table: str, terms: list[str]) -> pd.DataFrame:
        """Rows of the given terms (exact strings)."""
        frames = []
        for term in terms:
            path = self._dir(table, "backfill") / _term_file(term)
            if path.is_file():
                frame = pq.read_table(path).to_pandas()
                frames.append(frame[frame["term"] == term])  # guard against a hash collision
        if not frames:
            return pd.DataFrame(columns=BACKFILL_SCHEMA.names)
        return pd.concat(frames, ignore_index=True)
