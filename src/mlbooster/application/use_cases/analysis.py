"""Panel analysis: quality audit."""

from __future__ import annotations

from datetime import date

from mlbooster.application.dto import PanelResult
from mlbooster.application.use_cases.panel import DEFAULT_START, BuildPanel
from mlbooster.domain.errors import InvalidInput
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
        result = self._build(sector_id, start, end, save=False)  # do not touch the shared gold file
        if result.panel.empty:
            raise InvalidInput(f"{sector_id} paneli boş; atlanan feature'lar: {result.skipped}")
        features = [f for f in self._build.sector(sector_id).macro_features if f.name in result.panel.columns]
        transforms = {f.name: f.transform.value for f in features}
        companions = {
            frozenset((a.name, b.name))
            for i, a in enumerate(features)
            for b in features[i + 1 :]
            if a.series and a.series == b.series
        }
        report = audit_panel(result.panel, end or date.today(), thresholds, transforms, companions)
        return report, result
