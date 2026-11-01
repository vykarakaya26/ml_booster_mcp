import json

import pytest

from mlbooster.domain.errors import InvalidInput
from mlbooster.infrastructure.table_reader import LocalTableReader


def test_reads_semicolon_csv_with_decimal_comma(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("entity;date;value\nA;2025-01;1,5\nB;2025-01;2,25\n", encoding="utf-8-sig")
    df = LocalTableReader().read(p)
    assert df["value"].tolist() == [1.5, 2.25] and df["date"].tolist() == ["2025-01", "2025-01"]


def test_reads_json_rows_wrapper(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"rows": [{"entity": "A", "date": "2025", "value": 1}]}), encoding="utf-8")
    assert len(LocalTableReader().read(p)) == 1


@pytest.mark.parametrize(("name", "content", "match"), [
    ("t.xlsx", "x", "Desteklenen"),
    ("t.json", "{bad", "geçerli JSON"),
    ("t.json", '{"a": 1}', "liste"),
])  # fmt: skip
def test_rejects_bad_files(tmp_path, name, content, match):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    with pytest.raises(InvalidInput, match=match):
        LocalTableReader().read(p)


def test_missing_file(tmp_path):
    with pytest.raises(InvalidInput, match="bulunamadı"):
        LocalTableReader().read(tmp_path / "yok.csv")
