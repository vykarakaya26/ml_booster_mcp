"""Local file use cases (CLI only; MCP tools never accept file paths)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from mlbooster.application.ports import PanelStore
from mlbooster.domain.errors import InvalidInput

EXPORT_SUFFIXES = (".parquet", ".csv")


class ExportTable:
    """Writes a frame to a path the user gave explicitly. `check` validates the path before any expensive work."""

    def __init__(self, panels: PanelStore):
        self._panels = panels

    @staticmethod
    def check(path: Path) -> Path:
        if path.suffix not in EXPORT_SUFFIXES:
            raise InvalidInput(f"Çıktı dosyası {' ya da '.join(EXPORT_SUFFIXES)} uzantılı olmalıdır: {path.name}")
        return path

    def __call__(self, frame: pd.DataFrame, path: Path) -> str:
        return self._panels.export(frame, self.check(path))
