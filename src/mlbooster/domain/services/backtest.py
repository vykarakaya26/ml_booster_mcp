"""Rolling-origin (genişleyen pencere) backtest ile referans (baseline) tahmin modelleri.

Look-ahead koruması: `t` başlangıç noktasında yalnızca `t` ve öncesindeki satırlar kullanılır.
Panelin kendisi zaten nokta-zamanlıdır (yayın gecikmesi uygulanmış), bu yüzden `t` satırı
`t` ayının sonunda bilinen bilgiyi içerir. Hedef `t+h` için doğrudan (direct) strateji kullanılır.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from mlbooster.domain.errors import InvalidInput

MODELS = ("naive", "seasonal_naive", "drift", "ridge")


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


@dataclass(frozen=True)
class BacktestResult:
    config: BacktestConfig
    predictions: pd.DataFrame  # origin, date, model, y_true, y_pred
    metrics: pd.DataFrame  # model, n, mae, rmse, smape, mase
    features_used: tuple[str, ...] = field(default_factory=tuple)
    common_origins: int = 0  # metriklerin hesaplandığı ortak başlangıç noktası sayısı

    @property
    def best_model(self) -> str | None:
        return None if self.metrics.empty else str(self.metrics.sort_values("mae").iloc[0]["model"])


def _design(panel: pd.DataFrame, target: str, lags: tuple[int, ...], exogenous: bool) -> pd.DataFrame:
    columns = [target] + ([c for c in panel.columns if c != target] if exogenous else [])
    parts = {f"{c}_lag{lag}": panel[c].shift(lag) for c in columns for lag in lags}
    return pd.DataFrame(parts, index=panel.index)


def _ridge_fit_predict(x_train: np.ndarray, y_train: np.ndarray, x_new: np.ndarray, alpha: float) -> float:
    mean, std = x_train.mean(axis=0), x_train.std(axis=0)
    std[std == 0] = 1.0
    xs = (x_train - mean) / std
    y_mean = y_train.mean()
    k = xs.shape[1]
    beta = np.linalg.solve(xs.T @ xs + alpha * np.eye(k), xs.T @ (y_train - y_mean))
    return float(((x_new - mean) / std) @ beta + y_mean)


def _metrics(pred: pd.DataFrame, scale: float) -> pd.DataFrame:
    rows = []
    for model, g in pred.groupby("model", sort=False):
        err = g["y_pred"] - g["y_true"]
        denom = (g["y_true"].abs() + g["y_pred"].abs()).replace(0, np.nan)
        rows.append(
            {
                "model": model,
                "n": int(len(g)),
                "mae": float(err.abs().mean()),
                "rmse": float(np.sqrt((err**2).mean())),
                "smape": float((2 * err.abs() / denom).mean() * 100),
                "mase": float(err.abs().mean() / scale) if scale > 0 else float("nan"),
            }
        )
    return pd.DataFrame(rows).sort_values("mae").reset_index(drop=True)


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
    design = _design(panel, config.target, config.lags, config.exogenous) if "ridge" in config.models else None
    # Ridge özellikleri: tamamen boş kolonları at, kalan satırlarda eksik olanı kullanma.
    if design is not None:
        design = design.dropna(axis=1, how="all")

    rows = []
    positions = range(config.min_train - 1, len(panel) - h)
    for pos in positions:
        origin, target_date = panel.index[pos], panel.index[pos + h]
        y_true = y.iloc[pos + h]
        if pd.isna(y_true) or pd.isna(y.iloc[pos]):
            continue
        history = y.iloc[: pos + 1]
        preds: dict[str, float] = {}
        if "naive" in config.models:
            preds["naive"] = float(y.iloc[pos])
        if config.anchor is not None:
            value = panel[config.anchor].iloc[pos]
            if pd.notna(value):
                preds["anchor_naive"] = float(value)
        if "seasonal_naive" in config.models and pos + h - 12 >= 0 and h <= 12:
            value = y.iloc[pos + h - 12]
            if pd.notna(value):
                preds["seasonal_naive"] = float(value)
        if "drift" in config.models:
            hist = history.dropna()
            if len(hist) >= 2:
                slope = (hist.iloc[-1] - hist.iloc[0]) / (len(hist) - 1)
                preds["drift"] = float(hist.iloc[-1] + h * slope)
        if design is not None:
            # Eğitim çiftleri (X_s, y_{s+h}); s+h <= pos olmalı ki hedef origin anında bilinsin.
            # Hedef seviyesi değil h-adımlı değişim (y_{s+h} - y_s): durağan olmayan TR serilerinde
            # daha sağlam; tahmin naive'in üstüne eklenir, en kötü durumda naive'e yakın kalır.
            x_all = design.iloc[: pos + 1]
            base = panel[config.anchor] if config.anchor is not None else y  # değişim çapadan ölçülür
            change = (y.shift(-h) - base).iloc[: pos + 1]
            train = pd.concat([x_all, change.rename("__y")], axis=1).iloc[: pos + 1 - h].dropna()
            x_new = design.iloc[[pos]]
            if len(train) >= max(24, train.shape[1]) and not x_new.isna().any(axis=None) and pd.notna(base.iloc[pos]):
                delta = _ridge_fit_predict(
                    train.drop(columns="__y").to_numpy(), train["__y"].to_numpy(), x_new.to_numpy()[0], config.alpha
                )
                preds["ridge"] = float(base.iloc[pos]) + delta
        rows += [
            {"origin": origin, "date": target_date, "model": m, "y_true": float(y_true), "y_pred": p}
            for m, p in preds.items()
        ]

    predictions = pd.DataFrame(rows, columns=["origin", "date", "model", "y_true", "y_pred"])
    if predictions.empty:
        raise InvalidInput(f"Backtest için yeterli veri yok (min_train={config.min_train}, gözlem={y.notna().sum()})")
    # MASE ölçeği: ilk eğitim penceresinde h-adımlı naive hatanın ortalaması.
    train_y = y.iloc[: config.min_train].dropna()
    scale = float((train_y - train_y.shift(h)).abs().mean())
    # Adil karşılaştırma: metrikler yalnızca TÜM modellerin tahmin ürettiği başlangıç noktalarında hesaplanır
    # (ör. ridge, eksik exogenous veri yüzünden daha az noktada tahmin üretebilir).
    n_models = predictions["model"].nunique()
    per_origin = predictions.groupby("origin")["model"].nunique()
    common = per_origin[per_origin == n_models].index
    scored = predictions[predictions["origin"].isin(common)] if len(common) else predictions
    used = tuple(design.columns) if design is not None else ()
    return BacktestResult(config, predictions, _metrics(scored, scale), used, int(len(common)))
