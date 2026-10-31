"""Modellemeden önce dağılım teşhisi: şekil, durağanlık, oynaklık rejimi, mevsimsellik → dönüşüm önerisi.

Yalnızca numpy/pandas ile; testler ders kitabı formlarındadır (açıklanabilir olsun diye):
- ADF (sabitli; gecikme Schwert üst sınırı içinde AIC ile, ortak örneklemde) — H0: birim kök.
  Kritik değerler MacKinnon (2010) sonlu örneklem yanıt yüzeyinden (%5 asimptotik −2.86).
- KPSS (seviye) — H0: durağan. Kritik değerler: %10 0.347, %5 0.463, %1 0.739 (Kwiatkowski vd. 1992).
- İkisi birlikte okunur: ADF reddeder & KPSS reddetmez → durağan; tersi → birim kök;
  ikisi de reddeder → kırılma/fark-durağan şüphesi; hiçbiri → kararsız (kısa örneklem).
- Jarque-Bera: JB = n/6 (S² + K²/4); p ≈ exp(−JB/2) (χ²₂).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

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
    stationarity: str  # durağan | birim_kök | kırılma_şüphesi | kararsız
    volatility_ratio: float | None  # son 36 ayın değişim std'si / tüm dönem değişim std'si
    acf12: float | None
    recommendations: tuple[str, ...] = field(default_factory=tuple)
    managed_path: bool = False  # kontrollü/sabit hızlı patika (ör. TCMB'nin kur yönetimi)


# MacKinnon (2010) yanıt yüzeyi, sabitli tek değişken: CV(n) = b0 + b1/n + b2/n² + b3/n³
_MACKINNON_C = {
    "1%": (-3.43035, -6.5393, -16.786, -79.433),
    "5%": (-2.86154, -2.8903, -4.234, -40.040),
    "10%": (-2.56677, -1.5384, -2.809, 0.0),
}


def adf_critical(n: int, level: str = "5%") -> float:
    b0, b1, b2, b3 = _MACKINNON_C[level]
    return b0 + b1 / n + b2 / n**2 + b3 / n**3


def _adf_design(y: np.ndarray, p: int, start: int) -> tuple[np.ndarray, np.ndarray]:
    """Δy_t = α + γ y_{t−1} + Σ_{i≤p} δ_i Δy_{t−i}; satırlar t = start…T−1 (Δ indeksinde)."""
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
    """ADF (sabitli). Gecikme: Schwert üst sınırı ⌊12(T/100)^¼⌋, AIC ile ORTAK örneklemde seçilir,
    sonra seçilen p tüm kullanılabilir gözlemlerle yeniden kestirilir (statsmodels adfuller ile aynı mantık)."""
    n = len(y)
    if n < 30:
        return None
    p_max = min(int(12 * (n / 100) ** 0.25), n // 4)
    best: tuple[float, int] | None = None
    for p in range(p_max + 1):
        fit = _ols_t(*_adf_design(y, p, p_max))  # ortak örneklem: ilk p_max gözlem atılır
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
        return "kararsız"
    adf_rejects = adf_t < (adf_critical(adf_n) if adf_n else ADF_CRIT_5)
    kpss_rejects = kpss > KPSS_CRIT_5
    if adf_rejects and not kpss_rejects:
        return "durağan"
    if not adf_rejects and kpss_rejects:
        return "birim_kök"
    if adf_rejects and kpss_rejects:
        return "kırılma_şüphesi"
    return "kararsız"


def _recommend(d: dict, transform: str | None) -> list[str]:
    recs = []
    already_changed = transform in {"log_diff", "diff", "yoy_pct"}
    if d["stationarity"] == "birim_kök" and not already_changed:
        if d["min"] > 0:
            recs.append("Birim kök: seviye yerine log-fark (log_diff) ya da yıllık % değişim kullanın.")
        else:
            recs.append("Birim kök: seviye yerine fark (diff) kullanın; ağaç modellerinde seviye kalabilir.")
    if d["stationarity"] == "kırılma_şüphesi":
        recs.append("ADF ve KPSS ikisi de reddediyor: yapısal kırılma muhtemel — rejim dummy'si ekleyin veya örneklemi bölün.")
    if d["excess_kurtosis"] > 3:
        recs.append("Kalın kuyruk: doğrusal modellerde robust ölçekleme (medyan/IQR) veya %1–%99 winsorize; şok aylarını silmeyin, dummy ile işaretleyin.")
    if abs(d["skew"]) > 1 and d["min"] > 0 and not already_changed:
        recs.append("Belirgin çarpıklık ve pozitif değerler: log dönüşümü dağılımı simetrikleştirir.")
    if d["volatility_ratio"] is not None and (d["volatility_ratio"] > 2 or d["volatility_ratio"] < 0.5):
        recs.append(f"Oynaklık rejimi değişmiş (son 36 ay / tüm dönem değişim std'si = {d['volatility_ratio']:.2f}): ağırlıklı/kayan pencereli eğitim veya rejim değişkeni düşünün.")
    if d["acf12"] is not None and d["acf12"] > 0.4 and transform not in {"yoy_pct"} and d["stationarity"] == "durağan":
        recs.append("12. gecikmede güçlü otokorelasyon: mevsimsellik — ay dummy'leri veya yıllık değişim.")
    return recs


def _managed_path(y: np.ndarray, transform: str | None, vol: float | None) -> bool:
    """Değişim serisinde (log_diff/diff) son 12 ay: pozitif ve istikrarlı ortalama (değişim katsayısı < 0,5)
    ve oynaklık geçmişe göre düşük (oran < 0,5). TR'de TCMB'nin kuru belirli bir patikada tuttuğu dönemlerin
    tipik izi; rastgele yürüyüş varsayımı burada zayıftır, ani kırılma (devalüasyon) riski ise kuyruktadır."""
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
        return Diagnostics(name, n, *(float("nan"),) * 8, None, None, None, "kararsız", None, None, ("Yetersiz gözlem.",))
    std = float(y.std(ddof=1))
    z = (y - y.mean()) / std if std > 0 else np.zeros(n)
    skew = float((z**3).mean())
    kurt = float((z**4).mean() - 3)
    jb = n / 6 * (skew**2 + kurt**2 / 4)
    adf = _adf(y)
    kpss = _kpss(y)
    vol = None
    changes = np.diff(y)  # oynaklık değişimler üzerinden: seviyenin std'si rejim hakkında bilgi vermez
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
            ["Kontrollü patika: son 12 ayda değişim pozitif ve istikrarlı, oynaklık düşük (ör. yönetilen kur). "
             "Çıta olarak son dönem ortalama değişim (naive/drift_recent) kullanın; kuyruk riski (ani kırılma) için "
             "kantil aralığa ve geçmiş kırılma dönemlerine (2018-08, 2021-12, 2023-06, 2025-03) bakın."]
            if _managed_path(y, transform, vol) else []
        )),
        managed_path=_managed_path(y, transform, vol),
    )  # fmt: skip


def diagnose_panel(panel: pd.DataFrame, transforms: dict[str, str] | None = None) -> tuple[Diagnostics, ...]:
    transforms = transforms or {}
    return tuple(diagnose(panel[c], c, transforms.get(c)) for c in panel.columns)
