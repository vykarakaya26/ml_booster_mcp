from datetime import date

import numpy as np
import pandas as pd
import pytest

from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Aggregation, Frequency, SeriesKind, Transform
from mlbooster.domain.services.chunking import date_chunks
from mlbooster.domain.services.deflation import deflate, index_from_levels, index_from_pct_changes
from mlbooster.domain.services.features import apply_transform
from mlbooster.domain.services.frequency import to_monthly


def test_daily_to_monthly_avg_and_drops_partial_month():
    idx = pd.bdate_range("2026-08-01", "2026-10-15")
    s = pd.Series(np.arange(len(idx), dtype=float), index=idx)
    m = to_monthly(s, Frequency.BUSINESS, Aggregation.AVG)
    assert list(m.index) == [pd.Timestamp("2026-08-31"), pd.Timestamp("2026-09-30")]
    assert m.iloc[0] == s["2026-08"].mean()


def test_daily_flow_sums_and_last():
    s = pd.Series(1.0, index=pd.date_range("2026-01-01", "2026-02-28"))
    assert to_monthly(s, Frequency.DAILY, kind=SeriesKind.FLOW).tolist() == [31.0, 28.0]
    assert to_monthly(s.cumsum(), Frequency.DAILY, Aggregation.LAST).tolist() == [31.0, 59.0]


def test_quarterly_ffill_with_publication_lag():
    s = pd.Series([10.0, 20.0], index=pd.to_datetime(["2025-12-31", "2026-03-31"]))
    m = to_monthly(s, Frequency.QUARTERLY, publication_lag_days=90)
    assert m.index[0] == pd.Timestamp("2026-03-31")  # Ç4, 90 gün sonra bilinir
    assert m.loc["2026-05-31"] == 10.0
    assert m.loc["2026-06-30"] == 20.0


def test_monthly_lag_shifts_to_next_month():
    s = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-08-31", "2026-09-30"]))
    m = to_monthly(s, Frequency.MONTHLY, publication_lag_days=3)
    assert list(m.index) == [pd.Timestamp("2026-09-30"), pd.Timestamp("2026-10-31")]


def test_chain_link_from_pct_changes():
    idx = index_from_pct_changes(pd.Series([10.0, 10.0], index=pd.to_datetime(["2026-01-31", "2026-02-28"])))
    assert idx.iloc[-1] == pytest.approx(100.0)
    assert idx.iloc[0] == pytest.approx(100 / 1.1)


def test_chain_link_across_base_break():
    dates = pd.to_datetime(["2025-11-30", "2025-12-31", "2026-01-31", "2026-02-28"])
    levels = pd.Series([3000.0, 3030.0, 104.0, 106.08], index=dates)  # Ocak 2026: 2003=100 → 2025=100
    idx = index_from_levels(levels, "2025-12-31", ["2026-01-31"], {"2026-01-31": 4.0})
    assert idx.loc["2026-01-31"] == pytest.approx(104.0)
    assert idx.loc["2026-02-28"] == pytest.approx(106.08)
    with pytest.raises(InvalidInput):
        index_from_levels(levels, break_dates=["2026-01-31"])


def test_deflate_and_transforms_fill_gaps():
    dates = pd.date_range("2025-01-31", periods=13, freq="ME")
    assert (deflate(pd.Series(200.0, index=dates), pd.Series(200.0, index=dates)) == 100.0).all()
    s = pd.Series(np.arange(1, 14, dtype=float), index=dates)
    assert apply_transform(s, Transform.YOY_PCT).iloc[-1] == pytest.approx(1200.0)
    assert apply_transform(s, Transform.LOG_DIFF).iloc[1] == pytest.approx(np.log(2))
    gappy = s.drop(dates[5])  # eksik ay: shift(12) yanlış aya kaymamalı
    assert apply_transform(gappy, Transform.YOY_PCT).iloc[-1] == pytest.approx(1200.0)
    assert apply_transform(pd.Series([0.0, 1.0], index=dates[:2]), Transform.LOG_DIFF).isna().all()


