"""Pazar payı analizi: kullanıcı tablosu + (isteğe bağlı) sektör makro paneli."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from mlbooster.application.ports import TableReader
from mlbooster.application.use_cases.panel import BuildPanel
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.services.market_share import MAX_ROWS, ShareConfig, analyze
from mlbooster.domain.validation import slug


class AnalyzeMarketShare:
    def __init__(self, build_panel: BuildPanel, reader: TableReader):
        self._build = build_panel
        self._reader = reader

    def from_file(self, path: Path, config: ShareConfig, sector_id: str | None = None, start: date | None = None) -> dict:
        return self(self._reader.read(path), config, sector_id, start)

    def from_records(self, records: list[dict], config: ShareConfig, sector_id: str | None = None, start: date | None = None) -> dict:
        if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
            raise InvalidInput("records, {entity, date, value[, segment]} nesnelerinden oluşan bir liste olmalıdır")
        if len(records) > MAX_ROWS:
            raise InvalidInput(f"En fazla {MAX_ROWS:,} satır gönderilebilir")
        return self(pd.DataFrame.from_records(records), config, sector_id, start)

    def __call__(self, table: pd.DataFrame, config: ShareConfig, sector_id: str | None = None, start: date | None = None) -> dict:
        panel = None
        skipped: dict[str, str] = {}
        if sector_id:
            sector_id = slug(sector_id, "sektör")
            first = start or date(2012, 1, 1)
            result = self._build(sector_id, first, None, save=False)
            panel, skipped = result.panel, result.skipped
        out = analyze(table, config, panel)
        out["meta"]["sector"] = sector_id
        if skipped:
            out["meta"]["panel_skipped"] = skipped
        return out
