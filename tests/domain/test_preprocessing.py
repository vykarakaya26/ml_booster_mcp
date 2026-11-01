from datetime import date

import numpy as np
import pandas as pd
import pytest

from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.services.preprocessing import PrepConfig, fit_yeo_johnson, prepare, profile, robust_z, yeo_johnson


def _panel(n=120, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2012-01-31", periods=n, freq="ME")
    return pd.DataFrame({
        "normal": rng.normal(0, 1, n),
        "carpik": rng.lognormal(0, 1, n),  # sağa çarpık, pozitif
        "duzey": np.cumsum(rng.normal(0.5, 1, n)) + 100,  # birim kök
        "hedef": rng.normal(5, 2, n),
    }, index=idx)  # fmt: skip


def test_profile_chooses_robust_center_for_skewed_data():
    p = _panel()
    assert profile(p["normal"], "normal", None).center_scale == "ortalama/standart sapma"
    skewed = profile(p["carpik"], "carpik", None)
    assert skewed.center_scale == "medyan/IQR" and any("çarpıklık" in r for r in skewed.reasons)
    assert skewed.mad == pytest.approx((p["carpik"] - p["carpik"].median()).abs().median() * 1.4826)


def test_robust_z_matches_iglewicz_hoaglin():
    x = pd.Series([1.0, 2, 3, 4, 100])
    z = robust_z(x)
    assert z.iloc[-1] == pytest.approx(0.6745 * (100 - 3) / 1)


def test_yeo_johnson_inverse_cases_and_fit_reduces_skew():
    x = np.array([-2.0, -0.5, 0.0, 0.5, 2.0])
    assert yeo_johnson(x, 1.0) == pytest.approx(x)  # λ=1 özdeşlik
    sample = np.random.default_rng(1).lognormal(0, 1, 400)
    lam = fit_yeo_johnson(sample)
    assert lam < 0.5 and abs(pd.Series(yeo_johnson(sample, lam)).skew()) < abs(pd.Series(sample).skew()) / 3


def test_plan_for_linear_and_tree_models():
    p = _panel()
    lin = {pl.feature: [s["step"] for s in pl.steps] for pl in prepare(p, PrepConfig(target="hedef", train_end=date(2019, 12, 31))).plans}
    assert lin["hedef"] == []  # hedefin dağılımı değiştirilmez
    assert lin["normal"] == ["scale_standard"]
    assert "yeo_johnson" in lin["carpik"] or "log" in lin["carpik"]
    assert lin["carpik"][-1] == "scale_robust"
    assert lin["duzey"][0] == "diff"
    tree = {pl.feature: [s["step"] for s in pl.steps] for pl in prepare(p, PrepConfig(model_family="tree", train_end=date(2019, 12, 31))).plans}
    assert all(not any(s.startswith(("scale_", "yeo", "log")) for s in steps) for steps in tree.values())


def test_parameters_use_only_training_window_and_no_future_leakage():
    p = _panel()
    cfg = PrepConfig(target="hedef", train_end=date(2019, 12, 31))
    base = prepare(p, cfg)
    changed = p.copy()
    changed.loc["2020-01-31":] = changed.loc["2020-01-31":] * 50 + 1000  # eğitim sonrası veriyi boz
    after = prepare(changed, cfg)
    train = slice(None, "2019-12-31")
    pd.testing.assert_frame_equal(base.panel.loc[train], after.panel.loc[train])
    for a, b in zip(base.plans, after.plans):
        assert a.steps == b.steps  # λ, merkez ve ölçek yalnızca eğitimden


def test_missing_values_forward_filled_with_limit_and_flagged():
    p = _panel()
    p.iloc[30:32, 0] = np.nan  # 2 dönem: doldurulur
    p.iloc[50:55, 0] = np.nan  # 5 dönem: 2'si doldurulur, kalanı boş ve işaretli
    res = prepare(p, PrepConfig(train_end=date(2019, 12, 31), scaling="none", outliers="none", power="none"))
    col = res.panel["normal"]
    assert col.iloc[30:32].notna().all() and col.iloc[52:55].isna().all()
    assert res.panel["normal__eksik"].iloc[52:55].eq(1).all()


def test_outliers_are_clipped_causally_and_flagged():
    p = _panel()
    p.iloc[80, 0] = 40.0
    res = prepare(p, PrepConfig(train_end=date(2019, 12, 31), scaling="none", power="none"))
    assert res.panel["normal"].iloc[80] < 10 and res.panel["normal__aykiri"].iloc[80] == 1
    flagged = prepare(p, PrepConfig(train_end=date(2019, 12, 31), model_family="tree"))
    assert flagged.panel["normal"].iloc[80] == 40.0  # ağaç modelinde değer korunur


@pytest.mark.parametrize("kwargs", [{"model_family": "svm"}, {"scaling": "minmax"}, {"max_gap": 99}, {"outlier_k": 1}])
def test_config_validation(kwargs):
    with pytest.raises(InvalidInput):
        PrepConfig(**kwargs)


def test_train_end_must_be_inside_panel():
    with pytest.raises(InvalidInput, match="train_end"):
        prepare(_panel(), PrepConfig(train_end=date(2030, 1, 31)))
