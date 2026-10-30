import numpy as np
import pandas as pd
import pytest

from mlbooster.transform.deflate import apply_transform, deflate, index_from_levels, index_from_pct_changes
from mlbooster.transform.frequency import to_monthly


def test_daily_to_monthly_avg_and_drops_partial_month():
    idx = pd.bdate_range("2026-08-01", "2026-10-15")
    s = pd.Series(np.arange(len(idx), dtype=float), index=idx)
    m = to_monthly(s, "B", "avg")
    assert list(m.index) == [pd.Timestamp("2026-08-31"), pd.Timestamp("2026-09-30")]
    assert m.iloc[0] == s["2026-08"].mean()


def test_daily_flow_sums_and_last():
    idx = pd.date_range("2026-01-01", "2026-02-28")
    s = pd.Series(1.0, index=idx)
    assert to_monthly(s, "D", kind="flow").tolist() == [31.0, 28.0]
    assert to_monthly(s.cumsum(), "D", agg="last").tolist() == [31.0, 59.0]


def test_quarterly_ffill_with_publication_lag():
    s = pd.Series([10.0, 20.0], index=pd.to_datetime(["2025-12-31", "2026-03-31"]))
    m = to_monthly(s, "Q", publication_lag_days=90)
    # Ç4 değeri 90 gün sonra (Mart sonu) bilinir; Ç1 değeri Haziran sonunda.
    assert m.index[0] == pd.Timestamp("2026-03-31")
    assert m.loc["2026-05-31"] == 10.0
    assert m.loc["2026-06-30"] == 20.0
    assert pd.Timestamp("2025-12-31") not in m.index


def test_monthly_lag_shifts_to_next_month():
    s = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-08-31", "2026-09-30"]))
    m = to_monthly(s, "M", publication_lag_days=3)
    assert list(m.index) == [pd.Timestamp("2026-09-30"), pd.Timestamp("2026-10-31")]


def test_chain_link_from_pct_changes():
    pct = pd.Series([10.0, 10.0], index=pd.to_datetime(["2026-01-31", "2026-02-28"]))
    idx = index_from_pct_changes(pct)
    assert idx.iloc[-1] == pytest.approx(100.0)
    assert idx.iloc[0] == pytest.approx(100 / 1.1)


def test_chain_link_across_base_break():
    # Ocak 2026'da baz 2003=100 → 2025=100 değişiyor; seviye oranı anlamsız, resmi % değişim kullanılır.
    dates = pd.to_datetime(["2025-11-30", "2025-12-31", "2026-01-31", "2026-02-28"])
    levels = pd.Series([3000.0, 3030.0, 104.0, 106.08], index=dates)
    idx = index_from_levels(levels, ref_period="2025-12-31", break_dates=["2026-01-31"], pct_at_breaks={"2026-01-31": 4.0})
    assert idx.loc["2026-01-31"] == pytest.approx(104.0)
    assert idx.loc["2026-02-28"] == pytest.approx(106.08)
    with pytest.raises(ValueError):
        index_from_levels(levels, break_dates=["2026-01-31"])


def test_deflate_and_transforms():
    dates = pd.date_range("2025-01-31", periods=13, freq="ME")
    nominal = pd.Series(200.0, index=dates)
    cpi = pd.Series(200.0, index=dates)
    assert (deflate(nominal, cpi) == 100.0).all()
    s = pd.Series(np.arange(1, 14, dtype=float), index=dates)
    assert apply_transform(s, "yoy_pct").iloc[-1] == pytest.approx(1200.0)
    assert apply_transform(s, "log_diff").iloc[1] == pytest.approx(np.log(2))
