"""ML-ready monthly features of a brand from a share table, in point-in-time form.

Every column of month t uses only figures published by the end of t (`frequency.to_monthly`). The competitor set of a
month is rebuilt from the ranking of that period: a fixed set chosen with today's data would leak the future. Share
tables come from the latest vintage, so later revisions of a source are visible in past rows (as in sector panels).
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Frequency
from mlbooster.domain.services.brand_resolution import fold, is_placeholder
from mlbooster.domain.services.frequency import to_monthly
from mlbooster.domain.services.market_share import ShareConfig, compute_shares, prepare

NEAREST_RANKS = 2  # competitors of a period: this many players above and below the brand in that period's ranking

BRAND_COLUMNS = {
    "marka_pay": "Markanın pazar payı (%, analiz tabanında: akım serilerde son 12 ay, stok serilerde dönem)",
    "marka_pay_degisim": "Marka payının bir önceki döneme göre değişimi (puan)",
    "marka_sira": "Pay sıralaması (1 = lider; satışı olmayan dönemde boş)",
    "lider_farki": "Lider ile marka arasındaki pay farkı (puan; marka liderse 0)",
    "goreli_buyume": "Markanın yıllık büyümesi − pazarın yıllık büyümesi (puan)",
    "rakip_pay": "O dönemin sıralamasında markaya en yakın oyuncuların toplam payı (%)",
}


def brand_metrics(table: pd.DataFrame, config: ShareConfig, exclude: tuple[str, ...] = ()) -> pd.DataFrame:
    """Brand metrics at the periods of the source (not yet moved to their publication month). `config.focus` is the
    entity label; `exclude`: labels that are not rivals (the brand's sibling entities)."""
    prep = prepare(table, config)
    tables = compute_shares(prep, config)
    shares = tables.shares.dropna(how="all")
    focus = next(c for c in shares.columns if c.casefold() == (config.focus or "").casefold())
    rival_columns = [c for c in shares.columns if c != focus and c not in exclude and not is_placeholder(c)]
    own, rivals = shares[focus].astype(float), shares[rival_columns].astype(float)
    present = own > 0
    out = pd.DataFrame(index=shares.index)
    out["marka_pay"] = own
    out["marka_pay_degisim"] = own.diff()
    out["marka_sira"] = (1.0 + rivals.gt(own, axis=0).sum(axis=1)).where(present)
    out["lider_farki"] = (rivals.max(axis=1) - own).clip(lower=0)
    if tables.values is not None:
        ppy = prep.periods_per_year
        value, market = tables.values[focus].reindex(shares.index), tables.market["market"].reindex(shares.index)
        growth = value / value.shift(ppy).replace(0, np.nan) - 1
        out["goreli_buyume"] = (growth - (market / market.shift(ppy).replace(0, np.nan) - 1)) * 100
    out["rakip_pay"] = [_nearest_share(row.dropna(), mine) if mine > 0 else np.nan for (_, row), mine in zip(rivals.iterrows(), own, strict=True)]
    return out


def _nearest_share(rivals: pd.Series, own: float, k: int = NEAREST_RANKS) -> float:
    active = rivals[rivals > 0].sort_values()
    return float(active[active <= own].tail(k).sum() + active[active > own].head(k).sum())


def point_in_time(metrics: pd.DataFrame, freq: Frequency, publication_lag_days: int) -> pd.DataFrame:
    """Moves every column to the month its figures are published in; quarterly figures stay until the next release."""
    columns = {c: to_monthly(metrics[c], freq, publication_lag_days=publication_lag_days) for c in metrics.columns}
    return pd.DataFrame({c: s for c, s in columns.items() if not s.empty})


def monthly_grid(frames: list[pd.DataFrame | pd.Series], first: pd.Timestamp, last: pd.Timestamp) -> pd.DatetimeIndex:
    """Contiguous month ends from the first brand row to the last row of any frame, inside the requested period."""
    latest = max(f.index.max() for f in frames if not f.empty)
    start, end = max(first, frames[0].index.min()), min(last, latest)
    grid = pd.date_range(start, end, freq="ME")
    if len(grid) < 3:
        raise InvalidInput("Marka feature'ları için en az üç aylık gözlem gerekir")
    return grid


def panel_id(name: str, match_id: str) -> str:
    """File name stem of a brand panel: readable, and unique per brand through a hash of the candidate id."""
    readable = "_".join(fold(name).replace("'", "").split())
    readable = "".join(c for c in readable if c.isascii() and (c.isalnum() or c == "_"))[:16].strip("_")
    return f"marka_{readable}_{hashlib.sha256(match_id.encode()).hexdigest()[:6]}"
