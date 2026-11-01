"""Dağılıma dayalı veri ön işleme: profil → plan → sızıntısız uygulama.

Her değişken için eğitim penceresinde dağılım profili çıkarılır (ortalama/standart sapma ile medyan/MAD/IQR,
çarpıklık, basıklık, aykırı değer oranı, durağanlık). Bu profile ve hedeflenen model ailesine göre bir plan
seçilir; parametreler yalnızca eğitim penceresinde tahmin edilir ve bütün döneme aynı parametrelerle uygulanır.
Zamana bağlı adımlar (eksik doldurma, aykırı değer tespiti) yalnızca geçmiş gözlemleri kullanır.

Varsayılan eşikler ve kaynakları `skills/data-prep/references/preprocessing-guide.md` belgesindedir.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.services.distribution import diagnose

MAD_TO_SIGMA = 1.4826  # normal dağılımda MAD × 1,4826 ≈ σ
SKEW_ROBUST = 1.0  # |çarpıklık| > 1: belirgin çarpık dağılım
KURT_ROBUST = 3.0  # aşırı basıklık > 3: kalın kuyruk
OUTLIER_SHARE_ROBUST = 0.02
ROBUST_Z = 3.5  # Iglewicz–Hoaglin değiştirilmiş z skoru eşiği
HAMPEL_WINDOW = 24
HAMPEL_MIN = 12
LOG_RANGE = 10.0  # pozitif seride max/min > 10: büyüklük mertebesi farkı, log uygun
YJ_GRID = np.round(np.linspace(-2.0, 2.0, 81), 3)

FAMILIES = ("linear", "tree", "neural")
SCALINGS = ("auto", "standard", "robust", "none")
POWERS = ("auto", "yeo_johnson", "log", "none")
OUTLIERS = ("auto", "flag", "winsorize", "none")
MISSING = ("ffill", "none")
DIFFERENCES = ("auto", "none")


@dataclass(frozen=True)
class PrepConfig:
    target: str | None = None
    train_end: date | None = None
    model_family: str = "linear"
    missing: str = "ffill"
    max_gap: int = 2
    outliers: str = "auto"
    outlier_k: float = ROBUST_Z
    power: str = "auto"
    scaling: str = "auto"
    difference: str = "auto"

    def __post_init__(self) -> None:
        for value, allowed, what in (
            (self.model_family, FAMILIES, "model_family"), (self.scaling, SCALINGS, "scaling"),
            (self.power, POWERS, "power"), (self.outliers, OUTLIERS, "outliers"),
            (self.missing, MISSING, "missing"), (self.difference, DIFFERENCES, "difference"),
        ):  # fmt: skip
            if value not in allowed:
                raise InvalidInput(f"{what} şu değerlerden biri olmalıdır: {', '.join(allowed)}")
        if not 0 <= self.max_gap <= 12:
            raise InvalidInput("max_gap 0 ile 12 arasında olmalıdır")
        if not 2.0 <= self.outlier_k <= 10.0:
            raise InvalidInput("outlier_k 2 ile 10 arasında olmalıdır")


@dataclass
class Profile:
    feature: str
    n: int
    missing_ratio: float
    mean: float | None
    std: float | None
    median: float | None
    mad: float | None
    iqr: float | None
    skew: float | None
    excess_kurtosis: float | None
    min: float | None
    max: float | None
    outlier_share: float
    stationarity: str | None
    center_scale: str  # "ortalama/standart sapma" | "medyan/IQR"
    reasons: list[str] = field(default_factory=list)


@dataclass
class ColumnPlan:
    feature: str
    is_target: bool
    steps: list[dict]  # her adım: {step, params, reason}


@dataclass
class PrepResult:
    panel: pd.DataFrame
    profiles: list[Profile]
    after: list[Profile]
    plans: list[ColumnPlan]
    config: PrepConfig
    train_end: date
    notes: list[str]


# ------------------------------------------------------------------------------------------- profil


def robust_z(x: pd.Series) -> pd.Series:
    med = x.median()
    mad = (x - med).abs().median()
    if not mad or math.isnan(mad):
        return pd.Series(0.0, index=x.index)
    return 0.6745 * (x - med) / mad  # Iglewicz–Hoaglin: 0,6745 = 1 / 1,4826


def profile(series: pd.Series, name: str, transform: str | None, k: float = ROBUST_Z) -> Profile:
    span = series.loc[series.first_valid_index() : series.last_valid_index()] if series.notna().any() else series
    x = span.dropna().astype(float)
    n = len(x)
    missing = float(span.isna().mean()) if len(span) else 1.0
    if n < 3:
        return Profile(name, n, missing, None, None, None, None, None, None, None, None, None, 0.0, None,
                       "ortalama/standart sapma", ["gözlem sayısı yetersiz"])  # fmt: skip
    med = float(x.median())
    mad = float((x - med).abs().median() * MAD_TO_SIGMA)
    q1, q3 = x.quantile([0.25, 0.75])
    outlier_share = float((robust_z(x).abs() > k).mean())
    d = diagnose(x, name, transform) if n >= 24 else None
    skew = float(x.skew()) if n >= 3 else None
    kurt = float(x.kurt()) if n >= 4 else None
    reasons = []
    if skew is not None and abs(skew) > SKEW_ROBUST:
        reasons.append(f"çarpıklık {skew:+.2f} (|·| > {SKEW_ROBUST:g})")
    if kurt is not None and kurt > KURT_ROBUST:
        reasons.append(f"aşırı basıklık {kurt:.2f} (> {KURT_ROBUST:g})")
    if outlier_share > OUTLIER_SHARE_ROBUST:
        reasons.append(f"aykırı değer oranı %{outlier_share * 100:.1f} (> %{OUTLIER_SHARE_ROBUST * 100:g})")
    return Profile(
        name, n, missing, float(x.mean()), float(x.std()), med, mad, float(q3 - q1), skew, kurt,
        float(x.min()), float(x.max()), outlier_share, d.stationarity if d else None,
        "medyan/IQR" if reasons else "ortalama/standart sapma", reasons,
    )  # fmt: skip


# ----------------------------------------------------------------------------------------- dönüşümler


def yeo_johnson(x: np.ndarray, lam: float) -> np.ndarray:
    out = np.empty_like(x, dtype=float)
    pos = x >= 0
    if abs(lam) > 1e-9:
        out[pos] = ((x[pos] + 1) ** lam - 1) / lam
    else:
        out[pos] = np.log1p(x[pos])
    if abs(lam - 2) > 1e-9:
        out[~pos] = -(((-x[~pos] + 1) ** (2 - lam)) - 1) / (2 - lam)
    else:
        out[~pos] = -np.log1p(-x[~pos])
    return out


def fit_yeo_johnson(x: np.ndarray) -> float:
    """Profil olabilirlik maksimizasyonu (ızgara arama). Yalnızca eğitim penceresinde çağrılır."""
    x = x[np.isfinite(x)]
    n = len(x)
    jac = np.sum(np.sign(x) * np.log1p(np.abs(x)))
    best, best_llf = 1.0, -np.inf
    for lam in YJ_GRID:
        y = yeo_johnson(x, float(lam))
        var = y.var()
        if not np.isfinite(var) or var <= 0:
            continue
        llf = -n / 2 * math.log(var) + (lam - 1) * jac
        if llf > best_llf:
            best, best_llf = float(lam), llf
    return best


def causal_hampel(s: pd.Series, k: float, window: int = HAMPEL_WINDOW) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Geçmiş pencereye göre robust z: t anındaki değer, t−1'e kadarki medyan ve MAD ile karşılaştırılır."""
    past = s.shift(1).rolling(window, min_periods=HAMPEL_MIN)
    med = past.median()
    mad = (s.shift(1) - med).abs().rolling(window, min_periods=HAMPEL_MIN).median() * MAD_TO_SIGMA
    z = (s - med) / mad.replace(0, np.nan)
    return z, med, mad


