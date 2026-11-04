"""Real/nominal conversion. The deflator is chain-linked from monthly change rates, not levels;
so the CPI (TÜFE) base change (2003=100 → 2025=100) does not create a break in the level."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import pandas as pd

from mlbooster.domain.errors import InvalidInput


def _rebase(index: pd.Series, ref_period: object | None) -> pd.Series:
    ref = index.index[-1] if ref_period is None else pd.Timestamp(ref_period)
    if ref not in index.index:
        raise InvalidInput(f"Referans dönem {ref} deflator aralığının dışında")
    return index / index.loc[ref] * 100


def index_from_pct_changes(pct: pd.Series, ref_period: object | None = None) -> pd.Series:
    """Chain-linked index from monthly % changes; ref_period=100 (default: last period)."""
    pct = pct.dropna().sort_index()
    return _rebase((1 + pct / 100).cumprod(), ref_period)


def index_from_levels(
    levels: pd.Series,
    ref_period: object | None = None,
    break_dates: Iterable[object] = (),
    pct_at_breaks: Mapping[object, float] | None = None,
) -> pd.Series:
    """Chain-linked index from levels. In months with a base break the level ratio is meaningless;
    the official monthly % change for those months must be supplied via `pct_at_breaks`."""
    levels = levels.dropna().sort_index()
    ratio = levels / levels.shift(1)
    breaks = {pd.Timestamp(k): v for k, v in (pct_at_breaks or {}).items()}
    for raw in break_dates:
        b = pd.Timestamp(raw)
        if b not in breaks:
            raise InvalidInput(f"{b.date()} baz değişikliği için aylık % değişim (pct_at_breaks) verilmelidir")
        ratio.loc[b] = 1 + breaks[b] / 100
    return _rebase(ratio.fillna(1.0).cumprod(), ref_period)


def deflate(nominal: pd.Series, deflator: pd.Series) -> pd.Series:
    """Real = nominal / (deflator / 100). Only common dates are returned."""
    nominal, deflator = nominal.align(deflator, join="inner")
    return nominal / (deflator / 100)
