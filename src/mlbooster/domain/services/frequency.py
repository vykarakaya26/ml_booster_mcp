"""Frequency alignment: everything is converted to a month-end calendar.

Point-in-time rule: an observation is written to the row of the month in which its publication date
(`observation/period end + publication_lag_days`) falls. For high-frequency series this shift is applied
to each observation before aggregation; for low-frequency series, to the period end. The row for month t
therefore contains only data known at the end of t (no look-ahead).
"""

from __future__ import annotations

import pandas as pd

from mlbooster.domain.model import Aggregation, Frequency, SeriesKind

_PANDAS_AGG = {
    Aggregation.AVG: "mean", Aggregation.LAST: "last", Aggregation.SUM: "sum",
    Aggregation.MIN: "min", Aggregation.MAX: "max", Aggregation.FIRST: "first",
}  # fmt: skip


def _month_end(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return index.to_period("M").to_timestamp(how="end").normalize()


def to_monthly(
    s: pd.Series,
    freq: Frequency,
    agg: Aggregation | None = None,
    kind: SeriesKind = SeriesKind.LEVEL,
    publication_lag_days: int = 0,
) -> pd.Series:
    """Converts a date-indexed series to a month-end series.

    Higher→monthly: `avg` (default) for levels, `sum` for flows. For daily series an incomplete last
    month is dropped.
    Lower→monthly: shifted by the publication lag and only forward-filled.
    """
    s = s.dropna().sort_index()
    if s.empty:
        return s
    s.index = pd.DatetimeIndex(s.index)

    if freq.is_high:
        # The lag is applied to each observation BEFORE aggregation: the row for month t summarises
        # the observations published during t. (Shifting after aggregation would show intra-month data a month late.)
        if publication_lag_days:
            s.index = s.index + pd.Timedelta(days=publication_lag_days)
        default = Aggregation.SUM if kind is SeriesKind.FLOW else Aggregation.AVG
        monthly = s.resample("ME").agg(_PANDAS_AGG[agg or default])
        last_obs = s.index.max()
        last_bday = pd.offsets.BMonthEnd().rollback(last_obs + pd.offsets.MonthEnd(0))
        if freq in {Frequency.DAILY, Frequency.BUSINESS} and last_obs < last_bday.normalize():
            monthly = monthly.iloc[:-1]
    else:
        monthly = s.copy()
        monthly.index = _month_end(monthly.index)

    if publication_lag_days and not freq.is_high:
        # For low-frequency series the lag is a FIXED month shift: the largest distance between an
        # observation and its publication month. Shifting per observation would map two periods to the
        # same month because month lengths differ (Jan+30d = 2 Mar, Feb+30d = 30 Mar → Jan is lost). Taking
        # the largest guarantees that no value appears before it is published.
        available = _month_end(pd.DatetimeIndex(monthly.index + pd.Timedelta(days=publication_lag_days)))
        shift = int(max((a.to_period("M") - p.to_period("M")).n for a, p in zip(available, monthly.index)))
        monthly.index = _month_end(monthly.index + pd.offsets.MonthEnd(shift)) if shift else monthly.index

    monthly = monthly.dropna()
    if freq.is_low and len(monthly) > 0:
        full = pd.date_range(monthly.index.min(), monthly.index.max(), freq="ME")
        monthly = monthly.reindex(full).ffill()
    monthly.index.name = "date"
    return monthly


# --- Weekly / business-day panel --------------------------------------------------------------

PANEL_FREQS = {"M": "ME", "W": "W-FRI", "B": "B"}
PERIODS_PER_YEAR = {"M": 12, "W": 52, "B": 252}


def to_frequency(
    s: pd.Series,
    freq: Frequency,
    target: str = "M",
    agg: Aggregation | None = None,
    kind: SeriesKind = SeriesKind.LEVEL,
    publication_lag_days: int = 0,
) -> pd.Series:
    """Moves the series point-in-time to the target panel frequency (M monthly, W weekly-Friday, B business day).

    Each observation is first shifted to its publication date (observation/period end + lag). Series with a
    higher frequency than the target are aggregated into target periods by that date; lower-frequency series
    are only forward-filled from their publication date. A row therefore contains only data published by its date."""
    if target == "M":
        return to_monthly(s, freq, agg, kind, publication_lag_days)
    if target not in PANEL_FREQS:
        raise ValueError(f"Panel frekansı şunlardan biri olmalıdır: {list(PANEL_FREQS)} (verilen: {target})")
    s = s.dropna().sort_index()
    if s.empty:
        return s
    rule = PANEL_FREQS[target]
    s.index = pd.DatetimeIndex(s.index) + pd.Timedelta(days=publication_lag_days)  # publication date
    finer = (target == "W" and freq in {Frequency.DAILY, Frequency.BUSINESS, Frequency.HOURLY}) or (
        target == "B" and freq is Frequency.HOURLY
    )
    if finer:
        default = Aggregation.SUM if kind is SeriesKind.FLOW else Aggregation.AVG
        out = s.resample(rule).agg(_PANDAS_AGG[agg or default])
        last = s.index.max()
        if target == "W" and last < (last + pd.offsets.Week(weekday=4, n=0)).normalize():
            out = out.iloc[:-1]  # incomplete last week
    else:
        # Same or lower frequency: forward-fill from the publication date (as-of join).
        # The last value is valid until the next period is published: forward-fill horizon ≈ one period.
        horizon = pd.Timedelta(days=max(7, int(freq.min_period_days * 1.15)))
        grid = pd.date_range(s.index.min().normalize(), s.index.max().normalize() + horizon, freq=rule)
        s = s[~s.index.duplicated(keep="last")]
        out = s.reindex(s.index.union(grid)).ffill().reindex(grid)
    out = out.dropna()
    out.index.name = "date"
    return out
