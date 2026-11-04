from datetime import date

import numpy as np
import pandas as pd
import pytest

from mlbooster.domain.services.quality import Severity, audit_panel

IDX = pd.date_range("2015-01-31", "2026-09-30", freq="ME")
RNG = np.random.default_rng(0)


def _panel():
    n = len(IDX)
    trend = np.linspace(0, 10, n) + RNG.normal(0, 0.3, n)
    shifted = RNG.normal(0, 1, n) + np.where(IDX >= "2021-12-31", 8.0, 0.0)  # December 2021 shock
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
    assert report.issues[0].severity is Severity.HIGH  # ordered by severity


def test_audit_handles_empty_and_constant():
    panel = pd.DataFrame({"empty": np.nan, "flat": 1.0}, index=IDX)
    checks = {(i.feature, i.check) for i in audit_panel(panel, date(2026, 10, 30)).issues}
    assert ("empty", "empty") in checks and ("flat", "constant") in checks


def test_distribution_diagnostics_classify_stationarity_and_tails():
    from mlbooster.domain.services.distribution import diagnose

    rng = np.random.default_rng(42)
    idx = pd.date_range("1975-01-31", periods=600, freq="ME")
    walk = pd.Series(np.cumsum(rng.normal(0, 1, 600)) + 500, index=idx)
    noise = pd.Series(rng.normal(0, 1, 600), index=idx)
    tails = pd.Series(rng.standard_t(2, 600), index=idx)
    w = diagnose(walk, "walk")
    assert w.stationarity == "unit_root" and w.adf_t > -2.86 and w.kpss_stat > 0.463
    assert any("log farkı" in r for r in w.recommendations)
    assert diagnose(walk, "walk", transform="log_diff").recommendations == ()  # already transformed
    assert diagnose(noise, "noise").stationarity == "stationary"
    d = diagnose(tails, "tails")
    assert d.excess_kurtosis > 3 and any("Heavy tail" in r for r in d.recommendations)
    assert diagnose(pd.Series([1.0, 2.0]), "short").stationarity == "inconclusive"


def test_companion_pairs_not_flagged_as_collinear():
    panel = _panel()[["trend", "twin"]]
    plain = audit_panel(panel, date(2026, 10, 30))
    assert plain.correlated_pairs
    skipped = audit_panel(panel, date(2026, 10, 30), companions={frozenset(("trend", "twin"))})
    assert not skipped.correlated_pairs
    assert {d.feature for d in skipped.diagnostics} == {"trend", "twin"}


def test_adf_critical_values_match_mackinnon():
    from mlbooster.domain.services.distribution import adf_critical

    assert adf_critical(10_000) == pytest.approx(-2.8618, abs=1e-3)  # close to the asymptotic value
    assert adf_critical(100) < adf_critical(1000)  # more negative in small samples


def test_notable_pairs():
    p = pd.DataFrame({"a": np.arange(100.0), "b": np.arange(100.0) + np.random.default_rng(3).normal(0, 14, 100)}, index=IDX[:100])
    report = audit_panel(p, date(2026, 10, 30))
    assert report.notable_pairs and not report.correlated_pairs


def test_managed_path_detected_for_crawling_fx():
    from mlbooster.domain.services.distribution import diagnose

    rng = np.random.default_rng(9)
    n = len(IDX)
    early = rng.normal(0.02, 0.05, n - 36)  # free-floating period: volatile
    late = rng.normal(0.02, 0.002, 36)  # managed period: constant-pace depreciation
    s = pd.Series(np.concatenate([early, late]), index=IDX)
    assert diagnose(s, "usdtry_logdiff", "log_diff").managed_path
    assert not diagnose(pd.Series(rng.normal(0.02, 0.05, n), index=IDX), "x", "log_diff").managed_path
    assert not diagnose(s, "level_series", "level").managed_path
