"""Modellemeye hazırlık: dağılıma dayalı ön işleme ve otomatik değişken üretimi (sektör paneli üzerinde)."""

from __future__ import annotations

from datetime import date

from mlbooster.application.dto import PanelResult
from mlbooster.application.use_cases.panel import DEFAULT_START, BuildPanel
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.services.feature_engineering import FeatureConfig, FeatureResult, engineer
from mlbooster.domain.services.preprocessing import PrepConfig, PrepResult, prepare


class PreparePanel:
    def __init__(self, build_panel: BuildPanel):
        self._build = build_panel

    def __call__(self, sector_id: str, config: PrepConfig, start: date = DEFAULT_START, end: date | None = None) -> tuple[PrepResult, PanelResult]:
        result = self._build(sector_id, start, end, save=False)
        if result.panel.empty:
            raise InvalidInput(f"{sector_id} paneli boş; atlanan değişkenler: {result.skipped}")
        transforms = {f.name: f.transform.value for f in self._build.sector(sector_id).macro_features}
        return prepare(result.panel, config, transforms), result


class EngineerFeatures:
    def __init__(self, build_panel: BuildPanel):
        self._build = build_panel
        self._prepare = PreparePanel(build_panel)

    def __call__(
        self,
        sector_id: str,
        config: FeatureConfig,
        prep: PrepConfig | None = None,
        start: date = DEFAULT_START,
        end: date | None = None,
    ) -> tuple[FeatureResult, PrepResult | None, PanelResult]:
        if prep is not None:
            prepared, result = self._prepare(sector_id, prep, start, end)
            panel = prepared.panel
        else:
            prepared, result = None, self._build(sector_id, start, end, save=False)
            panel = result.panel
            if panel.empty:
                raise InvalidInput(f"{sector_id} paneli boş; atlanan değişkenler: {result.skipped}")
        sector = self._build.sector(sector_id)
        return engineer(panel, sector.feature_recipes, config, sector.events), prepared, result
