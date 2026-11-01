from datetime import date

import numpy as np
import pandas as pd
import pytest

from mlbooster.domain.errors import InconsistentCatalog, InvalidInput
from mlbooster.domain.model import FeatureRecipe
from mlbooster.domain.services.calendar_tr import holiday_days, month_calendar
from mlbooster.domain.services.feature_engineering import (
    FeatureConfig,
    bh_qvalues,
    effective_n,
    engineer,
    generate,
    recipe_series,
)


def _panel(n=150, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2012-01-31", periods=n, freq="ME")
    driver = rng.normal(0, 1, n)
    noise = rng.normal(0, 1, (n, 2))
    target = np.cumsum(0.8 * np.roll(driver, 1) + 0.3 * noise[:, 0])  # the change of the target depends on the previous driver value
    return pd.DataFrame({"driver": driver, "noise": noise[:, 1], "rate": 10 + np.cumsum(rng.normal(0, .2, n)),
                         "inflation": 8 + np.cumsum(rng.normal(0, .2, n)), "target": target}, index=idx)  # fmt: skip


def test_recipes_compute_expected_values():
    p = _panel()
    spread = recipe_series(p, FeatureRecipe("real", "spread", ("rate", "inflation")))
    assert spread.equals(p["rate"] - p["inflation"])
    gap = recipe_series(p, FeatureRecipe("g", "gap", ("rate",), window=12))
    assert gap.iloc[20] == pytest.approx(p["rate"].iloc[20] - p["rate"].iloc[8:20].mean())


@pytest.mark.parametrize("bad", [
    dict(name="x", op="difference", inputs=("a", "b")), dict(name="x", op="spread", inputs=("a",)),
    dict(name="x", op="gap", inputs=("a",)), dict(name="x", op="mean", inputs=("a",)),
])  # fmt: skip
def test_recipe_validation(bad):
    with pytest.raises(InconsistentCatalog):
        FeatureRecipe(**bad)


def test_generated_features_are_causal():
    """No feature at time t may be affected by observations after t."""
    p = _panel()
    recipes = [FeatureRecipe("real", "spread", ("rate", "inflation")), FeatureRecipe("z", "zscore", ("rate",), window=12)]
    cfg = FeatureConfig()
    base, _, _ = generate(p, recipes, cfg)
    cut = p.index[100]
    changed = p.copy()
    changed.loc[changed.index > cut] = changed.loc[changed.index > cut] * -7 + 50
    after, _, _ = generate(changed, recipes, cfg)
    pd.testing.assert_frame_equal(base.loc[:cut], after.loc[:cut])


def test_screening_selects_true_driver_and_controls_false_discoveries():
    res = engineer(_panel(), [], FeatureConfig(target="target", horizon=1, train_end=date(2021, 12, 31), calendar=False, events=False))
    assert "değişim" in res.label  # persistent target → screening against the change
    assert res.selected and res.selected[0].startswith("driver")
    noise = [name for name in res.selected if name.startswith("noise")]
    assert len(noise) / len(res.selected) <= 0.2  # FDR is controlled at the 10% level; not zero
    assert res.screening["q"].dropna().between(0, 1).all()


def test_selection_ignores_data_after_train_end():
    p = _panel()
    cfg = FeatureConfig(target="target", horizon=1, train_end=date(2021, 12, 31), calendar=False, events=False)
    first = engineer(p, [], cfg)
    changed = p.copy()
    changed.loc["2022-01-31":, "noise"] = changed.loc["2022-01-31":, "target"].diff().shift(-1) * 100  # leaky afterwards
    second = engineer(changed, [], cfg)
    assert first.selected == second.selected


def test_statistics_helpers():
    assert bh_qvalues(np.array([0.01, 0.04, 0.03, 0.5])).tolist() == pytest.approx([0.04, 0.0533333, 0.0533333, 0.5], rel=1e-4)
    rng = np.random.default_rng(3)
    walk = np.cumsum(rng.normal(size=200))
    assert effective_n(walk, walk) < 20 < effective_n(rng.normal(size=200), rng.normal(size=200))


def test_monthly_panel_required_and_missing_recipe_inputs_skipped():
    p = _panel()
    weekly = p.copy()
    weekly.index = pd.date_range("2020-01-03", periods=len(p), freq="W-FRI")
    with pytest.raises(InvalidInput, match="aylık"):
        generate(weekly, [], FeatureConfig())
    _, _, skipped = generate(p, [FeatureRecipe("x", "spread", ("rate", "no_such_feature"))], FeatureConfig())
    assert "no_such_feature" in skipped["x"]


def test_calendar_working_days_and_fixed_holidays():
    days = holiday_days(date(2024, 1, 1), date(2024, 12, 31))
    assert days[date(2024, 10, 29)] == (1.0, None) and days[date(2024, 10, 28)] == (0.5, None)
    assert date(2016, 7, 15) not in holiday_days(date(2016, 1, 1), date(2016, 12, 31))  # from 2017 onwards
    cal = month_calendar(pd.date_range("2024-01-31", periods=12, freq="ME"))
    assert set(cal.columns) >= {"ay_sin", "ay_cos", "is_gunu", "is_gunu_fark", "ramazan_bayrami_gun", "kurban_bayrami_gun"}


def test_moving_holidays_reduce_working_days():
    cal = month_calendar(pd.date_range("2024-01-31", periods=12, freq="ME"))
    april, june = cal.loc["2024-04-30"], cal.loc["2024-06-30"]
    assert april["ramazan_bayrami_gun"] == 3 and june["kurban_bayrami_gun"] == 4
    # April 2024: 22 weekdays − 9 April eve (0.5) − 10–12 April Eid al-Fitr (3) − 23 April (1) = 17.5
    assert april["is_gunu"] == 17.5
    assert cal["is_gunu"].notna().all()


def test_sector_events_and_ramadan_windows(catalog):
    auto = catalog.sector("otomotiv")
    assert any(e.name == "otv_indirimi_2018" for e in auto.events)
    p = _panel(n=120)
    p.index = pd.date_range("2016-01-31", periods=120, freq="ME")
    feats, specs, _ = generate(p, [], FeatureConfig(), auto.events)
    assert feats.loc["2018-11-30":"2019-06-30", "otv_indirimi_2018"].eq(1).all()
    assert feats.loc["2019-07-31", "otv_indirimi_2018"] == 0
    cal = month_calendar(pd.date_range("2025-02-28", periods=2, freq="ME"))
    assert cal.loc["2025-03-31", "ramazan_ayi_gun"] == 29 and cal.loc["2025-02-28", "ramazan_ayi_gun"] == 1
