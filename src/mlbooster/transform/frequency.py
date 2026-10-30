"""Frekans hizalama: her şey aylık dönem sonu takvimine indirgenir.

Nokta-zamanlı (point-in-time) kural: bir gözlem, dönem sonu + publication_lag_days tarihine
denk gelen ayın satırına yazılır. Böylece t ayının satırında yalnızca t sonunda bilinen veri olur.
"""

from __future__ import annotations

import pandas as pd

HIGH_FREQ = {"D", "B", "W", "SM"}
LOW_FREQ = {"Q", "S", "A"}
_AGG = {"avg": "mean", "last": "last", "sum": "sum"}


def _month_end(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return index.to_period("M").to_timestamp(how="end").normalize()


def to_monthly(
    s: pd.Series,
    freq: str,
    agg: str | None = None,
    kind: str = "level",
    publication_lag_days: int = 0,
) -> pd.Series:
    """Tarih indeksli seriyi aylık dönem sonu serisine çevirir.

    Günlük→aylık: seviye için `avg` (varsayılan) veya `last`, akım (`kind='flow'`) için `sum`.
    Çeyreklik/yıllık→aylık: yayın gecikmesine göre kaydırılıp yalnızca ileriye doldurulur.
    """
    s = s.dropna().sort_index()
    if s.empty:
        return s
    s.index = pd.DatetimeIndex(s.index)

    if freq in HIGH_FREQ:
        how = _AGG[agg or ("sum" if kind == "flow" else "avg")]
        monthly = s.resample("ME").agg(how)
        # Günlük seride son ay eksikse (son gözlem ayın son işgününden önce) yarım ayı at.
        last_obs = s.index.max()
        last_bday = pd.offsets.BMonthEnd().rollback(last_obs + pd.offsets.MonthEnd(0))
        if freq in {"D", "B"} and last_obs < last_bday.normalize():
            monthly = monthly.iloc[:-1]
    else:
        monthly = s.copy()
        monthly.index = _month_end(monthly.index)

    if publication_lag_days:
        available = monthly.index + pd.Timedelta(days=publication_lag_days)
        monthly.index = _month_end(pd.DatetimeIndex(available))
        monthly = monthly[~monthly.index.duplicated(keep="last")]

    monthly = monthly.dropna()
    if freq in LOW_FREQ and len(monthly) > 0:
        full = pd.date_range(monthly.index.min(), monthly.index.max(), freq="ME")
        monthly = monthly.reindex(full).ffill()
    monthly.index.name = "date"
    return monthly
