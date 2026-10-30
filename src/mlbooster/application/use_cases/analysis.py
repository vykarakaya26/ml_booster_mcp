"""Panel üzerinde analiz: kalite denetimi ve referans tahmin backtest'i."""

from __future__ import annotations

from datetime import date

from mlbooster.application.dto import PanelResult
from mlbooster.application.use_cases.panel import DEFAULT_START, BuildPanel
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.services.backtest import BacktestConfig, BacktestResult, rolling_origin_backtest
from mlbooster.domain.services.quality import QualityReport, QualityThresholds, audit_panel


class AuditPanel:
    def __init__(self, build_panel: BuildPanel):
        self._build = build_panel

    def __call__(
        self,
        sector_id: str,
        start: date = DEFAULT_START,
        end: date | None = None,
        thresholds: QualityThresholds | None = None,
    ) -> tuple[QualityReport, PanelResult]:
        result = self._build(sector_id, start, end)
        if result.panel.empty:
            raise InvalidInput(f"{sector_id} paneli boş; atlanan feature'lar: {result.skipped}")
        return audit_panel(result.panel, end or date.today(), thresholds), result


class RunBacktest:
    def __init__(self, build_panel: BuildPanel):
        self._build = build_panel

    def __call__(
        self,
        sector_id: str,
        config: BacktestConfig,
        start: date = DEFAULT_START,
        end: date | None = None,
    ) -> tuple[BacktestResult, PanelResult]:
        result = self._build(sector_id, start, end)
        if result.panel.empty:
            raise InvalidInput(f"{sector_id} paneli boş; atlanan feature'lar: {result.skipped}")
        return rolling_origin_backtest(result.panel, config), result
