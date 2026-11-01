"""Kullanıcının yerel CSV/JSON tablosunu okur (pazar payı girdisi).

Yalnızca CLI kullanır; MCP araçları dosya yolu almaz. Uzantı izin listesi ve boyut sınırı uygulanır.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pandas as pd

from mlbooster.domain.errors import InvalidInput

MAX_BYTES = 20 * 1024 * 1024
SUFFIXES = {".csv", ".json"}


class LocalTableReader:
    def read(self, path: Path) -> pd.DataFrame:
        path = Path(path).expanduser()
        if path.suffix.lower() not in SUFFIXES:
            raise InvalidInput(f"Desteklenen dosya türleri: {', '.join(sorted(SUFFIXES))}")
        if not path.is_file():
            raise InvalidInput(f"Dosya bulunamadı: {path.name}")
        if path.stat().st_size > MAX_BYTES:
            raise InvalidInput(f"Dosya {MAX_BYTES // (1024 * 1024)} MB sınırını aşıyor: {path.name}")
        text = path.read_text(encoding="utf-8-sig")
        return _json(text, path.name) if path.suffix.lower() == ".json" else _csv(text, path.name)

    def read_panel(self, path: Path) -> pd.DataFrame:
        frame = self.read(path)
        if frame.empty or frame.shape[1] < 2:
            raise InvalidInput(f"{Path(path).name}: ilk kolonu tarih olan geniş bir panel bekleniyor")
        first = frame.columns[0]
        try:
            index = pd.to_datetime(frame[first], format="mixed")
        except (ValueError, TypeError):
            raise InvalidInput(f"{Path(path).name}: ilk kolon ({first}) tarih olarak okunamadı") from None
        panel = frame.drop(columns=[first]).apply(pd.to_numeric, errors="coerce")
        panel.index = pd.DatetimeIndex(index, name="date")
        if panel.index.has_duplicates:
            raise InvalidInput(f"{Path(path).name}: tekrarlanan tarih var")
        return panel.sort_index()


def _csv(text: str, name: str) -> pd.DataFrame:
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        sep = dialect.delimiter
    except csv.Error:
        sep = ","
    # Türkçe Excel dışa aktarımları ';' ayırıcı ve ondalık virgül kullanır.
    decimal = "," if sep == ";" else "."
    try:
        return pd.read_csv(io.StringIO(text), sep=sep, decimal=decimal, dtype={"date": str, "tarih": str, "dönem": str, "donem": str})
    except (pd.errors.ParserError, ValueError) as exc:
        raise InvalidInput(f"{name} CSV olarak okunamadı: {exc}") from None


def _json(text: str, name: str) -> pd.DataFrame:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidInput(f"{name} geçerli JSON değil: {exc.msg} (satır {exc.lineno})") from None
    if isinstance(data, dict):
        data = next((data[k] for k in ("rows", "data", "records") if isinstance(data.get(k), list)), None)
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise InvalidInput(f"{name}: satır nesnelerinden oluşan bir liste (ya da 'rows' altında liste) bekleniyor")
    return pd.DataFrame.from_records(data)
