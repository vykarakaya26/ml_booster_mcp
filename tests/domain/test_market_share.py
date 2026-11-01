import numpy as np
import pandas as pd
import pytest

from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.services.market_share import (
    ShareConfig,
    analyze,
    compute_shares,
    concentration,
    driver_correlations,
    hhi_class,
    parse_period,
    prepare,
    trend_label,
)


def _monthly(players: dict[str, list[float]], start="2022-01", total: list[float] | None = None, segment=None) -> pd.DataFrame:
    months = pd.period_range(start, periods=len(next(iter(players.values()))), freq="M").astype(str)
    rows = [{"entity": e, "date": m, "value": v} for e, vals in players.items() for m, v in zip(months, vals, strict=True)]
    if total is not None:
        rows += [{"entity": "TOPLAM", "date": m, "value": v} for m, v in zip(months, total, strict=True)]
    df = pd.DataFrame(rows)
    if segment:
        df["segment"] = segment
    return df


def test_parse_period_formats():
    assert str(parse_period("2025-03")) == "2025-03-31"
    assert str(parse_period("2025-Q2")) == "2025-06-30"
    assert str(parse_period("2024")) == "2024-12-31"
    assert str(parse_period("2025-10-17")) == "2025-10-17"
    with pytest.raises(InvalidInput, match="Dönem biçimi tanınmadı"):
        parse_period("Mart 2025")


def test_shares_coverage_and_hhi_against_hand_calculation():
    df = _monthly({"A": [50] * 3, "B": [30] * 3}, total=[100] * 3)
    cfg = ShareConfig(kind="stock")
    prep = prepare(df, cfg)
    tables = compute_shares(prep, cfg)
    assert tables.basis == "period"
    assert tables.shares.iloc[-1].to_dict() == {"A": 50.0, "B": 30.0}
    assert tables.market["coverage"].iloc[-1] == 80.0
    conc = concentration(tables, prep.periods_per_year).iloc[-1]
    assert conc["hhi"] == 50**2 + 30**2
    assert conc["hhi_upper"] == 50**2 + 30**2 + 20**2  # if the unlisted 20% were a single entity
    assert conc["cr1"] == 50 and hhi_class(conc["hhi"]) == "high"


@pytest.mark.parametrize("marker", ["TOPLAM", "toplam", "TOTAL", "Total"])
def test_total_rows_accept_turkish_and_english_markers(marker):
    df = _monthly({"A": [50] * 3, "B": [30] * 3}, total=[100] * 3)
    df.loc[df["entity"] == "TOPLAM", "entity"] = marker
    cfg = ShareConfig(kind="stock")
    tables = compute_shares(prepare(df, cfg), cfg)
    assert list(tables.shares.columns) == ["A", "B"]
    assert tables.market["coverage"].iloc[-1] == 80.0


def test_monthly_flow_uses_trailing_twelve_months():
    rng = np.random.default_rng(0)
    df = _monthly({"A": list(60 + rng.normal(0, 5, 30)), "B": list(40 + rng.normal(0, 5, 30))})
    out = analyze(df, ShareConfig())
    assert out["meta"]["basis"] == "ttm" and out["meta"]["first"] == "2022-12-31"
    shares = [p["share"] for p in out["players"]]
    assert sum(shares) == pytest.approx(100.0, abs=1e-3)
    assert any(i["check"] == "coverage_unknown" for i in out["issues"])


def test_share_measure_accepts_fractions_and_rejects_over_100():
    df = _monthly({"A": [0.6] * 4, "B": [0.3] * 4})
    out = analyze(df, ShareConfig(measure="share", kind="stock"))
    assert out["players"][0]["share"] == 60.0
    assert any(i["check"] == "share_scale" for i in out["issues"])
    with pytest.raises(InvalidInput, match="%100"):
        analyze(_monthly({"A": [70] * 4, "B": [40] * 4}), ShareConfig(measure="share", kind="stock"))


