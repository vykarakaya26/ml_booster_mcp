from datetime import date

import numpy as np
import pandas as pd

from mlbooster.application.dto import PanelResult
from mlbooster.application.use_cases import EngineerFeatures, PreparePanel
from mlbooster.domain.services.feature_engineering import FeatureConfig
from mlbooster.domain.services.preprocessing import PrepConfig


class StubBuild:
    """Fake replacing BuildPanel that returns the real sector catalog and a synthetic panel."""

    def __init__(self, catalog, sector_id="makro_genel"):
        self._sector = catalog.sector(sector_id)
        rng = np.random.default_rng(0)
        idx = pd.date_range("2012-01-31", periods=150, freq="ME")
        cols = {f.name: rng.normal(0, 1, 150).cumsum() + 20 for f in self._sector.macro_features}
        self.panel = pd.DataFrame(cols, index=idx)
        self.calls = []

    def sector(self, sector_id):
        return self._sector

    def __call__(self, sector_id, start, end, save=True, **kw):
        self.calls.append(save)
        return PanelResult(sector_id, self.panel, {"missing": "series code not discovered"})


def test_prepare_panel_uses_catalog_transforms_and_never_saves(catalog):
    build = StubBuild(catalog)
    result, panel = PreparePanel(build)("makro_genel", PrepConfig(target="tufe_yoy", train_end=date(2020, 12, 31)), date(2012, 1, 1))
    assert build.calls == [False] and panel.skipped
    plans = {p.feature: [s["step"] for s in p.steps] for p in result.plans}
    assert plans["tufe_yoy"] == [] and "diff" not in plans["tufe_yoy"]


def test_engineer_features_applies_sector_recipes(catalog):
    build = StubBuild(catalog)
    result, prepared, _ = EngineerFeatures(build)(
        "makro_genel", FeatureConfig(target="tufe_yoy", train_end=date(2020, 12, 31)), PrepConfig(target="tufe_yoy", train_end=date(2020, 12, 31))
    )  # fmt: skip
    names = {s.name for s in result.specs if s.family == "sector_recipe"}
    assert {"reel_faiz_ex_ante", "aofm_politika_farki"} <= names and prepared is not None
    assert result.screening is not None and "q" in result.screening
