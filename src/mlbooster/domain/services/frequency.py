"""Frekans hizalama: her şey aylık dönem sonu takvimine indirgenir.

Nokta-zamanlı kural: bir gözlem, yayımlandığı tarihin (`gözlem/dönem sonu + publication_lag_days`)
düştüğü ayın satırına yazılır. Sık serilerde bu kayma her gözleme toplamadan önce uygulanır; seyrek
serilerde dönem sonuna. Böylece t ayının satırında yalnızca t sonunda bilinen veri olur (look-ahead yok).
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
        # Gecikme TOPLAMADAN ÖNCE her gözleme uygulanır: t ayının satırı, t içinde yayımlanmış
        # gözlemlerin özetidir. (Toplamadan sonra kaydırmak ay içi bilgiyi bir ay geç gösterirdi.)
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
        # Seyrek seride gecikme SABİT bir ay kaymasıdır: gözlemlerin yayın ayına uzaklıklarının en
        # büyüğü. Gözlem başına kaydırmak, ay uzunlukları farklı olduğu için iki dönemi aynı aya
        # düşürürdü (Ocak+30g = 2 Mart, Şubat+30g = 30 Mart → Ocak kaybolur). En büyüğü almak
        # hiçbir değerin yayımlanmadan önce görünmemesini garanti eder.
        available = _month_end(pd.DatetimeIndex(monthly.index + pd.Timedelta(days=publication_lag_days)))
        shift = int(max((a.to_period("M") - p.to_period("M")).n for a, p in zip(available, monthly.index)))
        monthly.index = _month_end(monthly.index + pd.offsets.MonthEnd(shift)) if shift else monthly.index

    monthly = monthly.dropna()
    if freq.is_low and len(monthly) > 0:
        full = pd.date_range(monthly.index.min(), monthly.index.max(), freq="ME")
        monthly = monthly.reindex(full).ffill()
    monthly.index.name = "date"
    return monthly
