"""Gold: sector panels (Parquet)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from mlbooster.domain.validation import slug
from mlbooster.infrastructure.settings import ensure_private_dir


class ParquetPanelStore:
    def __init__(self, root: Path):
        self._root = root

    def save(self, sector_id: str, panel: pd.DataFrame) -> str:
        path = ensure_private_dir(self._root) / f"{slug(sector_id, 'sector')}.parquet"
        panel.to_parquet(path)
        return str(path)

    @staticmethod
    def export(panel: pd.DataFrame, path: Path) -> str:
        """Export to a path explicitly given by the user (.parquet / .csv); called only from the local CLI."""
        if path.suffix not in {".parquet", ".csv"}:
            raise ValueError("Çıktı dosyası .parquet ya da .csv uzantılı olmalıdır")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".csv":
            panel.to_csv(path, date_format="%Y-%m-%d")
        else:
            panel.to_parquet(path)
        return str(path)
