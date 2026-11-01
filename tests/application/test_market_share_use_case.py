from datetime import date

import pandas as pd
import pytest

from mlbooster.application.dto import PanelResult
from mlbooster.application.use_cases.market_share import AnalyzeMarketShare
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.services.market_share import ShareConfig


class StubPanel:
    def __init__(self):
        self.calls = []

    def __call__(self, sector_id, start, end, save=True):
        self.calls.append((sector_id, start, save))
        idx = pd.date_range("2019-01-31", periods=96, freq="ME")
        return PanelResult(sector_id, pd.DataFrame({"x": range(96)}, index=idx), {"eksik": "sağlayıcı yok"})


class StubReader:
    def __init__(self, frame):
        self.frame = frame

    def read(self, path):
        return self.frame


def _records():
    months = pd.period_range("2020-01", "2025-12", freq="M").astype(str)
    return [{"entity": e, "date": m, "value": v * (1 + i / 50)} for i, m in enumerate(months) for e, v in (("A", 6), ("B", 4))]


def test_from_records_with_sector_adds_drivers_without_saving_panel():
    panel = StubPanel()
    out = AnalyzeMarketShare(panel, StubReader(None)).from_records(_records(), ShareConfig(focus="A"), "otomotiv", date(2019, 1, 1))
    assert panel.calls == [("otomotiv", date(2019, 1, 1), False)]
    assert out["meta"]["sector"] == "otomotiv" and out["meta"]["panel_skipped"] == {"eksik": "sağlayıcı yok"}
    assert "drivers" in out


def test_rejects_bad_records_and_sector():
    uc = AnalyzeMarketShare(StubPanel(), StubReader(None))
    with pytest.raises(InvalidInput, match="liste"):
        uc.from_records("A,B", ShareConfig())
    with pytest.raises(InvalidInput, match="Geçersiz sektör"):
        uc.from_records(_records(), ShareConfig(), "../x")


def test_from_file_uses_reader():
    uc = AnalyzeMarketShare(StubPanel(), StubReader(pd.DataFrame(_records())))
    assert uc.from_file("ignored.csv", ShareConfig())["meta"]["entities"] == 2
