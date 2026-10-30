from datetime import date

import numpy as np
import pandas as pd
import pytest

from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.services.backtest import BacktestConfig, rolling_origin_backtest
from mlbooster.domain.services.quality import Severity, audit_panel

IDX = pd.date_range("2015-01-31", "2026-09-30", freq="ME")
RNG = np.random.default_rng(0)


def _panel():
    n = len(IDX)
    trend = np.linspace(0, 10, n) + RNG.normal(0, 0.3, n)
    shifted = RNG.normal(0, 1, n) + np.where(IDX >= "2021-12-31", 8.0, 0.0)  # Aralık 2021 şoku
    spiky = RNG.normal(0, 1, n)
    spiky[50] = 40
    stale = pd.Series(RNG.normal(0, 1, n), index=IDX)
    stale.loc["2026-01-31":] = np.nan
    return pd.DataFrame(
        {"trend": trend, "shifted": shifted, "spiky": spiky, "stale": stale, "twin": trend * 2 + 1}, index=IDX
    )


def test_audit_flags_expected_issues():
    report = audit_panel(_panel(), date(2026, 10, 30))
    by = {(i.feature, i.check): i for i in report.issues}
    assert by[("stale", "stale")].severity is Severity.HIGH
    assert ("spiky", "outliers") in by
    brk = by[("shifted", "structural_break")]
    assert "2021" in brk.message and "kur şoku" in brk.message and brk.severity is Severity.INFO
    assert ("trend", "twin", 1.0) in report.correlated_pairs
    assert report.counts["high"] >= 1
    assert report.issues[0].severity is Severity.HIGH  # önem sırasına göre


def test_audit_handles_empty_and_constant():
    panel = pd.DataFrame({"empty": np.nan, "flat": 1.0}, index=IDX)
    checks = {(i.feature, i.check) for i in audit_panel(panel, date(2026, 10, 30)).issues}
    assert ("empty", "empty") in checks and ("flat", "constant") in checks


def test_backtest_ranks_models_and_has_no_lookahead():
    panel = _panel()[["trend", "shifted"]]
    result = rolling_origin_backtest(panel, BacktestConfig(target="trend", horizon=3, min_train=36))
    assert set(result.metrics["model"]) == {"naive", "seasonal_naive", "drift", "ridge"}
    assert result.best_model in {"drift", "ridge"}  # trendli seride naive'den iyi olmalı
    p = result.predictions
    assert ((p["date"] - p["origin"]).dt.days > 0).all()
    # Her tahmin noktası için hedef, başlangıçtan tam horizon ay sonrası.
    months = (p["date"].dt.year - p["origin"].dt.year) * 12 + (p["date"].dt.month - p["origin"].dt.month)
    assert (months == 3).all()


def test_backtest_future_values_do_not_change_past_predictions():
    panel = _panel()[["trend", "shifted"]]
    config = BacktestConfig(target="trend", horizon=1, min_train=36, models=("ridge",))
    base = rolling_origin_backtest(panel, config).predictions
    tampered = panel.copy()
    tampered.loc["2024-01-31":, :] = 1e6  # gelecek verisini boz
    after = rolling_origin_backtest(tampered, config).predictions
    early = base["date"] < pd.Timestamp("2024-01-31")
    merged = base[early].merge(after, on=["origin", "date", "model"], suffixes=("", "_t"))
    assert np.allclose(merged["y_pred"], merged["y_pred_t"])


@pytest.mark.parametrize("kwargs", [{"horizon": 0}, {"min_train": 10}, {"models": ("xgb",)}, {"lags": (-1,)}])
def test_backtest_config_validation(kwargs):
    with pytest.raises(InvalidInput):
        BacktestConfig(target="x", **kwargs)


def test_backtest_unknown_target():
    with pytest.raises(InvalidInput, match="Hedef"):
        rolling_origin_backtest(_panel(), BacktestConfig(target="nope"))


def test_metrics_use_common_origins():
    panel = _panel()[["trend", "shifted"]].copy()
    panel.loc[:"2019-12-31", "shifted"] = np.nan  # ridge için exogenous geç başlıyor
    result = rolling_origin_backtest(panel, BacktestConfig(target="trend", min_train=36))
    assert result.metrics["n"].nunique() == 1  # hepsi aynı noktalarda ölçüldü
    assert result.metrics["n"].iloc[0] == result.common_origins
    assert result.predictions.groupby("model").size()["naive"] > result.common_origins


def test_anchor_naive_model():
    panel = _panel()[["trend"]].copy()
    panel["trend_son"] = panel["trend"].shift(-1)  # hedefin "geleceğe yakın" versiyonu (ay sonu değeri gibi)
    result = rolling_origin_backtest(panel.iloc[:-1], BacktestConfig(target="trend", min_train=36, anchor="trend_son"))
    assert "anchor_naive" in set(result.metrics["model"])
    assert result.best_model == "anchor_naive"
    with pytest.raises(InvalidInput):
        BacktestConfig(target="x", anchor="x")
