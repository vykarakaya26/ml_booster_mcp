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


# --- Haftalık / iş günlük panel ----------------------------------------------------------------

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
    """Seriyi hedef panel frekansına (M aylık, W haftalık-Cuma, B iş günü) nokta-zamanlı taşır.

    Her gözlem önce yayın tarihine kaydırılır (gözlem/dönem sonu + gecikme). Hedeften sık seriler bu
    tarihe göre hedef dönemlere toplanır; seyrek seriler yalnızca yayımlandıkları günden itibaren ileriye
    doldurulur. Böylece bir satırda yalnızca o satırın tarihinde yayımlanmış veri bulunur."""
    if target == "M":
        return to_monthly(s, freq, agg, kind, publication_lag_days)
    if target not in PANEL_FREQS:
        raise ValueError(f"Panel frekansı {list(PANEL_FREQS)} olmalı: {target}")
    s = s.dropna().sort_index()
    if s.empty:
        return s
    rule = PANEL_FREQS[target]
    s.index = pd.DatetimeIndex(s.index) + pd.Timedelta(days=publication_lag_days)  # yayın tarihi
    finer = (target == "W" and freq in {Frequency.DAILY, Frequency.BUSINESS, Frequency.HOURLY}) or (
        target == "B" and freq is Frequency.HOURLY
    )
    if finer:
        default = Aggregation.SUM if kind is SeriesKind.FLOW else Aggregation.AVG
        out = s.resample(rule).agg(_PANDAS_AGG[agg or default])
        last = s.index.max()
        if target == "W" and last < (last + pd.offsets.Week(weekday=4, n=0)).normalize():
            out = out.iloc[:-1]  # tamamlanmamış son hafta
    else:
        # Aynı ya da daha seyrek: yayın tarihinden itibaren ileriye doldur (as-of birleştirme).
        # Son değer, bir sonraki dönemin yayımına kadar geçerlidir: ileriye doldurma ufku ≈ bir dönem.
        horizon = pd.Timedelta(days=max(7, int(freq.min_period_days * 1.15)))
        grid = pd.date_range(s.index.min().normalize(), s.index.max().normalize() + horizon, freq=rule)
        s = s[~s.index.duplicated(keep="last")]
        out = s.reindex(s.index.union(grid)).ffill().reindex(grid)
    out = out.dropna()
    out.index.name = "date"
    return out