@pytest.mark.parametrize(
    ("frame", "match"),
    [
        (pd.DataFrame({"entity": ["A"], "date": ["2025-01"]}), "eksik kolon"),
        (pd.DataFrame({"entity": ["A", "A", "B"], "date": ["2025-01"] * 3, "value": [1, 2, 3]}), "birden fazla satır"),
        (pd.DataFrame({"entity": ["A", "B"], "date": ["2025-01"] * 2, "value": [1, -2]}), "Negatif değer"),
        (pd.DataFrame({"entity": ["A", "A"], "date": ["2025-01", "2025-02"], "value": [1, 2]}), "en az iki oyuncu"),
        (pd.DataFrame({"entity": ["A" * 81, "B"], "date": ["2025-01"] * 2, "value": [1, 2]}), "en fazla 80"),
    ],
)
def test_input_validation(frame, match):
    with pytest.raises(InvalidInput, match=match):
        analyze(frame, ShareConfig())


def test_turkish_column_aliases_and_unknown_focus():
    df = pd.DataFrame({"Şirket": ["A", "B"], "Dönem": ["2025-Q1"] * 2, "Değer": [3, 1]})
    assert analyze(df, ShareConfig(kind="stock"))["meta"]["periods_per_year"] == 1
    with pytest.raises(InvalidInput, match="Odak oyuncu"):
        analyze(df, ShareConfig(focus="Z", kind="stock"))


def test_trend_label_requires_significance_and_materiality():
    assert trend_label(None, None) == "insufficient_data"
    assert trend_label(1.0, 5.0) == "gaining"
    assert trend_label(-1.0, -5.0) == "losing"
    assert trend_label(1.0, 1.0) == "flat"
    assert trend_label(0.05, 30.0) == "flat"  # statistically significant but economically immaterial


def test_substitution_identifies_mirror_competitor():
    rng = np.random.default_rng(1)
    n = 48
    shock = rng.normal(0, 2, n)
    noise = rng.normal(0, 1, (n, 2))
    df = _monthly({
        "Focus": list(30 + shock), "Rival": list(30 - shock + noise[:, 0] * 0.2),
        "Independent": list(20 + noise[:, 1]), "Leader": [60.0] * n,
    })  # fmt: skip
    out = analyze(df, ShareConfig(focus="Focus", kind="stock"))
    subst = {r["entity"]: r for r in out["competitors"]["substitution"]}
    assert subst["Rival"]["r"] < -0.9 and subst["Rival"]["significant"]
    assert not subst["Independent"]["significant"]
    cands = {c["entity"]: c["reasons"] for c in out["competitors"]["candidates"]}
    assert any("ters yönde" in r for r in cands["Rival"])
    assert "pazar lideri" in cands["Leader"]


def test_shift_share_reconciles_with_focus_share_change():
    rng = np.random.default_rng(2)
    n = 30
    frames = []
    for seg, size in (("S1", 100), ("S2", 40)):
        frames.append(_monthly({
            "A": list(size * 0.5 + rng.normal(0, 3, n) + np.arange(n) * 0.3),
            "B": list(size * 0.3 + rng.normal(0, 3, n)),
        }, total=list(size * 1.0 + np.arange(n) * (0.8 if seg == "S1" else -0.2)), segment=seg))  # fmt: skip
    out = analyze(pd.concat(frames, ignore_index=True), ShareConfig(focus="A"))
    ss = out["segments"]["shift_share"]
    d_year = next(p["d_year_pp"] for p in out["players"] if p["entity"] == "A")
    assert ss["share_effect_pp"] + ss["mix_effect_pp"] == pytest.approx(ss["total_pp"], abs=1e-3)
    assert ss["total_pp"] == pytest.approx(d_year, abs=1e-2)


def test_segments_require_value_measure():
    df = _monthly({"A": [60] * 3, "B": [40] * 3}, segment="S1")
    with pytest.raises(InvalidInput, match="Segment"):
        analyze(df, ShareConfig(measure="share"))


def test_driver_correlation_finds_lagged_relation():
    idx = pd.date_range("2018-01-31", periods=60, freq="ME")
    x = pd.Series(np.sin(np.arange(60) / 4) + np.arange(60) * 0.01, index=idx)
    panel = pd.DataFrame({"driver": x, "noise": np.random.default_rng(3).normal(size=60)}, index=idx)
    growth = x.shift(2).dropna()
    best = driver_correlations(growth, panel, ppy=12, max_lag=3)
    assert best[0]["feature"] == "driver" and best[0]["lag"] == 2 and best[0]["r"] == pytest.approx(1.0)
