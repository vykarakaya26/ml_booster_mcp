"""Reel/nominal dönüşüm. Deflatör seviyelerden değil aylık değişim oranlarından zincirlenir;
böylece TÜFE baz değişikliği (2003=100 → 2025=100) seviyede kırılma yaratmaz."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import numpy as np
import pandas as pd


def _rebase(index: pd.Series, ref_period: object | None) -> pd.Series:
    ref = index.index[-1] if ref_period is None else pd.Timestamp(ref_period)
    if ref not in index.index:
        raise ValueError(f"Referans dönem {ref} deflatör aralığında değil")
    return index / index.loc[ref] * 100


def index_from_pct_changes(pct: pd.Series, ref_period: object | None = None) -> pd.Series:
    """Aylık % değişimlerden (EVDS formulas=1) zincirlenmiş endeks; ref_period=100."""
    pct = pct.dropna().sort_index()
    return _rebase((1 + pct / 100).cumprod(), ref_period)


def index_from_levels(
    levels: pd.Series,
    ref_period: object | None = None,
    break_dates: Iterable[object] = (),
    pct_at_breaks: Mapping[object, float] | None = None,
) -> pd.Series:
    """Seviyelerden zincirleme endeks. Baz kırılması olan aylarda seviye oranı anlamsızdır;
    o aylar için resmi aylık % değişim `pct_at_breaks` ile verilmelidir."""
    levels = levels.dropna().sort_index()
    ratio = levels / levels.shift(1)
    pct_at_breaks = {pd.Timestamp(k): v for k, v in (pct_at_breaks or {}).items()}
    for raw in break_dates:
        b = pd.Timestamp(raw)
        if b not in pct_at_breaks:
            raise ValueError(f"{b.date()} kırılması için aylık % değişim (pct_at_breaks) gerekli")
        ratio.loc[b] = 1 + pct_at_breaks[b] / 100
    return _rebase(ratio.fillna(1.0).cumprod(), ref_period)


def deflate(nominal: pd.Series, deflator: pd.Series) -> pd.Series:
    """Reel = nominal / (deflatör / 100). Yalnızca ortak tarihler döner."""
    nominal, deflator = nominal.align(deflator, join="inner")
    return nominal / (deflator / 100)


def apply_transform(s: pd.Series, transform: str) -> pd.Series:
    if transform == "level":
        return s
    if transform == "diff":
        return s.diff()
    if transform == "log_diff":
        return np.log(s).diff()
    if transform == "yoy_pct":
        return (s / s.shift(12) - 1) * 100
    raise ValueError(f"Bilinmeyen dönüşüm: {transform}")
