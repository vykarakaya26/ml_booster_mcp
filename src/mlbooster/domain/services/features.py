"""Feature dönüşümleri (aylık, kesintisiz indeksli seriler üzerinde)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from mlbooster.domain.model import Transform


def contiguous_monthly(s: pd.Series) -> pd.Series:
    """Eksik ayları NaN ile doldurur; shift tabanlı dönüşümler için zorunlu."""
    if s.empty:
        return s
    return s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="ME"))


def apply_transform(s: pd.Series, transform: Transform) -> pd.Series:
    s = contiguous_monthly(s)
    match transform:
        case Transform.LEVEL:
            return s
        case Transform.DIFF:
            return s.diff()
        case Transform.LOG_DIFF:
            return np.log(s.where(s > 0)).diff()
        case Transform.YOY_PCT:
            return (s / s.shift(12) - 1) * 100