# --------------------------------------------------------------------------------------------- plan


def _plan_column(name: str, train_series: pd.Series, cfg: PrepConfig, transform: str | None, is_target: bool) -> ColumnPlan:
    steps: list[dict] = []
    linear = cfg.model_family in {"linear", "neural"}
    prof = profile(train_series, name, transform, cfg.outlier_k)
    if cfg.missing == "ffill" and prof.missing_ratio > 0:
        steps.append({"step": "ffill", "params": {"limit": cfg.max_gap},
                      "reason": f"Seri içi eksik oranı %{prof.missing_ratio * 100:.1f}; en fazla {cfg.max_gap} dönem ileri taşınır, "
                                "daha uzun boşluklar boş kalır ve eksiklik göstergesiyle işaretlenir."})  # fmt: skip
    if is_target:
        return ColumnPlan(name, True, steps)
    if cfg.difference == "auto" and linear and prof.stationarity == "birim_kök" and transform in {None, "level"}:
        positive_range = prof.min is not None and prof.min > 0 and prof.max / prof.min > LOG_RANGE
        if positive_range:
            steps.append({"step": "log", "params": {},
                          "reason": "Pozitif ve mertebe farkı büyük düzey serisi; farktan önce log alınır (log-fark ≈ dönemlik büyüme)."})  # fmt: skip
        steps.append({"step": "diff", "params": {},
                      "reason": "ADF ve KPSS birlikte birim kök gösteriyor; doğrusal modelde düzey yerine birinci fark kullanılır."})  # fmt: skip
        changed = np.log(train_series.where(train_series > 0)) if positive_range else train_series
        prof = profile(changed.diff(), name, "diff", cfg.outlier_k)  # sonraki kararlar dönüştürülmüş seriye göre
        transform = "diff"
    mode = cfg.outliers if cfg.outliers != "auto" else ("winsorize" if linear else "flag")
    if mode != "none" and prof.outlier_share > 0:
        steps.append({"step": "outlier_" + mode, "params": {"k": cfg.outlier_k, "window": HAMPEL_WINDOW},
                      "reason": f"Eğitim penceresinde aykırı değer oranı %{prof.outlier_share * 100:.1f}. "
                                + ("Değer geçmiş medyan ± k·MAD aralığına kırpılır ve gösterge değişkenle işaretlenir."
                                   if mode == "winsorize" else "Değer korunur; gösterge değişkenle işaretlenir (gerçek şoklar silinmez).")})  # fmt: skip
    power = cfg.power
    if power == "auto":
        power = "none"
        if linear and prof.skew is not None and abs(prof.skew) > SKEW_ROBUST:
            positive_range = prof.min is not None and prof.min > 0 and prof.max / prof.min > LOG_RANGE
            power = "log" if positive_range and transform in {None, "level"} else "yeo_johnson"
    if power == "log" and (prof.min is None or prof.min <= 0):
        power = "yeo_johnson"
    if power != "none":
        steps.append({"step": power, "params": {},
                      "reason": f"Çarpıklık {prof.skew:+.2f}; " + ("pozitif ve mertebe farkı büyük seri için log dönüşümü."
                                if power == "log" else "işaret içeren/çarpık seri için Yeo–Johnson (λ eğitim penceresinde tahmin edilir).")})  # fmt: skip
    scaling = cfg.scaling
    if scaling == "auto":
        scaling = "none" if cfg.model_family == "tree" else ("robust" if prof.reasons else "standard")
    if scaling != "none":
        label = "medyan ve IQR" if scaling == "robust" else "ortalama ve standart sapma"
        steps.append({"step": "scale_" + scaling, "params": {},
                      "reason": f"Ölçekleme {label} ile yapılır; parametreler yalnızca eğitim penceresinden hesaplanır."
                                + (f" Gerekçe: {', '.join(prof.reasons)}." if scaling == "robust" and prof.reasons else "")})  # fmt: skip
    return ColumnPlan(name, False, steps)


