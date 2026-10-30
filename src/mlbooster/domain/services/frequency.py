"""Frekans hizalama: her şey aylık dönem sonu takvimine indirgenir.

Nokta-zamanlı kural: bir gözlem, `dönem sonu + publication_lag_days` tarihinin düştüğü ayın
satırına yazılır. Böylece t ayının satırında yalnızca t sonunda bilinen veri olur (look-ahead yok).
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
    """Tarih indeksli seriyi aylık dönem sonu serisine çevirir.

    Sık→aylık: seviye için `avg` (varsayılan), akım için `sum`. Günlük seride eksik son ay atılır.
    Seyrek→aylık: yayın gecikmesine göre kaydırılıp yalnızca ileriye doldurulur.
    """
    s = s.dropna().sort_index()
    if s.empty:
        return s
    s.index = pd.DatetimeIndex(s.index)

    if freq.is_high:
        default = Aggregation.SUM if kind is SeriesKind.FLOW else Aggregation.AVG
        monthly = s.resample("ME").agg(_PANDAS_AGG[agg or default])
        last_obs = s.index.max()
        last_bday = pd.offsets.BMonthEnd().rollback(last_obs + pd.offsets.MonthEnd(0))
        if freq in {Frequency.DAILY, Frequency.BUSINESS} and last_obs < last_bday.normalize():
            monthly = monthly.iloc[:-1]
    else:
        monthly = s.copy()
        monthly.index = _month_end(monthly.index)

    if publication_lag_days:
        available = monthly.index + pd.Timedelta(days=publication_lag_days)
        monthly.index = _month_end(pd.DatetimeIndex(available))
        monthly = monthly[~monthly.index.duplicated(keep="last")]

    monthly = monthly.dropna()
    if freq.is_low and len(monthly) > 0:
        full = pd.date_range(monthly.index.min(), monthly.index.max(), freq="ME")
        monthly = monthly.reindex(full).ffill()
    monthly.index.name = "date"
    return monthly
