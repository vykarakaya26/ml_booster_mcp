"""Distribution diagnostics before modelling: shape, stationarity, volatility regime, seasonality → transform advice.

numpy/pandas only; the tests use their textbook forms (so that they remain explainable):
- ADF (with constant; lag chosen by AIC within the Schwert upper bound, on a common sample) — H0: unit root.
  Critical values from the MacKinnon (2010) finite-sample response surface (5% asymptotic −2.86).
- KPSS (level) — H0: stationary. Critical values: 10% 0.347, 5% 0.463, 1% 0.739 (Kwiatkowski et al. 1992).
- Both are read together: ADF rejects & KPSS does not → stationary; the reverse → unit root;
  both reject → suspected break/difference-stationary; neither → inconclusive (short sample).
- Jarque-Bera: JB = n/6 (S² + K²/4); p ≈ exp(−JB/2) (χ²₂).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from mlbooster.domain.numfmt import tr_num

ADF_CRIT_5 = -2.86
KPSS_CRIT_5 = 0.463


@dataclass(frozen=True)
class Diagnostics:
    feature: str
    n: int
    mean: float
    std: float
    median: float
    p01: float
    p99: float
    skew: float
    excess_kurtosis: float
    jarque_bera_p: float
    adf_t: float | None
    adf_lags: int | None
    kpss_stat: float | None
    stationarity: str  # stationary | unit_root | break_suspected | inconclusive
    volatility_ratio: float | None  # std of changes over the last 36 months / std of changes over the full period
    acf12: float | None
    recommendations: tuple[str, ...] = field(default_factory=tuple)
    managed_path: bool = False  # controlled/constant-pace path (e.g. TCMB's exchange rate management)


# MacKinnon (2010) response surface, single variable with constant: CV(n) = b0 + b1/n + b2/n² + b3/n³
_MACKINNON_C = {
    "1%": (-3.43035, -6.5393, -16.786, -79.433),
    "5%": (-2.86154, -2.8903, -4.234, -40.040),
    "10%": (-2.56677, -1.5384, -2.809, 0.0),
}


def adf_critical(n: int, level: str = "5%") -> float:
    b0, b1, b2, b3 = _MACKINNON_C[level]
    return b0 + b1 / n + b2 / n**2 + b3 / n**3


def _adf_design(y: np.ndarray, p: int, start: int) -> tuple[np.ndarray, np.ndarray]:
    """Δy_t = α + γ y_{t−1} + Σ_{i≤p} δ_i Δy_{t−i}; rows t = start…T−1 (in the Δ index)."""
    dy = np.diff(y)
    rows = range(start, len(dy))
    x = np.array([[1.0, y[t]] + [dy[t - i] for i in range(1, p + 1)] for t in rows])
    return x, dy[start:]


def _ols_t(x: np.ndarray, target: np.ndarray) -> tuple[float, float] | None:
    beta, *_ = np.linalg.lstsq(x, target, rcond=None)
    resid = target - x @ beta
    n, k = x.shape
    if n - k <= 0:
        return None
    ssr = float(resid @ resid)
    try:
        cov = ssr / (n - k) * np.linalg.inv(x.T @ x)
    except np.linalg.LinAlgError:
        return None
    se = math.sqrt(cov[1, 1]) if cov[1, 1] > 0 else float("nan")
    aic = n * math.log(ssr / n) + 2 * k if ssr > 0 else float("-inf")
    return float(beta[1] / se), aic


def _adf(y: np.ndarray) -> tuple[float, int, int] | None:
    """ADF (with constant). Lag: chosen by AIC on a COMMON sample within the Schwert upper bound ⌊12(T/100)^¼⌋,
    then the selected p is re-estimated on all usable observations (same logic as statsmodels adfuller)."""
    n = len(y)
    if n < 30:
        return None
    p_max = min(int(12 * (n / 100) ** 0.25), n // 4)
    best: tuple[float, int] | None = None
    for p in range(p_max + 1):
        fit = _ols_t(*_adf_design(y, p, p_max))  # common sample: the first p_max observations are dropped
        if fit is not None and (best is None or fit[1] < best[0]):
            best = (fit[1], p)
    if best is None:
        return None
    p = best[1]
    x, target = _adf_design(y, p, p)
    fit = _ols_t(x, target)
    if fit is None:
        return None
    return fit[0], p, len(target)


def _kpss(y: np.ndarray) -> float | None:
    n = len(y)
    if n < 30:
        return None
    e = y - y.mean()
    s = np.cumsum(e)
    lags = int(4 * (n / 100) ** 0.25)
    s2 = e @ e / n
    for lag in range(1, lags + 1):
        w = 1 - lag / (lags + 1)  # Bartlett
        s2 += 2 * w * (e[lag:] @ e[:-lag]) / n
    if s2 <= 0:
        return None
    return float((s @ s) / (n**2 * s2))


def _classify(adf_t: float | None, kpss: float | None, adf_n: int | None = None) -> str:
    if adf_t is None or kpss is None or math.isnan(adf_t):
        return "inconclusive"
    adf_rejects = adf_t < (adf_critical(adf_n) if adf_n else ADF_CRIT_5)
    kpss_rejects = kpss > KPSS_CRIT_5
    if adf_rejects and not kpss_rejects:
        return "stationary"
    if not adf_rejects and kpss_rejects:
        return "unit_root"
    if adf_rejects and kpss_rejects:
        return "break_suspected"
    return "inconclusive"


def _recommend(d: dict, transform: str | None) -> list[str]:
    recs = []
    already_changed = transform in {"log_diff", "diff", "yoy_pct"}
    if d["stationarity"] == "unit_root" and not already_changed:
        if d["min"] > 0:
            recs.append("Unit root var: düzey yerine log farkı (log_diff) ya da yıllık % değişimi kullanın.")
        else:
            recs.append("Unit root var: düzey yerine farkı (diff) kullanın; ağaç tabanlı modellerde düzey kalabilir.")
    if d["stationarity"] == "break_suspected":
        recs.append("ADF ve KPSS ikisi de reddediyor: structural break olasılığı yüksek. Rejim dummy'si ekleyin ya da örneklemi bölün.")
    if d["excess_kurtosis"] > 3:
        recs.append("Heavy tail: doğrusal modellerde robust scaling (medyan/IQR) ya da %1–%99 winsorizing uygulayın; şok aylarını silmeyin, dummy ile işaretleyin.")
    if abs(d["skew"]) > 1 and d["min"] > 0 and not already_changed:
        recs.append("Belirgin skewness ve pozitif değerler: log dönüşümü dağılımı daha simetrik hale getirir.")
    if d["volatility_ratio"] is not None and (d["volatility_ratio"] > 2 or d["volatility_ratio"] < 0.5):
        recs.append(f"Volatilite rejimi değişmiş (değişimlerin std'si, son 36 ay / tüm dönem = {tr_num(d['volatility_ratio'], '.2f')}): ağırlıklı ya da rolling window ile eğitimi veya bir rejim değişkenini değerlendirin.")
    if d["acf12"] is not None and d["acf12"] > 0.4 and transform not in {"yoy_pct"} and d["stationarity"] == "stationary":
        recs.append("12. lag'de güçlü otokorelasyon var; bu mevsimselliğe işaret eder. Ay dummy'leri ya da yıllık değişim kullanın.")
    return recs


def _managed_path(y: np.ndarray, transform: str | None, vol: float | None) -> bool:
    """In a change series (log_diff/diff), the last 12 months: positive and stable mean (coefficient of variation < 0.5)
    and volatility low relative to history (ratio < 0.5). This is the typical signature of periods in which TCMB kept
    the exchange rate on a set path in Türkiye; the random-walk assumption is weak here, while the risk of a sudden
    break (devaluation) lies in the tail."""
    if transform not in {"log_diff", "diff"} or len(y) < 24 or vol is None:
        return False
    last = y[-12:]
    mean, std = float(last.mean()), float(last.std(ddof=1))
    return mean > 0 and std / mean < 0.5 and vol < 0.5


def diagnose(series: pd.Series, name: str, transform: str | None = None) -> Diagnostics:
    s = series.dropna().astype(float)
    y = s.to_numpy()
    n = len(y)
    if n < 3:
        return Diagnostics(name, n, *(float("nan"),) * 8, None, None, None, "inconclusive", None, None, ("Gözlem sayısı yetersiz.",))
    std = float(y.std(ddof=1))
    z = (y - y.mean()) / std if std > 0 else np.zeros(n)
    skew = float((z**3).mean())
    kurt = float((z**4).mean() - 3)
    jb = n / 6 * (skew**2 + kurt**2 / 4)
    adf = _adf(y)
    kpss = _kpss(y)
    vol = None
    changes = np.diff(y)  # volatility from changes: the std of the level says nothing about the regime
    if n >= 72 and changes.std(ddof=1) > 0:
        vol = float(changes[-36:].std(ddof=1) / changes.std(ddof=1))
    acf12 = float(s.autocorr(lag=12)) if n > 36 else None
    stationarity = _classify(adf[0] if adf else None, kpss, adf[2] if adf else None)
    facts = {"stationarity": stationarity, "excess_kurtosis": kurt, "skew": skew, "min": float(y.min()),
             "volatility_ratio": vol, "acf12": acf12}  # fmt: skip
    return Diagnostics(
        feature=name, n=n, mean=float(y.mean()), std=std, median=float(np.median(y)),
        p01=float(np.percentile(y, 1)), p99=float(np.percentile(y, 99)), skew=round(skew, 3),
        excess_kurtosis=round(kurt, 3), jarque_bera_p=float(math.exp(-jb / 2)),
        adf_t=round(adf[0], 3) if adf else None, adf_lags=adf[1] if adf else None,
        kpss_stat=round(kpss, 3) if kpss is not None else None, stationarity=stationarity,
        volatility_ratio=round(vol, 3) if vol is not None else None,
        acf12=round(acf12, 3) if acf12 is not None and not math.isnan(acf12) else None,
        recommendations=tuple(_recommend(facts, transform) + (
            ["Kontrollü seyir: son 12 ayda değişim pozitif ve istikrarlı, volatilite düşük (ör. yönetilen kur). "
             "Son dönemin ortalama değişimi uzun dönem ortalamasından daha temsil edicidir; kuyruk riski (ani sıçrama) "
             "için geçmiş structural break dönemlerini (2018-08, 2021-12, 2023-06, 2025-03) dikkate alın."]
            if _managed_path(y, transform, vol) else []
        )),
        managed_path=_managed_path(y, transform, vol),
    )  # fmt: skip


def diagnose_panel(panel: pd.DataFrame, transforms: dict[str, str] | None = None) -> tuple[Diagnostics, ...]:
    transforms = transforms or {}
    return tuple(diagnose(panel[c], c, transforms.get(c)) for c in panel.columns)
