"""Panel kalite denetimi: eksiklik, bayatlık, aykırı değer, yapısal kırılma, aşırı korelasyon.

Bütün kontroller açıklanabilir basit istatistiklerdir. Amaç, modelden önce insanın bakması
gereken noktaları işaretlemektir; kesin hüküm vermek değil.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

import numpy as np
import pandas as pd

from mlbooster.domain.services.distribution import Diagnostics, diagnose_panel

# Türkiye makro verisinde bilinen rejim/şok dönemleri: kırılma bulgularını yorumlamak için bağlam.
KNOWN_EVENTS: dict[str, str] = {
    "2014-01": "Ocak 2014 TCMB olağanüstü faiz artırımı",
    "2018-08": "Ağustos 2018 kur şoku",
    "2020-03": "COVID-19 şoku",
    "2021-12": "Aralık 2021 kur şoku / KKM",
    "2023-06": "Para politikasında normalleşme (sıkılaştırma başlangıcı)",
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
    notable_correlation: float = 0.80  # bu eşikle collinearity arası: bilgi olarak listelenir


@dataclass(frozen=True)
class QualityReport:
    as_of: date
    months: int
    series: tuple[SeriesQuality, ...]
    issues: tuple[Issue, ...]
    correlated_pairs: tuple[tuple[str, str, float], ...] = field(default_factory=tuple)
    diagnostics: tuple[Diagnostics, ...] = field(default_factory=tuple)
    notable_pairs: tuple[tuple[str, str, float, str], ...] = field(default_factory=tuple)  # a, b, r, not

    @property
    def counts(self) -> dict[str, int]:
        return {s.value: sum(1 for i in self.issues if i.severity is s) for s in Severity}


def _robust_outliers(s: pd.Series, z: float) -> list[pd.Timestamp]:
    """Medyan mutlak sapma (MAD) tabanlı z-skoru: kalın kuyruklu TR verisinde std'den sağlam."""
    median = s.median()
    mad = (s - median).abs().median()
    if not mad or np.isnan(mad):
        return []
    robust_z = 0.6745 * (s - median) / mad
    return list(s.index[robust_z.abs() > z])


def _mean_shift_breaks(s: pd.Series, window: int, threshold: float) -> list[tuple[pd.Timestamp, float]]:
    """Her ay için önceki/sonraki `window` ayın ortalama farkını havuzlanmış std ile ölçekler (t-istatistiği).
    Eşiği aşan yerel tepeler, birbirine en az `window` ay uzaklıkta olacak şekilde döner."""
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
    """`companions`: aynı ham seriden türeyen feature çiftleri (ör. aofm / aofm_son); kolineerlik sayılmaz."""
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
            issues.append(Issue(Severity.HIGH, name, "stale", f"Son gözlem {last.date()} ({months_since} ay önce); seri durmuş veya kodu değişmiş olabilir."))
        if missing > t.missing_ratio:
            issues.append(Issue(Severity.MEDIUM, name, "missing", f"Aralık içinde eksik oranı %{missing * 100:.1f}."))
        if outliers:
            sample = ", ".join(str(d.date()) for d in outliers[:5])
            issues.append(Issue(Severity.MEDIUM, name, "outliers", f"{len(outliers)} aykırı değer (robust z>{t.outlier_z:g}): {sample}"))
        for b in breaks:
            context = f" — muhtemel neden: {b.event}" if b.event else " — bilinen bir olayla eşleşmedi, incelenmeli"
            severity = Severity.INFO if b.event else Severity.MEDIUM
            issues.append(Issue(severity, name, "structural_break", f"{b.date:%Y-%m} civarında ortalama kayması (t={b.t_stat:+.1f}){context}."))
        if valid.nunique() <= 1:
            issues.append(Issue(Severity.HIGH, name, "constant", "Seri sabit; modele bilgi taşımaz."))

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
                    notable.append((a, b, round(float(value), 3), "aynı ham seriden türetilmiş; modelde birini kullanın"))
                    continue
                if t.notable_correlation <= abs(value) < t.collinearity:
                    notable.append((a, b, round(float(value), 3), "yüksek korelasyon; birlikte kullanmadan önce düşünün"))
                if abs(value) >= t.collinearity:
                    pairs.append((a, b, round(float(value), 3)))
                    issues.append(Issue(Severity.MEDIUM, f"{a} ~ {b}", "collinearity", f"Korelasyon {value:+.2f}; ikisinden biri gereksiz olabilir."))

    diagnostics = diagnose_panel(panel, transforms)
    for d in diagnostics:
        if d.stationarity == "birim_kök":
            issues.append(Issue(Severity.INFO, d.feature, "nonstationary", f"ADF t={d.adf_t}, KPSS={d.kpss_stat}: birim kök (seviye durağan değil)."))
        elif d.stationarity == "kırılma_şüphesi":
            issues.append(Issue(Severity.INFO, d.feature, "nonstationary", f"ADF ve KPSS ikisi de reddediyor (t={d.adf_t}, KPSS={d.kpss_stat}): kırılma/rejim şüphesi."))
        if d.excess_kurtosis is not None and d.excess_kurtosis > 3:
            issues.append(Issue(Severity.INFO, d.feature, "heavy_tails", f"Fazla basıklık {d.excess_kurtosis:.1f}: kalın kuyruk (şoklar)."))

    order = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.INFO: 2}
    issues.sort(key=lambda i: (order[i.severity], i.feature))
    return QualityReport(
        as_of, int(panel.shape[0]), tuple(series), tuple(issues), tuple(pairs), diagnostics, tuple(notable)
    )