# ------------------------------------------------------------------------------------------ uygulama


def _apply(s: pd.Series, plan: ColumnPlan, train: pd.Index, extra: dict[str, pd.Series]) -> pd.Series:
    out = s.astype(float).copy()
    for step in plan.steps:
        kind = step["step"]
        if kind == "ffill":
            limit = step["params"]["limit"]
            before = out.isna()
            if limit:
                active = out.index >= out.first_valid_index() if out.notna().any() else out.index == out.index
                out = out.where(~active, out.ffill(limit=limit))
            gaps = out.isna() & before
            if out.notna().any():
                gaps = gaps & (out.index >= out.first_valid_index())
            if gaps.any():
                extra[f"{plan.feature}__eksik"] = gaps.astype(float)
        elif kind == "diff":
            out = out.diff()
        elif kind.startswith("outlier_"):
            z, med, mad = causal_hampel(out, step["params"]["k"])
            hit = z.abs() > step["params"]["k"]
            extra[f"{plan.feature}__aykiri"] = hit.astype(float)
            step["params"]["count_train"] = int(hit.reindex(train).fillna(False).sum())
            if kind == "outlier_winsorize":
                k = step["params"]["k"]
                out = out.where(~hit, out.clip(lower=med - k * mad, upper=med + k * mad))
        elif kind == "log":
            out = np.log(out.where(out > 0))
        elif kind == "yeo_johnson":
            lam = fit_yeo_johnson(out.reindex(train).dropna().to_numpy())
            step["params"]["lambda"] = lam
            vals = out.to_numpy(dtype=float, copy=True)
            ok = np.isfinite(vals)
            vals[ok] = yeo_johnson(vals[ok], lam)
            out = pd.Series(vals, index=out.index)
        elif kind.startswith("scale_"):
            ref = out.reindex(train).dropna()
            if len(ref) < 3:
                continue
            if kind == "scale_robust":
                center = float(ref.median())
                scale = float(ref.quantile(0.75) - ref.quantile(0.25)) or float(ref.std()) or 1.0
            else:
                center, scale = float(ref.mean()), float(ref.std()) or 1.0
            step["params"].update(center=center, scale=scale)
            out = (out - center) / scale
    return out


