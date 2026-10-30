"""CSV / JSON okuma — yalnızca standart kütüphane."""

from __future__ import annotations

import csv
import json
import math
from datetime import date
from pathlib import Path


def read_json(path: str | Path) -> dict | list:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_panel_csv(path: str | Path) -> tuple[list[date], dict[str, list[float | None]]]:
    """`mlbooster build-panel --out x.csv` çıktısı: ilk kolon tarih, diğerleri feature."""
    with Path(path).open(encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        dates: list[date] = []
        columns: dict[str, list[float | None]] = {name: [] for name in header[1:]}
        for row in reader:
            if not row:
                continue
            dates.append(date.fromisoformat(row[0][:10]))
            for name, raw in zip(header[1:], row[1:]):
                columns[name].append(_num(raw))
    return dates, columns


def _num(raw: str) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None