def test_date_chunks_cover_range_without_overlap():
    chunks = date_chunks(date(2020, 1, 1), date(2026, 10, 30), Frequency.BUSINESS, 1000)
    assert chunks[0][0] == date(2020, 1, 1) and chunks[-1][1] == date(2026, 10, 30)
    assert all((b[0] - a[1]).days == 1 for a, b in zip(chunks, chunks[1:]))
    assert all((e - s).days < 1000 for s, e in chunks)
    assert len(date_chunks(date(2000, 1, 1), date(2026, 1, 1), Frequency.MONTHLY, 1000)) == 1


def test_daily_lag_applied_before_aggregation():
    # 1 gün gecikmeli günlük seri: Eylül satırı Eylül içinde yayımlanan gözlemlerin ortalaması olmalı,
    # Ağustos'unki değil (eski hata: ortalama alıp sonra bütün ayı kaydırmak).
    idx = pd.bdate_range("2026-08-03", "2026-10-30")
    s = pd.Series(np.where(idx.month == 8, 10.0, np.where(idx.month == 9, 20.0, 30.0)), index=idx)
    m = to_monthly(s, Frequency.BUSINESS, publication_lag_days=1)
    assert m.loc["2026-09-30"] > 19.0  # yalnızca 31 Ağustos gözlemi (1 Eylül'de yayımlanan) karışır
    assert m.loc["2026-10-31"] > 29.0


def test_monthly_lag_never_collides_or_drops_months():
    # 30 günlük gecikme: Ocak+30g = 2 Mart, Şubat+30g = 30 Mart. Eski kod Ocak'ı kaybediyordu.
    idx = pd.date_range("2025-01-31", "2026-06-30", freq="ME")
    s = pd.Series(range(len(idx)), index=idx, dtype=float)
    m = to_monthly(s, Frequency.MONTHLY, publication_lag_days=30)
    assert len(m) == len(s) and m.is_unique  # hiçbir ay kaybolmadı
    assert m.loc["2025-03-31"] == 0.0  # Ocak değeri Mart'ta (yayımlandığı ay), Şubat'ta değil
    assert m.index.to_series().diff().dropna().dt.days.max() <= 31  # boşluk yok


def test_quarterly_lag_shift_is_exact():
    s = pd.Series([1.0, 2.0, 3.0, 4.0], index=pd.to_datetime(["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]))
    m = to_monthly(s, Frequency.QUARTERLY, publication_lag_days=90)
    assert m.loc["2025-06-30"] == 1.0 and m.loc["2026-03-31"] == 4.0  # 3 ay kayma, 4 değil


def test_to_frequency_weekly_and_daily_point_in_time():
    from mlbooster.domain.services.frequency import to_frequency

    days = pd.bdate_range("2026-09-01", "2026-10-30")
    daily = pd.Series(np.arange(len(days), dtype=float), index=days)
    weekly = to_frequency(daily, Frequency.BUSINESS, "W", Aggregation.LAST, publication_lag_days=1)
    assert all(d.weekday() == 4 for d in weekly.index)  # Cuma
    # 1 gün gecikme: Cuma satırı Perşembe'ye kadarki son değeri taşır (Cuma verisi Pazartesi yayımlanır)
    assert weekly.loc["2026-09-11"] == daily.loc["2026-09-10"]
    monthly = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-08-31", "2026-09-30"]))
    w = to_frequency(monthly, Frequency.MONTHLY, "W", publication_lag_days=3)
    assert w.loc["2026-09-04"] == 1.0  # Ağustos değeri 3 Eylül'de yayımlandı
    assert w.loc["2026-10-02"] == 1.0 and w.loc["2026-10-09"] == 2.0  # Eylül değeri 3 Ekim'de
    b = to_frequency(monthly, Frequency.MONTHLY, "B", publication_lag_days=3)
    assert b.loc["2026-10-02"] == 1.0 and b.loc["2026-10-05"] == 2.0
    with pytest.raises(ValueError):
        to_frequency(monthly, Frequency.MONTHLY, "X")


def test_yoy_uses_periods_per_year():
    idx = pd.date_range("2024-01-05", periods=60, freq="W-FRI")
    s = pd.Series(np.arange(1, 61, dtype=float), index=idx)
    assert apply_transform(s, Transform.YOY_PCT, "W").iloc[-1] == pytest.approx((60 / 8 - 1) * 100)
