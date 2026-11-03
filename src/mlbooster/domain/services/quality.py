"""Panel quality audit: missingness, timeliness, outliers, structural breaks, excessive correlation.

All checks are simple, explainable statistics. The aim is to flag the points an analyst should review
before modelling, not to deliver a definitive verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

import numpy as np
import pandas as pd

from mlbooster.domain.numfmt import tr_num, tr_pct
from mlbooster.domain.services.distribution import Diagnostics, diagnose_panel

# Known regime/shock periods in Turkish macro data: context for interpreting break findings (Turkish display labels).
KNOWN_EVENTS: dict[str, str] = {
    "2014-01": "Ocak 2014 TCMB olağanüstü faiz artırımı",
    "2018-08": "Ağustos 2018 kur şoku",
    "2020-03": "COVID-19 şoku",
    "2021-12": "Aralık 2021 kur şoku / KKM",
    "2023-06": "Para politikasında normalleşme (sıkılaştırmanın başlangıcı)",
    "2026-01": "TÜFE 2025=100 baz değişikliği",
}


class Severity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    INFO = "info"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    feature: str
    check: str
    message: str


@dataclass(frozen=True)
class BreakPoint:
    date: date
    t_stat: float
    event: str | None


@dataclass(frozen=True)
class SeriesQuality:
    feature: str
    first: date | None
    last: date | None
    observations: int
    missing_ratio: float
    months_since_last: int | None
    outliers: tuple[date, ...]
    breaks: tuple[BreakPoint, ...]


@dataclass(frozen=True)
class QualityThresholds:
    stale_months: int = 3
    missing_ratio: float = 0.10
    outlier_z: float = 5.0
    break_t: float = 4.0
    break_window: int = 24
    collinearity: float = 0.95
    notable_correlation: float = 0.80  # between this threshold and collinearity: listed for information


@dataclass(frozen=True)
class QualityReport:
    as_of: date
    months: int
    series: tuple[SeriesQuality, ...]
    issues: tuple[Issue, ...]
    correlated_pairs: tuple[tuple[str, str, float], ...] = field(default_factory=tuple)
    diagnostics: tuple[Diagnostics, ...] = field(default_factory=tuple)
    notable_pairs: tuple[tuple[str, str, float, str], ...] = field(default_factory=tuple)  # a, b, r, note

    @property
    def counts(self) -> dict[str, int]:
        return {s.value: sum(1 for i in self.issues if i.severity is s) for s in Severity}


def _robust_outliers(s: pd.Series, z: float) -> list[pd.Timestamp]:
    """Median absolute deviation (MAD) based z-score: more robust than std for heavy-tailed Turkish data."""
    median = s.median()
    mad = (s - median).abs().median()
    if not mad or np.isnan(mad):
        return []
    robust_z = 0.6745 * (s - median) / mad
    return list(s.index[robust_z.abs() > z])


def _mean_shift_breaks(s: pd.Series, window: int, threshold: float) -> list[tuple[pd.Timestamp, float]]:
    """For each month, scales the difference between the means of the preceding/following `window` months by the
    pooled std (t-statistic). Returns local peaks above the threshold that are at least `window` months apart."""
    values = s.to_numpy(dtype=float)
    n = len(values)
    if n < 2 * window:
        return []
    stats = np.full(n, np.nan)
    for i in range(window, n - window + 1):
        left, right = values[i - window : i], values[i : i + window]
        pooled = np.sqrt((left.var(ddof=1) + right.var(ddof=1)) / 2)
        if pooled > 0:
            stats[i] = (right.mean() - left.mean()) / (pooled * np.sqrt(2 / window))
    found: list[tuple[pd.Timestamp, float]] = []
    for i in np.argsort(-np.nan_to_num(np.abs(stats), nan=0.0)):
        if np.isnan(stats[i]) or abs(stats[i]) < threshold:
            break
        if all(abs(i - s.index.get_loc(d)) >= window for d, _ in found):
            found.append((s.index[i], float(stats[i])))
    return sorted(found)


def _event_for(ts: pd.Timestamp, tolerance_months: int = 3) -> str | None:
    period = ts.to_period("M")
    for key, label in KNOWN_EVENTS.items():
        if abs((period - pd.Period(key, "M")).n) <= tolerance_months:
            return label
    return None


def audit_panel(
    panel: pd.DataFrame,
    as_of: date,
    thresholds: QualityThresholds | None = None,
    transforms: dict[str, str] | None = None,
    companions: set[frozenset[str]] | None = None,
) -> QualityReport:
    """`companions`: feature pairs derived from the same raw series (e.g. aofm / aofm_son); not counted as collinearity."""
    t = thresholds or QualityThresholds()
    companions = companions or set()
    as_of_month = pd.Timestamp(as_of).to_period("M")
    series, issues = [], []

    for name in panel.columns:
        s = panel[name]
        valid = s.dropna()
        if valid.empty:
            series.append(SeriesQuality(name, None, None, 0, 1.0, None, (), ()))
            issues.append(Issue(Severity.HIGH, name, "empty", "Seride hiç gözlem yok."))
            continue
        first, last = valid.index.min(), valid.index.max()
        span = s.loc[first:last]
        missing = float(span.isna().mean())
        months_since = (as_of_month - last.to_period("M")).n
        outliers = _robust_outliers(valid, t.outlier_z)
        breaks = [
            BreakPoint(d.date(), round(stat, 2), _event_for(d))
            for d, stat in _mean_shift_breaks(valid, t.break_window, t.break_t)
        ]
        series.append(
            SeriesQuality(
                name, first.date(), last.date(), int(valid.size), round(missing, 4), months_since,
                tuple(d.date() for d in outliers), tuple(breaks),
            )
        )  # fmt: skip

        if months_since > t.stale_months:
            issues.append(Issue(Severity.HIGH, name, "stale", f"Son gözlem {last.date()} ({months_since} ay önce). Serinin yayımı durdurulmuş ya da seri kodu değiştirilmiş olabilir (stale seri)."))
        if missing > t.missing_ratio:
            issues.append(Issue(Severity.MEDIUM, name, "missing", f"Gözlem aralığındaki eksik değer oranı {tr_pct(missing * 100)}."))
        if outliers:
            sample = ", ".join(str(d.date()) for d in outliers[:5])
            issues.append(Issue(Severity.MEDIUM, name, "outliers", f"{len(outliers)} outlier (robust z > {tr_num(t.outlier_z, 'g')}): {sample}"))
        for b in breaks:
            context = f"; eşleşen bilinen olay: {b.event}" if b.event else "; bilinen bir olayla eşleşmiyor, incelenmesi gerekir"
            severity = Severity.INFO if b.event else Severity.MEDIUM
            issues.append(Issue(severity, name, "structural_break", f"{b.date:%Y-%m} civarında ortalamada kayma (t={tr_num(b.t_stat, '+.1f')}){context}."))
        if valid.nunique() <= 1:
            issues.append(Issue(Severity.HIGH, name, "constant", "Seri sabit; model için bilgi taşımaz."))

    pairs, notable = [], []
    numeric = panel.dropna(axis=1, how="all")
    if numeric.shape[1] >= 2:
        corr = numeric.corr(min_periods=24)
        cols = list(corr.columns)
        for i, a in enumerate(cols):
            for b in cols[i + 1 :]:
                value = corr.loc[a, b]
                if pd.isna(value):
                    continue
                if frozenset((a, b)) in companions:
                    notable.append((a, b, round(float(value), 3), "aynı ham seriden türetilmiş; modelde yalnızca biri kullanılmalıdır"))
                    continue
                if t.notable_correlation <= abs(value) < t.collinearity:
                    notable.append((a, b, round(float(value), 3), "yüksek korelasyon; birlikte kullanılmadan önce değerlendirilmelidir"))
                if abs(value) >= t.collinearity:
                    pairs.append((a, b, round(float(value), 3)))
                    issues.append(Issue(Severity.MEDIUM, f"{a} ~ {b}", "collinearity", f"Korelasyon {tr_num(value, '+.2f')}; feature'lardan biri aynı bilgiyi tekrar ediyor olabilir."))

    diagnostics = diagnose_panel(panel, transforms)
    for d in diagnostics:
        if d.stationarity == "unit_root":
            issues.append(Issue(Severity.INFO, d.feature, "nonstationary", f"ADF t={tr_num(d.adf_t, 'g')}, KPSS={tr_num(d.kpss_stat, 'g')}: unit root var, düzey non-stationary."))
        elif d.stationarity == "break_suspected":
            issues.append(Issue(Severity.INFO, d.feature, "nonstationary", f"ADF ve KPSS ikisi de reddediyor (t={tr_num(d.adf_t, 'g')}, KPSS={tr_num(d.kpss_stat, 'g')}): structural break ya da rejim değişikliği şüphesi."))
        if d.managed_path:
            issues.append(Issue(Severity.INFO, d.feature, "managed_path", "Kontrollü seyir: son 12 ayda değişim pozitif ve istikrarlı, volatilite düşük. Seri random walk gibi davranmıyor; ani sıçrama riski dağılımın kuyruğunda yoğunlaşıyor."))
        if d.excess_kurtosis is not None and d.excess_kurtosis > 3:
            issues.append(Issue(Severity.INFO, d.feature, "heavy_tails", f"Excess kurtosis {tr_num(d.excess_kurtosis, '.1f')}: heavy tail (şok dönemleri)."))

    order = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.INFO: 2}
    issues.sort(key=lambda i: (order[i.severity], i.feature))
    return QualityReport(
        as_of, int(panel.shape[0]), tuple(series), tuple(issues), tuple(pairs), diagnostics, tuple(notable)
    )
