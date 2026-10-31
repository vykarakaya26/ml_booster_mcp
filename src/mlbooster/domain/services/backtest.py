"""Rolling-origin (genişleyen pencere) backtest ile referans (baseline) tahmin modelleri.

Look-ahead koruması: `t` başlangıç noktasında yalnızca `t` ve öncesindeki satırlar kullanılır.
Panelin kendisi zaten nokta-zamanlıdır (yayın gecikmesi uygulanmış), bu yüzden `t` satırı
`t` ayının sonunda bilinen bilgiyi içerir. Hedef `t+h` için doğrudan (direct) strateji kullanılır.

Modeller
- Basit çıtalar: naive, seasonal_naive, drift, drift_recent (son 6 ayın eğimi; kontrollü kur
  patikasını yakalar), mean12 (Atkeson–Ohanian), median12 (sapmalı veride dayanıklı), anchor_naive.
- ridge: gecikmeli, değişimi tabandan (çapa ya da son değer) öğrenir.
- qr: kantil regresyon (τ = 0.1/0.5/0.9); nokta tahmin medyandır, aralık modelin kendisinden gelir.
- Kombinasyonlar: combo_mean, combo_median ve auto_robust (geçmiş penceredeki sapma yüksekse
  medyan, değilse ortalama; karar yalnızca o güne kadarki veriyle verilir).
Aralıklar: qr dışındaki her model için %80 aralık, o modelin yalnızca GEÇMİŞTE gerçekleşmiş
hatalarının %10/%90 kantillerinden (ampirik/konformal benzeri) kurulur.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from mlbooster.domain.errors import InvalidInput

BASE_MODELS = ("naive", "seasonal_naive", "drift", "drift_recent", "mean12", "median12", "ridge", "qr")
COMBO_MODELS = ("combo_mean", "combo_median", "auto_robust")
MODELS = BASE_MODELS + COMBO_MODELS
# Kombinasyona girenler: mevsimsel naive YoY/oran serilerinde yapısal olarak kötü olduğu için dışarıda.
COMBO_MEMBERS = ("naive", "anchor_naive", "drift", "drift_recent", "mean12", "median12", "ridge", "qr")
QUANTILES = (0.1, 0.5, 0.9)
MIN_ERRORS_FOR_INTERVAL = 12

# Türkiye makro rejimleri: sonuçlar tam dönemle birlikte bu alt dönemlerde de raporlanır
# (dönem seçimiyle kiraz toplamayı önlemek ve rejim bağımlılığını göstermek için).
REGIMES: tuple[tuple[str, str | None, str | None], ...] = (
    ("2021-11 ve öncesi", None, "2021-11-30"),
    ("2021-12 – 2023", "2021-12-01", "2023-12-31"),
    ("2024 ve sonrası", "2024-01-01", None),
)


@dataclass(frozen=True)
class BacktestConfig:
    target: str
    horizon: int = 1
    min_train: int = 48
    lags: tuple[int, ...] = (0, 1, 2, 11)  # 0 = başlangıç ayının kendisi
    alpha: float = 10.0
    models: tuple[str, ...] = MODELS
    exogenous: bool = True  # ridge için diğer panel kolonlarını da kullan
    # Çapa: hedefin bilgi açısından daha güncel bir versiyonu (ör. aylık ortalama hedef için ay sonu değeri).
    # Verilirse "anchor_naive" modeli = anchor[t]; aylık ortalama hedeflerde gerçek çıta budur.
    anchor: str | None = None
    recent_window: int = 6  # drift_recent için ay
    # auto_robust: geçmiş değişimlerde fazla basıklık ya da aykırı oranı bu eşikleri aşarsa medyan kombinasyonu.
    robust_kurtosis: float = 3.0
    robust_outlier_share: float = 0.05

    def __post_init__(self) -> None:
        if not 1 <= self.horizon <= 24:
            raise InvalidInput("horizon 1–24 ay olmalı")
        if self.min_train < 24:
            raise InvalidInput("min_train en az 24 ay olmalı")
        if any(lag < 0 for lag in self.lags):
            raise InvalidInput("lags negatif olamaz")
        unknown = set(self.models) - set(MODELS)
        if unknown:
            raise InvalidInput(f"Bilinmeyen model(ler): {sorted(unknown)}; seçenekler: {list(MODELS)}")
        if self.alpha < 0:
            raise InvalidInput("alpha negatif olamaz")
        if self.anchor is not None and self.anchor == self.target:
            raise InvalidInput("anchor hedefle aynı kolon olamaz")
        if not 2 <= self.recent_window <= 36:
            raise InvalidInput("recent_window 2–36 ay olmalı")


@dataclass(frozen=True)
class BacktestResult:
    config: BacktestConfig
    predictions: pd.DataFrame  # origin, date, model, y_true, y_pred, lower, upper
    metrics: pd.DataFrame  # model, n, mae, rmse, smape, mase, coverage80, width80, pinball
    features_used: tuple[str, ...] = field(default_factory=tuple)
    common_origins: int = 0  # metriklerin hesaplandığı ortak başlangıç noktası sayısı
    regime_metrics: pd.DataFrame = field(default_factory=pd.DataFrame)  # regime, model, n, mae, rmse
    robust_share: float | None = None  # auto_robust'ın medyanı seçtiği başlangıçların oranı

    @property
    def best_model(self) -> str | None:
        return None if self.metrics.empty else str(self.metrics.sort_values("mae").iloc[0]["model"])


# --- tahminciler ----------------------------------------------------------------------------


def _design(panel: pd.DataFrame, columns: list[str], lags: tuple[int, ...]) -> pd.DataFrame:
    parts = {f"{c}_lag{lag}": panel[c].shift(lag) for c in columns for lag in lags}
    return pd.DataFrame(parts, index=panel.index).dropna(axis=1, how="all")


def _standardize(x_train: np.ndarray, x_new: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean, std = x_train.mean(axis=0), x_train.std(axis=0)
    std[std == 0] = 1.0
    return (x_train - mean) / std, (x_new - mean) / std


def _ridge_fit_predict(x_train: np.ndarray, y_train: np.ndarray, x_new: np.ndarray, alpha: float) -> float:
    xs, xn = _standardize(x_train, x_new)
    y_mean = y_train.mean()
    k = xs.shape[1]
    beta = np.linalg.solve(xs.T @ xs + alpha * np.eye(k), xs.T @ (y_train - y_mean))
    return float(xn @ beta + y_mean)


def quantile_fit_predict(
    x_train: np.ndarray, y_train: np.ndarray, x_new: np.ndarray, tau: float, alpha: float = 1.0, iters: int = 100
) -> float:
    """L2 cezalı lineer kantil regresyon, IRLS ile (pinball kaybının ağırlıklı en küçük kareler yaklaşımı).

    Ağırlık w_i = τ/|r_i| (r ≥ 0) ya da (1−τ)/|r_i| (r < 0). Sabit terim cezalandırılmaz; yalnız sabitle
    çözüm örneklem kantiline yakınsar (test edildi)."""
    xs, xn = _standardize(x_train, x_new)
    n, k = xs.shape
    design = np.column_stack([np.ones(n), xs])
    penalty = alpha * np.eye(k + 1)
    penalty[0, 0] = 0.0
    beta = np.linalg.lstsq(design, y_train, rcond=None)[0]
    eps = max(1e-6, 1e-6 * float(np.std(y_train)))
    for _ in range(iters):
        r = y_train - design @ beta
        w = np.where(r >= 0, tau, 1 - tau) / np.maximum(np.abs(r), eps)
        wd = design * w[:, None]
        new = np.linalg.solve(design.T @ wd + penalty, wd.T @ y_train)
        if np.max(np.abs(new - beta)) < 1e-9:
            beta = new
            break
        beta = new
    return float(np.concatenate([[1.0], xn]) @ beta)


def _is_dispersed(changes: np.ndarray, config: BacktestConfig) -> bool:
    """Geçmiş değişimler kalın kuyruklu ya da aykırı-yoğun mu? (yalnız geçmiş veri)"""
    c = changes[~np.isnan(changes)]
    if len(c) < 12 or np.std(c) == 0:
        return False
    z = (c - c.mean()) / c.std()
    kurt = float((z**4).mean() - 3)
    mad = np.median(np.abs(c - np.median(c)))
    outlier_share = float(np.mean(np.abs(0.6745 * (c - np.median(c)) / mad) > 3.5)) if mad > 0 else 0.0
    return kurt > config.robust_kurtosis or outlier_share > config.robust_outlier_share


# --- metrikler ------------------------------------------------------------------------------


def _pinball(y: np.ndarray, q: np.ndarray, tau: float) -> np.ndarray:
    d = y - q
    return np.maximum(tau * d, (tau - 1) * d)


def _metrics(pred: pd.DataFrame, scale: float) -> pd.DataFrame:
    rows = []
    for model, g in pred.groupby("model", sort=False):
        err = g["y_pred"] - g["y_true"]
        denom = (g["y_true"].abs() + g["y_pred"].abs()).replace(0, np.nan)
        row = {
            "model": model,
            "n": int(len(g)),
            "mae": float(err.abs().mean()),
            "rmse": float(np.sqrt((err**2).mean())),
            "smape": float((2 * err.abs() / denom).mean() * 100),
            "mase": float(err.abs().mean() / scale) if scale > 0 else float("nan"),
            "coverage80": float("nan"),
            "width80": float("nan"),
            "pinball": float("nan"),
            "n_interval": 0,
        }
        iv = g.dropna(subset=["lower", "upper"])
        if len(iv):
            y = iv["y_true"].to_numpy()
            row["coverage80"] = float(((y >= iv["lower"]) & (y <= iv["upper"])).mean())
            row["width80"] = float((iv["upper"] - iv["lower"]).mean())
            row["pinball"] = float(np.mean([
                _pinball(y, iv["lower"].to_numpy(), 0.1).mean(),
                _pinball(y, iv["y_pred"].to_numpy(), 0.5).mean(),
                _pinball(y, iv["upper"].to_numpy(), 0.9).mean(),
            ]))  # fmt: skip
            row["n_interval"] = int(len(iv))
        rows.append(row)
    return pd.DataFrame(rows).sort_values("mae").reset_index(drop=True)


def _regime_metrics(pred: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, lo, hi in REGIMES:
        mask = pd.Series(True, index=pred.index)
        if lo:
            mask &= pred["date"] >= pd.Timestamp(lo)
        if hi:
            mask &= pred["date"] <= pd.Timestamp(hi)
        part = pred[mask]
        for model, g in part.groupby("model", sort=False):
            err = g["y_pred"] - g["y_true"]
            rows.append({"regime": name, "model": model, "n": int(len(g)),
                         "mae": float(err.abs().mean()), "rmse": float(np.sqrt((err**2).mean()))})  # fmt: skip
    return pd.DataFrame(rows, columns=["regime", "model", "n", "mae", "rmse"])


# --- ana döngü ------------------------------------------------------------------------------


def rolling_origin_backtest(panel: pd.DataFrame, config: BacktestConfig) -> BacktestResult:
    if config.target not in panel.columns:
        raise InvalidInput(f"Hedef kolon panelde yok: {config.target}. Kolonlar: {list(panel.columns)}")
    if config.anchor is not None and config.anchor not in panel.columns:
        raise InvalidInput(f"Çapa kolon panelde yok: {config.anchor}. Kolonlar: {list(panel.columns)}")
    panel = panel.sort_index()
    y = panel[config.target]
    first_valid = y.first_valid_index()
    if first_valid is None:
        raise InvalidInput(f"{config.target} serisi boş")
    panel = panel.loc[first_valid:]
    y = panel[config.target]
    h = config.horizon
    models = set(config.models)
    base = panel[config.anchor] if config.anchor is not None else y  # değişimler bu tabandan ölçülür
    change = y.shift(-h) - base

    own_cols = [config.target] + ([config.anchor] if config.anchor else [])
    ridge_cols = own_cols + ([c for c in panel.columns if c not in own_cols] if config.exogenous else [])
    ridge_x = _design(panel, ridge_cols, config.lags) if "ridge" in models else None
    # Kantil regresyon kısa örneklemde aşırı uyuma açık: yalnız hedefin (ve çapanın) gecikmeleri.
    qr_x = _design(panel, own_cols, config.lags) if "qr" in models else None

    rows: list[dict] = []
    past_errors: dict[str, list[tuple[pd.Timestamp, float]]] = {}
    robust_choices: list[bool] = []
    for pos in range(config.min_train - 1, len(panel) - h):
        origin, target_date = panel.index[pos], panel.index[pos + h]
        y_true = y.iloc[pos + h]
        if pd.isna(y_true) or pd.isna(y.iloc[pos]):
            continue
        history = y.iloc[: pos + 1]
        hist = history.dropna()
        preds: dict[str, float] = {}
        bands: dict[str, tuple[float, float]] = {}

        if "naive" in models:
            preds["naive"] = float(y.iloc[pos])
        if config.anchor is not None and pd.notna(base.iloc[pos]):
            preds["anchor_naive"] = float(base.iloc[pos])
        if "seasonal_naive" in models and pos + h - 12 >= 0 and h <= 12 and pd.notna(y.iloc[pos + h - 12]):
            preds["seasonal_naive"] = float(y.iloc[pos + h - 12])
        if "mean12" in models and len(history.iloc[-12:].dropna()) == 12:
            preds["mean12"] = float(history.iloc[-12:].mean())  # Atkeson–Ohanian
        if "median12" in models and len(history.iloc[-12:].dropna()) == 12:
            preds["median12"] = float(history.iloc[-12:].median())  # sapmalı veride dayanıklı
        if "drift" in models and len(hist) >= 2:
            preds["drift"] = float(hist.iloc[-1] + h * (hist.iloc[-1] - hist.iloc[0]) / (len(hist) - 1))
        if "drift_recent" in models and len(hist) > config.recent_window:
            w = config.recent_window
            preds["drift_recent"] = float(hist.iloc[-1] + h * (hist.iloc[-1] - hist.iloc[-1 - w]) / w)

        train_end = pos + 1 - h  # s + h <= pos olan eğitim satırları
        if pd.notna(base.iloc[pos]):
            if ridge_x is not None:
                train = pd.concat([ridge_x, change.rename("__y")], axis=1).iloc[:train_end].dropna()
                x_new = ridge_x.iloc[[pos]]
                if len(train) >= max(24, train.shape[1]) and not x_new.isna().any(axis=None):
                    preds["ridge"] = float(base.iloc[pos]) + _ridge_fit_predict(
                        train.drop(columns="__y").to_numpy(), train["__y"].to_numpy(), x_new.to_numpy()[0], config.alpha
                    )
            if qr_x is not None:
                train = pd.concat([qr_x, change.rename("__y")], axis=1).iloc[:train_end].dropna()
                x_new = qr_x.iloc[[pos]]
                if len(train) >= max(36, 3 * train.shape[1]) and not x_new.isna().any(axis=None):
                    xt, yt, xn = train.drop(columns="__y").to_numpy(), train["__y"].to_numpy(), x_new.to_numpy()[0]
                    q = sorted(quantile_fit_predict(xt, yt, xn, tau) for tau in QUANTILES)  # monotonluk
                    b = float(base.iloc[pos])
                    preds["qr"] = b + q[1]
                    bands["qr"] = (b + q[0], b + q[2])

        members = [preds[m] for m in COMBO_MEMBERS if m in preds]
        if len(members) >= 2:
            if "combo_mean" in models:
                preds["combo_mean"] = float(np.mean(members))
            if "combo_median" in models:
                preds["combo_median"] = float(np.median(members))
            if "auto_robust" in models:
                dispersed = _is_dispersed(np.diff(hist.to_numpy()), config)
                robust_choices.append(dispersed)
                preds["auto_robust"] = float(np.median(members) if dispersed else np.mean(members))

        # Ampirik aralık: modelin yalnızca origin anında gerçekleşmiş (hedef tarihi ≤ origin) geçmiş hataları.
        for m, p in preds.items():
            if m in bands:
                continue
            known = [e for d, e in past_errors.get(m, []) if d <= origin]
            if len(known) >= MIN_ERRORS_FOR_INTERVAL:
                lo, hi = np.quantile(known, [0.1, 0.9])
                bands[m] = (p + float(lo), p + float(hi))

        for m, p in preds.items():
            lo, hi = bands.get(m, (np.nan, np.nan))
            rows.append({"origin": origin, "date": target_date, "model": m, "y_true": float(y_true),
                         "y_pred": p, "lower": lo, "upper": hi})  # fmt: skip
            past_errors.setdefault(m, []).append((target_date, float(y_true) - p))

    predictions = pd.DataFrame(rows, columns=["origin", "date", "model", "y_true", "y_pred", "lower", "upper"])
    if predictions.empty:
        raise InvalidInput(f"Backtest için yeterli veri yok (min_train={config.min_train}, gözlem={y.notna().sum()})")
    # MASE ölçeği: ilk eğitim penceresinde h-adımlı naive hatanın ortalaması.
    train_y = y.iloc[: config.min_train].dropna()
    scale = float((train_y - train_y.shift(h)).abs().mean())
    # Adil karşılaştırma: metrikler yalnızca TÜM modellerin tahmin ürettiği başlangıç noktalarında hesaplanır.
    n_models = predictions["model"].nunique()
    per_origin = predictions.groupby("origin")["model"].nunique()
    common = per_origin[per_origin == n_models].index
    scored = predictions[predictions["origin"].isin(common)] if len(common) else predictions
    used = tuple(ridge_x.columns) if ridge_x is not None else ()
    return BacktestResult(
        config,
        predictions,
        _metrics(scored, scale),
        used,
        int(len(common)),
        _regime_metrics(scored),
        float(np.mean(robust_choices)) if robust_choices else None,
    )
