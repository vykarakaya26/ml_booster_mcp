"""Use case input/output objects. Input validation happens here, at the gate of the core."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Aggregation, Frequency
from mlbooster.domain.validation import DateRange, series_codes, slug

# Provider-independent formula codes (same numbering as EVDS).
FORMULA_METRICS = {
    0: "level", 1: "pct_change", 2: "diff", 3: "yoy_pct", 4: "yoy_diff",
    5: "ytd_pct", 6: "ytd_diff", 7: "moving_avg", 8: "moving_sum",
}  # fmt: skip


def metric_name(formula: int, agg: Aggregation | None = None) -> str:
    return FORMULA_METRICS[formula] + (f"_{agg.value}" if agg else "")


@dataclass(frozen=True)
class FetchRequest:
    source_id: str
    series_ids: tuple[str, ...]
    period: DateRange
    freq: Frequency | None = None
    agg: Aggregation | None = None
    formula: int = 0

    @classmethod
    def create(
        cls,
        source_id: str,
        series_ids: list[str] | tuple[str, ...],
        start: date,
        end: date | None = None,
        freq: str | None = None,
        agg: str | None = None,
        formula: int = 0,
    ) -> FetchRequest:
        if type(formula) is not int or formula not in FORMULA_METRICS:  # also reject bool/float
            raise InvalidInput(f"Geçersiz formül {formula}; seçenekler: {sorted(FORMULA_METRICS)}")
        try:
            freq_v = Frequency(freq) if freq else None
            agg_v = Aggregation(agg) if agg else None
        except ValueError as exc:
            raise InvalidInput(str(exc)) from None
        return cls(
            source_id=slug(source_id, "source"),
            series_ids=series_codes(series_ids),
            period=DateRange(start, end or date.today()),
            freq=freq_v,
            agg=agg_v,
            formula=formula,
        )

    @property
    def metric(self) -> str:
        return metric_name(self.formula, self.agg)


@dataclass(frozen=True)
class FetchSummary:
    rows: int
    by_series: list[dict]
    tail: list[dict]
    attribution: str
    # Date ranges actually requested from the source; the rest came from the local store (incremental sources).
    fetched: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class PanelResult:
    sector_id: str
    panel: pd.DataFrame = field(default_factory=pd.DataFrame)
    skipped: dict[str, str] = field(default_factory=dict)
    location: str | None = None


@dataclass(frozen=True)
class RunnerReadiness:
    """Whether the web search can run on this computer, and the exact next step if it cannot."""

    ready: bool
    reason: str | None = None
    next_step: str | None = None
    executable: str | None = None
    plugin_dir: str | None = None


@dataclass(frozen=True)
class ResearchEvent:
    kind: str  # started | search | page | record | refused


@dataclass(frozen=True)
class ResearchOutcome:
    status: str  # finished | failed | cancelled
    message: str
    report: str | None = None