def prepare(panel: pd.DataFrame, cfg: PrepConfig, transforms: dict[str, str] | None = None) -> PrepResult:
    if panel is None or panel.empty:
        raise InvalidInput("Panel boş")
    if not isinstance(panel.index, pd.DatetimeIndex):
        raise InvalidInput("Panelin indeksi tarih olmalıdır")
    if cfg.target and cfg.target not in panel.columns:
        raise InvalidInput(f"Hedef kolon panelde yok: {cfg.target}")
    numeric = panel.select_dtypes("number")
    notes: list[str] = []
    if cfg.train_end is None:
        cut = numeric.index[int(len(numeric) * 0.8) - 1]
        notes.append(f"Eğitim penceresi sonu verilmedi; örneklemin ilk %80'i ({cut.date()} dahil) eğitim kabul edildi.")
    else:
        cut = pd.Timestamp(cfg.train_end)
        if cut < numeric.index[0] or cut >= numeric.index[-1]:
            raise InvalidInput("train_end panel dönemi içinde olmalı ve son gözlemden önce gelmelidir")
    train = numeric.index[numeric.index <= cut]
    if len(train) < 24:
        notes.append(f"Eğitim penceresi {len(train)} dönem; dağılım tahminleri belirsizdir (en az 24 önerilir).")
    transforms = transforms or {}
    profiles, plans, columns, extra = [], [], {}, {}
    for name in numeric.columns:
        prof = profile(numeric.loc[train, name], name, transforms.get(name), cfg.outlier_k)
        plan = _plan_column(name, numeric.loc[train, name], cfg, transforms.get(name), name == cfg.target)
        profiles.append(prof)
        plans.append(plan)
        columns[name] = _apply(numeric[name], plan, train, extra)
    out = pd.DataFrame(columns, index=numeric.index)
    if extra:
        flags = pd.DataFrame(extra, index=numeric.index)
        flags = flags.loc[:, flags.reindex(train).sum() > 0]  # eğitimde hiç tetiklenmeyen gösterge bilgi taşımaz
        out = pd.concat([out, flags], axis=1)
    after = [profile(out.loc[train, p.feature], p.feature, None, cfg.outlier_k) for p in plans]
    return PrepResult(out, profiles, after, plans, cfg, cut.date(), notes)
