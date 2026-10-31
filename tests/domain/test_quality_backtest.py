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
    from mlbooster.domain.services.backtest import MODELS

    assert set(result.metrics["model"]) == set(MODELS)
    assert result.best_model in {"drift", "drift_recent", "ridge", "qr", "combo_mean", "combo_median", "auto_robust"}
    p = result.predictions
    assert ((p["date"] - p["origin"]).dt.days > 0).all()
    # Her tahmin noktası için hedef, başlangıçtan tam horizon ay sonrası.
    months = (p["date"].dt.year - p["origin"].dt.year) * 12 + (p["date"].dt.month - p["origin"].dt.month)
    assert (months == 3).all()


def test_backtest_future_values_do_not_change_past_predictions():
    panel = _panel()[["trend", "shifted"]]
    config = BacktestConfig(target="trend", horizon=1, min_train=36, models=("ridge", "qr", "combo_median", "auto_robust"))
    base = rolling_origin_backtest(panel, config).predictions
    tampered = panel.copy()
    tampered.loc["2024-01-31":, :] = 1e6  # gelecek verisini boz
    after = rolling_origin_backtest(tampered, config).predictions
    early = base["date"] < pd.Timestamp("2024-01-31")
    merged = base[early].merge(after, on=["origin", "date", "model"], suffixes=("", "_t"))
    assert np.allclose(merged["y_pred"], merged["y_pred_t"])
    both = merged.dropna(subset=["lower", "lower_t"])
    assert np.allclose(both["lower"], both["lower_t"]) and np.allclose(both["upper"], both["upper_t"])


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


def test_distribution_diagnostics_classify_stationarity_and_tails():
    from mlbooster.domain.services.distribution import diagnose

    rng = np.random.default_rng(42)
    idx = pd.date_range("1975-01-31", periods=600, freq="ME")
    walk = pd.Series(np.cumsum(rng.normal(0, 1, 600)) + 500, index=idx)
    noise = pd.Series(rng.normal(0, 1, 600), index=idx)
    tails = pd.Series(rng.standard_t(2, 600), index=idx)
    w = diagnose(walk, "walk")
    assert w.stationarity == "birim_kök" and w.adf_t > -2.86 and w.kpss_stat > 0.463
    assert any("log-fark" in r for r in w.recommendations)
    assert diagnose(walk, "walk", transform="log_diff").recommendations == ()  # zaten dönüştürülmüş
    assert diagnose(noise, "noise").stationarity == "durağan"
    d = diagnose(tails, "tails")
    assert d.excess_kurtosis > 3 and any("Kalın kuyruk" in r for r in d.recommendations)
    assert diagnose(pd.Series([1.0, 2.0]), "kisa").stationarity == "kararsız"


def test_companion_pairs_not_flagged_as_collinear():
    panel = _panel()[["trend", "twin"]]
    plain = audit_panel(panel, date(2026, 10, 30))
    assert plain.correlated_pairs
    skipped = audit_panel(panel, date(2026, 10, 30), companions={frozenset(("trend", "twin"))})
    assert not skipped.correlated_pairs
    assert {d.feature for d in skipped.diagnostics} == {"trend", "twin"}


def test_adf_critical_values_match_mackinnon():
    from mlbooster.domain.services.distribution import adf_critical

    assert adf_critical(10_000) == pytest.approx(-2.8618, abs=1e-3)  # asimptotiğe yakın
    assert adf_critical(100) < adf_critical(1000)  # küçük örneklemde daha negatif


def test_regime_metrics_and_notable_pairs():
    panel = _panel()[["trend", "shifted"]]
    result = rolling_origin_backtest(panel, BacktestConfig(target="trend", min_train=36, models=("naive",)))
    regimes = set(result.regime_metrics["regime"])
    assert regimes == {"2021-11 ve öncesi", "2021-12 – 2023", "2024 ve sonrası"}
    assert result.regime_metrics["n"].sum() == result.metrics["n"].sum()
    p = pd.DataFrame({"a": np.arange(100.0), "b": np.arange(100.0) + np.random.default_rng(3).normal(0, 14, 100)}, index=IDX[:100])
    report = audit_panel(p, date(2026, 10, 30))
    assert report.notable_pairs and not report.correlated_pairs


