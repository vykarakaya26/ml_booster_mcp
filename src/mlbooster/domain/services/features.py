"""Feature transforms (on monthly series with a contiguous index)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from mlbooster.domain.model import Transform

_RULES = {"M": "ME", "W": "W-FRI", "B": "B"}
_PER_YEAR = {"M": 12, "W": 52, "B": 252}


def contiguous_monthly(s: pd.Series, freq: str = "M") -> pd.Series:
    """Fills missing periods with NaN; required for shift-based transforms."""
    if s.empty:
        return s
    return s.reindex(pd.date_range(s.index.min(), s.index.max(), freq=_RULES[freq]))


def apply_transform(s: pd.Series, transform: Transform, freq: str = "M") -> pd.Series:
    """`freq` is the panel frequency; the year-on-year change is computed over 12 / 52 / 252 periods."""
    s = contiguous_monthly(s, freq)
    match transform:
        case Transform.LEVEL:
            return s
        case Transform.DIFF:
            return s.diff()
        case Transform.LOG_DIFF:
            return np.log(s.where(s > 0)).diff()
        case Transform.YOY_PCT:
            return (s / s.shift(_PER_YEAR[freq]) - 1) * 100
