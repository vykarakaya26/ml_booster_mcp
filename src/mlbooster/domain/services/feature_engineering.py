"""Otomatik ve sektöre özgü değişken üretimi ile sızıntısız ön seçim.

Üretim: gecikmeler, hareketli istatistikler, momentum, standart skor, üstel ortalama, sektör tarifleri
(faiz farkı, reel faiz, kur sepeti vb.), takvim (iş günü, bayram) ve bilinen olay göstergeleri. Bütün değişkenler
nedenseldir: t anındaki değer yalnızca t ve öncesindeki gözlemlerden hesaplanır.

Seçim (hedef verildiyse): yalnızca eğitim penceresinde, h dönem sonraki hedefle Spearman korelasyonu;
otokorelasyona göre düzeltilmiş etkin örneklem; Benjamini–Hochberg yanlış keşif oranı; alt dönemlerde
işaret istikrarı; |r| ≥ 0,95 olan çiftlerde fazlalık eleme ve gözlem sayısına göre üst sınır.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import FeatureRecipe, SectorEvent
from mlbooster.domain.services.calendar_tr import month_calendar

LAGS = (1, 2, 3, 6, 12)
WINDOWS = (3, 6, 12)
HALFLIVES = (3, 6)
REDUNDANCY_R = 0.95
FDR_Q = 0.10
MIN_STABILITY = 2 / 3
OBS_PER_FEATURE = 15  # Harrell (RMS §4.4): değişken sayısı < etkin örneklem / 15
MAX_FEATURES_CAP = 40
PERSISTENT_AC1 = 0.9  # hedefin 1. gecikme otokorelasyonu bunu aşıyorsa tarama değişime karşı yapılır

# Türkiye'de bilinen şok ve rejim dönemleri (olay göstergeleri). Darbe: yalnızca o ay 1; rejim: o aydan itibaren 1.
EVENTS: tuple[tuple[str, str, str], ...] = (
    ("olay_2018_08_kur_soku", "2018-08", "pulse"),
    ("olay_2020_03_covid", "2020-03", "pulse"),
    ("olay_2020_04_covid", "2020-04", "pulse"),
    ("olay_2021_12_kur_soku", "2021-12", "pulse"),
    ("rejim_2021_09_faiz_indirim", "2021-09", "step_until:2023-05"),
    ("rejim_2023_06_normallesme", "2023-06", "step"),
    ("olay_2023_02_deprem", "2023-02", "pulse"),
)


@dataclass(frozen=True)
class FeatureConfig:
    target: str | None = None
    horizon: int = 1
    train_end: date | None = None
    lags: tuple[int, ...] = LAGS
    windows: tuple[int, ...] = WINDOWS
    halflives: tuple[int, ...] = HALFLIVES
    calendar: bool = True
    events: bool = True
    max_features: int | None = None
    redundancy_r: float = REDUNDANCY_R
    label: str = "auto"  # auto | level | change

    def __post_init__(self) -> None:
        if not 1 <= self.horizon <= 24:
            raise InvalidInput("horizon 1 ile 24 arasında olmalıdır")
        if any(not 1 <= lag <= 36 for lag in self.lags) or any(not 2 <= w <= 60 for w in self.windows):
            raise InvalidInput("Gecikmeler 1–36, pencereler 2–60 dönem aralığında olmalıdır")
        if self.max_features is not None and not 1 <= self.max_features <= 200:
            raise InvalidInput("max_features 1 ile 200 arasında olmalıdır")
        if not 0.5 <= self.redundancy_r < 1:
            raise InvalidInput("redundancy_r 0,5 ile 1 arasında olmalıdır")
        if self.label not in {"auto", "level", "change"}:
            raise InvalidInput("label şu değerlerden biri olmalıdır: auto, level, change")


@dataclass
class FeatureSpec:
    name: str
    family: str
    inputs: tuple[str, ...]
    description: str
    params: dict = field(default_factory=dict)


@dataclass
class FeatureResult:
    features: pd.DataFrame
    specs: list[FeatureSpec]
    skipped: dict[str, str]
    screening: pd.DataFrame | None  # name, r, n, n_eff, p, q, stability, score, selected, reason
    selected: list[str]
    train_end: date | None
    label: str | None
    notes: list[str]


# ------------------------------------------------------------------------------------------- üretim


def _require_monthly(panel: pd.DataFrame) -> None:
    if not isinstance(panel.index, pd.DatetimeIndex) or len(panel) < 3:
        raise InvalidInput("Panel tarih indeksli ve en az üç dönem olmalıdır")
    gaps = np.diff(panel.index.values).astype("timedelta64[D]").astype(int)
    if np.median(gaps) < 27 or np.median(gaps) > 32:
        raise InvalidInput("Otomatik değişken üretimi aylık panel için tanımlıdır (build-panel --freq M)")


def recipe_series(panel: pd.DataFrame, r: FeatureRecipe) -> pd.Series:
    cols = [panel[c].astype(float) for c in r.inputs]
    if r.op == "spread":
        return cols[0] - cols[1]
    if r.op == "ratio":
        return cols[0] / cols[1].replace(0, np.nan)
    if r.op == "mean":
        return pd.concat(cols, axis=1).mean(axis=1, skipna=False)
    if r.op == "sum":
        return pd.concat(cols, axis=1).sum(axis=1, min_count=len(cols))
    if r.op == "product":
        return cols[0] * cols[1]
    past = cols[0].shift(1).rolling(r.window, min_periods=r.window)
    if r.op == "gap":
        return cols[0] - past.mean()
    return (cols[0] - past.mean()) / past.std().replace(0, np.nan)  # zscore


def _transforms(name: str, s: pd.Series, cfg: FeatureConfig, out: dict, specs: list[FeatureSpec]) -> None:
    def add(fname: str, family: str, values: pd.Series, desc: str, **params) -> None:
        out[fname] = values
        specs.append(FeatureSpec(fname, family, (name,), desc, params))

    for lag in cfg.lags:
        add(f"{name}__l{lag}", "gecikme", s.shift(lag), f"{name}, {lag} dönem önceki değer", lag=lag)
    for w in cfg.windows:
        roll = s.rolling(w, min_periods=w)
        add(f"{name}__ort{w}", "hareketli_ortalama", roll.mean(), f"{name}, son {w} dönem ortalaması", window=w)
        add(f"{name}__std{w}", "hareketli_oynaklik", roll.std(), f"{name}, son {w} dönem standart sapması", window=w)
    for k in (3, 12):
        add(f"{name}__deg{k}", "momentum", s - s.shift(k), f"{name}, {k} dönemlik değişim", periods=k)
    add(f"{name}__ivme", "ivme", s.diff() - s.diff().shift(1), f"{name}, değişimin değişimi")
    past = s.shift(1).rolling(24, min_periods=12)
    add(f"{name}__z24", "standart_skor", (s - past.mean()) / past.std().replace(0, np.nan),
        f"{name}, geçmiş 24 döneme göre standart skor", window=24)  # fmt: skip
    for hl in cfg.halflives:
        add(f"{name}__ewm{hl}", "ussel_ortalama", s.ewm(halflife=hl, min_periods=hl).mean(),
            f"{name}, yarı ömrü {hl} dönem olan üstel ortalama", halflife=hl)  # fmt: skip


def event_series(index: pd.DatetimeIndex, start: str, end: str | None, kind: str) -> pd.Series:
    periods = index.to_period("M")
    first = pd.Period(start, "M")
    if kind == "pulse":
        values = periods == first
    else:
        values = (periods >= first) & (periods <= pd.Period(end, "M") if end else True)
    return pd.Series(np.asarray(values, dtype=float), index=index)


def generate(
    panel: pd.DataFrame,
    recipes: Iterable[FeatureRecipe],
    cfg: FeatureConfig,
    sector_events: Iterable[SectorEvent] = (),
) -> tuple[pd.DataFrame, list[FeatureSpec], dict[str, str]]:
    _require_monthly(panel)
    numeric = panel.select_dtypes("number").astype(float)
    flags = [c for c in numeric.columns if "__" in c]  # ön işlemden gelen gösterge kolonları olduğu gibi kalır
    base = [c for c in numeric.columns if c not in flags]
    out: dict[str, pd.Series] = {}
    specs: list[FeatureSpec] = []
    skipped: dict[str, str] = {}
    for c in base:
        out[c] = numeric[c]
        specs.append(FeatureSpec(c, "temel", (c,), "Panel değişkeni (dönüşümsüz)"))
    for c in flags:
        out[c] = numeric[c]
        specs.append(FeatureSpec(c, "gosterge", (c.split("__")[0],), "Ön işlem göstergesi (eksik/aykırı)"))
    derived: dict[str, pd.Series] = {}
    for r in recipes:
        missing = [c for c in r.inputs if c not in numeric.columns]
        if missing:
            skipped[r.name] = f"girdi panelde yok: {', '.join(missing)}"
            continue
        values = recipe_series(numeric, r)
        if values.notna().sum() < 12:
            skipped[r.name] = "12'den az gözlem"
            continue
        derived[r.name] = values
        out[r.name] = values
        specs.append(FeatureSpec(r.name, "sektor_tarifi", r.inputs, r.note or r.op, {"op": r.op, "window": r.window}))
    for c in [*base, *derived]:
        _transforms(c, out[c], cfg, out, specs)
    if cfg.calendar:
        cal = month_calendar(numeric.index)
        for c in cal.columns:
            out[c] = cal[c]
            specs.append(FeatureSpec(c, "takvim", (), CALENDAR_DESC.get(c, c)))
    if cfg.events:
        events = [(n, w, None if k in {"pulse", "step"} else k.split(":")[1], "pulse" if k == "pulse" else "step",
                   f"Bilinen olay göstergesi ({w}, {'darbe' if k == 'pulse' else 'rejim'})") for n, w, k in EVENTS]
        events += [(e.name, e.start, e.end, e.kind, e.note or e.name) for e in sector_events]  # fmt: skip
        for name, start, end, kind, desc in events:
            values = event_series(numeric.index, start, end, kind)
            if values.sum() == 0 or values.sum() == len(values):
                continue
            out[name] = values
            specs.append(FeatureSpec(name, "olay", (), desc, {"start": start, "end": end, "kind": kind}))
    return pd.DataFrame(out, index=numeric.index), specs, skipped


CALENDAR_DESC = {
    "ay_sin": "Ay (mevsimsellik), sinüs bileşeni",
    "ay_cos": "Ay (mevsimsellik), kosinüs bileşeni",
    "is_gunu": "Ayın iş günü sayısı (hafta sonu, resmî tatil ve bayramlar düşülmüş)",
    "is_gunu_fark": "İş günü sayısının 12 ay önceye göre farkı (yıllık değişimlerde takvim etkisi)",
    "ramazan_bayrami_gun": "Ay içindeki Ramazan Bayramı günü sayısı",
    "kurban_bayrami_gun": "Ay içindeki Kurban Bayramı günü sayısı",
    "ramazan_ayi_gun": "Ay içindeki Ramazan ayı günü sayısı (yaklaşık, ±1 gün)",
    "bayram_oncesi_gun": "Ay içindeki bayram öncesi 7 günlük pencere (alışveriş ve seyahat etkisi)",
}


# --------------------------------------------------------------------------------------------- seçim


def _rank(x: np.ndarray) -> np.ndarray:
    return pd.Series(x).rank().to_numpy()


def _ac1(x: np.ndarray) -> float:
    if len(x) < 4 or np.std(x) == 0:
        return 0.0
    r = np.corrcoef(x[:-1], x[1:])[0, 1]
    return 0.0 if not np.isfinite(r) else float(r)


def effective_n(x: np.ndarray, y: np.ndarray) -> float:
    """Otokorelasyonlu iki seri için etkin örneklem: n·(1 − ρx·ρy) / (1 + ρx·ρy) (Bartlett; Bretherton vd. 1999)."""
    rho = _ac1(x) * _ac1(y)
    rho = min(max(rho, -0.95), 0.95)
    return max(3.0, len(x) * (1 - rho) / (1 + rho))


def _p_value(r: float, n_eff: float) -> float:
    if n_eff <= 3 or abs(r) >= 1:
        return 0.0 if abs(r) >= 1 else 1.0
    t = r * math.sqrt((n_eff - 2) / (1 - r * r))
    return math.erfc(abs(t) / math.sqrt(2))  # normal yaklaşımı, iki yönlü


def bh_qvalues(p: np.ndarray) -> np.ndarray:
    n = len(p)
    if n == 0:
        return p
    order = np.argsort(p)
    ranked = p[order] * n / np.arange(1, n + 1)
    q = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.minimum(q, 1.0)
    return out


def screen(features: pd.DataFrame, label: pd.Series, train_index: pd.Index, cfg: FeatureConfig, exclude: set[str]) -> tuple[pd.DataFrame, list[str]]:
    y_all = label.reindex(train_index)
    rows = []
    for name in features.columns:
        if name in exclude:
            continue
        pair = pd.concat([features[name].reindex(train_index), y_all], axis=1, keys=["x", "y"]).dropna()
        n = len(pair)
        if n < 24 or pair["x"].nunique() < 3:
            rows.append({"name": name, "n": n, "reason": "eğitimde yetersiz gözlem ya da değişkenlik"})
            continue
        xr, yr = _rank(pair["x"].to_numpy()), _rank(pair["y"].to_numpy())
        r = float(np.corrcoef(xr, yr)[0, 1])
        if not np.isfinite(r):
            rows.append({"name": name, "n": n, "reason": "korelasyon tanımsız"})
            continue
        n_eff = effective_n(pair["x"].to_numpy(), pair["y"].to_numpy())
        folds = np.array_split(np.arange(n), 3)
        same = 0
        for f in folds:
            if len(f) >= 8:
                rf = np.corrcoef(xr[f], yr[f])[0, 1]
                same += int(np.isfinite(rf) and np.sign(rf) == np.sign(r) and abs(rf) >= 0.1)
        rows.append({"name": name, "r": r, "n": n, "n_eff": n_eff, "p": _p_value(r, n_eff), "stability": same / 3})
    table = pd.DataFrame(rows)
    if "r" not in table or table["r"].notna().sum() == 0:
        return table.assign(selected=False), []
    valid = table["r"].notna()
    table.loc[valid, "q"] = bh_qvalues(table.loc[valid, "p"].to_numpy())
    table["score"] = table["r"].abs() * table["stability"]
    if "reason" not in table:
        table["reason"] = None
    table["reason"] = table["reason"].astype(object)
    n_train = int(table["n"].max())
    cap = cfg.max_features or min(MAX_FEATURES_CAP, max(3, n_train // OBS_PER_FEATURE))
    candidates = table[valid & (table["q"] <= FDR_Q) & (table["stability"] >= MIN_STABILITY)].sort_values("score", ascending=False)
    selected: list[str] = []
    reasons: dict[str, str] = {}
    ranked = features.reindex(train_index).rank()  # Spearman = sıralar üzerinde Pearson (scipy gerektirmez)
    for name in candidates["name"]:
        if len(selected) >= cap:
            reasons[name] = f"üst sınıra ulaşıldı ({cap} değişken)"
            continue
        clash = next((s for s in selected if abs(ranked[name].corr(ranked[s])) >= cfg.redundancy_r), None)
        if clash:
            reasons[name] = f"{clash} ile fazlalık (|r| ≥ {cfg.redundancy_r:g})"
            continue
        selected.append(name)
        reasons[name] = "seçildi"
    for idx, row in table.iterrows():
        if row["name"] in reasons:
            table.at[idx, "reason"] = reasons[row["name"]]
        elif pd.notna(row.get("r")):
            weak = []
            if row["q"] > FDR_Q:
                weak.append(f"q={row['q']:.2f} > {FDR_Q:g}")
            if row["stability"] < MIN_STABILITY:
                weak.append(f"alt dönem istikrarı {row['stability']:.2f}")
            table.at[idx, "reason"] = "; ".join(weak)
    table["selected"] = table["name"].isin(selected)
    return table.sort_values(["selected", "score"], ascending=[False, False], na_position="last").reset_index(drop=True), selected


def engineer(panel: pd.DataFrame, recipes: Iterable[FeatureRecipe], cfg: FeatureConfig, sector_events: Iterable[SectorEvent] = ()) -> FeatureResult:
    features, specs, skipped = generate(panel, recipes, cfg, sector_events)
    notes: list[str] = []
    if not cfg.target:
        return FeatureResult(features, specs, skipped, None, [], None, None, notes)
    if cfg.target not in panel.columns:
        raise InvalidInput(f"Hedef kolon panelde yok: {cfg.target}")
    y = panel[cfg.target].astype(float)
    mode = cfg.label
    if mode == "auto":
        mode = "change" if _ac1(y.dropna().to_numpy()) >= PERSISTENT_AC1 else "level"
    if mode == "change":
        label = y.shift(-cfg.horizon) - y
        label_name = f"{cfg.target}: {cfg.horizon} dönem sonraki değişim"
        notes.append(
            f"Hedef kalıcı bir seri (1. gecikme otokorelasyonu ≥ {PERSISTENT_AC1:g}); düzeyler arası korelasyon sahte ilişki "
            "üretebileceği için değişkenler hedefin h dönemlik değişimine karşı taranmıştır. Bu, değişkenin hedefin kendi "
            "geçmişinin ötesinde bilgi taşıyıp taşımadığını ölçer."
        )
    else:
        label = y.shift(-cfg.horizon)
        label_name = f"{cfg.target}: {cfg.horizon} dönem sonraki düzey"
    if cfg.train_end is None:
        labelled = label.dropna().index
        cut = labelled[int(len(labelled) * 0.8) - 1]
        notes.append(f"Eğitim penceresi sonu verilmedi; etiketli dönemlerin ilk %80'i ({cut.date()} dahil) kullanıldı.")
    else:
        cut = pd.Timestamp(cfg.train_end)
    # Etiket t+h'de gözlenir: eğitimde yalnızca etiketi eğitim sonuna kadar bilinen satırlar kullanılır.
    train_index = features.index[features.index <= cut - pd.offsets.MonthEnd(cfg.horizon)]
    if len(train_index) < 36:
        notes.append(f"Seçim için {len(train_index)} eğitim dönemi var; sonuçlar kırılgandır (en az 36 önerilir).")
    exclude = {cfg.target} if cfg.horizon == 0 else set()
    screening, selected = screen(features, label, train_index, cfg, exclude)
    notes.append(
        f"{len(screening)} aday tarandı. Çoklu karşılaştırma Benjamini–Hochberg (q ≤ {FDR_Q:g}) ile, otokorelasyon etkin "
        "örneklemle düzeltildi. Tarama ilişkiyi gösterir, nedenselliği göstermez; nihai değerlendirme backtest ile yapılmalıdır."
    )
    return FeatureResult(features, specs, skipped, screening, selected, cut.date(), label_name, notes)