def test_quantile_regression_recovers_sample_quantiles():
    from mlbooster.domain.services.backtest import quantile_fit_predict

    rng = np.random.default_rng(7)
    y = rng.standard_t(3, 400)
    x = np.zeros((400, 1))  # bilgi taşımayan feature: çözüm sabit = örneklem kantili
    for tau in (0.1, 0.5, 0.9):
        assert quantile_fit_predict(x, y, np.zeros(1), tau) == pytest.approx(np.quantile(y, tau), abs=0.05)
    # Eğim: y = 2x + gürültü → medyan eğimi ~2
    xs = rng.normal(0, 1, (400, 1))
    ys = 2 * xs[:, 0] + rng.normal(0, 0.5, 400)
    assert quantile_fit_predict(xs, ys, np.array([1.0]), 0.5, alpha=0.0) == pytest.approx(2.0, abs=0.15)


def test_intervals_and_quantile_model_are_reported():
    panel = _panel()[["trend", "shifted"]]
    result = rolling_origin_backtest(panel, BacktestConfig(target="trend", min_train=36))
    m = result.metrics.set_index("model")
    assert m.loc["qr", "n_interval"] > 0 and 0.4 <= m.loc["qr", "coverage80"] <= 1.0
    assert m.loc["naive", "n_interval"] > 0  # ampirik aralık
    p = result.predictions.dropna(subset=["lower", "upper"])
    assert (p["lower"] <= p["upper"]).all()


def test_auto_robust_prefers_median_on_heavy_tails():
    rng = np.random.default_rng(11)
    heavy = pd.Series(np.cumsum(rng.standard_t(1.5, len(IDX))), index=IDX)
    result = rolling_origin_backtest(heavy.to_frame("x"), BacktestConfig(target="x", min_train=36, models=("naive", "mean12", "drift", "auto_robust")))
    assert result.robust_share is not None and result.robust_share > 0.8


def test_drift_recent_tracks_managed_crawl():
    # Kontrollü kur: her ay ~%2 sabit değer kaybı + küçük gürültü → son eğim naive'i yener
    rng = np.random.default_rng(5)
    level = pd.Series(np.exp(np.cumsum(0.02 + rng.normal(0, 0.002, len(IDX)))), index=IDX)
    result = rolling_origin_backtest(level.to_frame("usdtry"), BacktestConfig(target="usdtry", min_train=36, models=("naive", "drift_recent")))
    m = result.metrics.set_index("model")
    assert m.loc["drift_recent", "mae"] < 0.5 * m.loc["naive", "mae"]


def test_managed_path_detected_for_crawling_fx():
    from mlbooster.domain.services.distribution import diagnose

    rng = np.random.default_rng(9)
    n = len(IDX)
    early = rng.normal(0.02, 0.05, n - 36)  # serbest dönem: oynak
    late = rng.normal(0.02, 0.002, 36)  # yönetilen dönem: sabit hızlı değer kaybı
    s = pd.Series(np.concatenate([early, late]), index=IDX)
    assert diagnose(s, "usdtry_logdiff", "log_diff").managed_path
    assert not diagnose(pd.Series(rng.normal(0.02, 0.05, n), index=IDX), "x", "log_diff").managed_path
    assert not diagnose(s, "level_series", "level").managed_path


def test_late_starting_feature_does_not_delay_ridge():
    panel = _panel()[["trend", "shifted"]].copy()
    panel["late"] = panel["shifted"]
    panel.loc[:"2018-12-31", "late"] = np.nan  # 4 yıl geç başlayan feature
    result = rolling_origin_backtest(panel, BacktestConfig(target="trend", min_train=36, models=("naive", "ridge")))
    counts = result.predictions.groupby("model").size()
    assert counts["ridge"] == counts["naive"]
