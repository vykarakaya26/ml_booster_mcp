"""Market share analysis: shares, concentration, entity dynamics, competitor set, segment decomposition.

The input is a long-format table: `entity, date, value` and an optional `segment`. `value` is given either as an
amount/volume (measure="value") or as a percentage share (measure="share"). If the market total is known it is
added as rows named `total_entity` (default `TOPLAM`; `TOTAL` is also accepted); shares are then computed against
that total and the coverage ratio is reported.

All measures are explainable, widely accepted definitions (HHI, CR-k, Hymer–Pashigian instability index,
shift-share decomposition). The functions are pure and perform no I/O.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.numfmt import tr_num, tr_pct
from mlbooster.domain.periods import parse_period as _parse_period

MAX_ROWS = 200_000
MAX_ENTITIES = 200
NAME_MAX = 80
HHI_LOW, HHI_HIGH = 1_000, 2_000  # thresholds of the EU Horizontal Merger Guidelines (2004/C 31/03)
SUBSTITUTION_R = -0.2
OVERLAP_MIN = 0.6
MIN_TREND_OBS = 6
MIN_TREND_PP = 0.2  # minimum magnitude for an annual trend to count as meaningful (percentage points/year)

MEASURES = ("value", "share")
KINDS = ("flow", "stock")
BASES = ("auto", "period", "ttm")

# Accepted input column names (Turkish aliases are kept: they are data, not code).
_ALIASES = {
    "entity": ("entity", "oyuncu", "firma", "sirket", "şirket", "marka", "kurum", "player", "company", "brand"),
    "date": ("date", "tarih", "donem", "dönem", "period"),
    "value": ("value", "deger", "değer", "tutar", "adet", "satis", "satış", "pay", "share"),
    "segment": ("segment", "kirilim", "kırılım", "kategori", "category"),
}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# Entity names that mark market total rows (case-insensitive); `TOTAL` is accepted as an English alias.
TOTAL_ALIASES = ("TOPLAM", "TOTAL")


@dataclass(frozen=True)
class ShareConfig:
    focus: str | None = None
    measure: str = "value"
    kind: str = "flow"
    total_entity: str = "TOPLAM"
    basis: str = "auto"
    trend_years: int = 3
    top_n: int = 8
    unit: str = ""

    def __post_init__(self) -> None:
        if self.measure not in MEASURES:
            raise InvalidInput(f"measure şu değerlerden biri olmalıdır: {', '.join(MEASURES)}")
        if self.kind not in KINDS:
            raise InvalidInput(f"kind şu değerlerden biri olmalıdır: {', '.join(KINDS)}")
        if self.basis not in BASES:
            raise InvalidInput(f"basis şu değerlerden biri olmalıdır: {', '.join(BASES)}")
        if not 1 <= self.trend_years <= 10:
            raise InvalidInput("trend_years 1 ile 10 arasında olmalıdır")
        if not 3 <= self.top_n <= 20:
            raise InvalidInput("top_n 3 ile 20 arasında olmalıdır")


@dataclass(frozen=True)
class Prepared:
    frame: pd.DataFrame  # entity, date, value, segment
    totals: pd.DataFrame  # date, segment, value (market total rows; may be empty)
    periods_per_year: int
    issues: list[dict]
    has_segments: bool


# ---------------------------------------------------------------------------------------------- input


def clean_name(raw: object) -> str:
    text = unicodedata.normalize("NFC", str(raw))
    text = " ".join(_CONTROL.sub(" ", text).split())
    if not text:
        raise InvalidInput("Oyuncu adı boş olamaz")
    if len(text) > NAME_MAX:
        raise InvalidInput(f"Oyuncu adı en fazla {NAME_MAX} karakter olabilir: {text[:40]}…")
    return text


def parse_period(raw: object) -> date:
    """Converts the `YYYY-MM`, `YYYY-MM-DD`, `YYYY-Qn` and `YYYY` formats to the period-end date."""
    if isinstance(raw, (pd.Timestamp, date)):
        return pd.Timestamp(raw).date()
    s = str(raw).strip()
    if parsed := _parse_period(s):
        return parsed[0]
    raise InvalidInput(f"Dönem biçimi tanınmadı: {s[:20]!r} (beklenen biçimler: YYYY-AA, YYYY-AA-GG, YYYY-Qn ya da YYYY)")


def infer_periods_per_year(dates: pd.Series | list[date]) -> int:
    uniq = sorted(set(pd.to_datetime(pd.Series(list(dates)))))
    if len(uniq) < 2:
        return 1
    gap = float(np.median(np.diff(np.array(uniq, dtype="datetime64[D]")).astype(float)))
    for limit, ppy in ((10, 52), (40, 12), (100, 4), (200, 2)):
        if gap <= limit:
            return ppy
    return 1


def _rename(df: pd.DataFrame) -> pd.DataFrame:
    lookup = {str(c).strip().casefold(): c for c in df.columns}
    mapping = {}
    for target, names in _ALIASES.items():
        for n in names:
            if n.casefold() in lookup:
                mapping[lookup[n.casefold()]] = target
                break
    out = df.rename(columns=mapping)
    missing = {"entity", "date", "value"} - set(out.columns)
    if missing:
        raise InvalidInput(f"Pazar payı tablosunda eksik kolon var: {', '.join(sorted(missing))} (beklenen kolonlar: entity, date, value[, segment])")
    return out


def prepare(raw: pd.DataFrame, cfg: ShareConfig) -> Prepared:
    if raw is None or raw.empty:
        raise InvalidInput("Pazar payı tablosu boş")
    if len(raw) > MAX_ROWS:
        raise InvalidInput(f"Pazar payı tablosu en fazla {tr_num(MAX_ROWS, ',')} satır olabilir")
    df = _rename(raw.copy())
    has_segments = "segment" in df.columns and df["segment"].notna().any()
    if has_segments and cfg.measure != "value":
        raise InvalidInput("Segment kırılımı yalnızca tutar/adet verisiyle (measure=value) analiz edilebilir")
    issues: list[dict] = []
    df["entity"] = df["entity"].map(clean_name)
    df["segment"] = df["segment"].map(lambda s: clean_name(s) if pd.notna(s) and str(s).strip() else "") if has_segments else ""
    df["date"] = df["date"].map(parse_period)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    dropped = int(df["value"].isna().sum())
    if dropped:
        issues.append(_issue("info", "missing_values", f"Sayısal olmayan ya da boş {dropped} değer analiz dışında bırakıldı."))
        df = df.dropna(subset=["value"])
    if (df["value"] < 0).any():
        bad = df.loc[df["value"] < 0, ["entity", "date"]].head(3).astype(str).agg(" ".join, axis=1).tolist()
        raise InvalidInput(f"Negatif değer içeren satırlar var: {'; '.join(bad)}")
    dup = df.duplicated(["entity", "date", "segment"], keep=False)
    if dup.any():
        bad = df.loc[dup, ["entity", "date"]].head(3).astype(str).agg(" ".join, axis=1).tolist()
        raise InvalidInput(f"Aynı oyuncu ve dönem için birden fazla satır var: {'; '.join(bad)}")
    markers = {cfg.total_entity.casefold()}
    if markers & {a.casefold() for a in TOTAL_ALIASES}:
        markers |= {a.casefold() for a in TOTAL_ALIASES}
    is_total = df["entity"].str.casefold().isin(markers)
    totals = df.loc[is_total, ["date", "segment", "value"]].reset_index(drop=True)
    frame = df.loc[~is_total, ["entity", "date", "value", "segment"]].reset_index(drop=True)
    if frame.empty:
        raise InvalidInput("Tabloda pazar toplamı dışında oyuncu satırı yok")
    n_entities = frame["entity"].nunique()
    if n_entities > MAX_ENTITIES:
        raise InvalidInput(f"En fazla {MAX_ENTITIES} oyuncu analiz edilebilir (tabloda {n_entities} oyuncu var)")
    if n_entities < 2:
        raise InvalidInput("Pazar payı analizi için en az iki oyuncu gerekir")
    if cfg.measure == "share" and frame["value"].max() <= 1.0001:
        frame["value"] = frame["value"] * 100
        issues.append(_issue("info", "share_scale", "Paylar 0–1 aralığında verildiği için yüzdeye çevrildi."))
    if cfg.focus is not None and cfg.focus.casefold() not in set(frame["entity"].str.casefold()):
        raise InvalidInput(f"Odak oyuncu tabloda yok: {cfg.focus}")
    ppy = infer_periods_per_year(frame["date"])
    _check_calendar(frame, ppy, issues)
    return Prepared(frame, totals, ppy, issues, bool(has_segments))


def _issue(severity: str, code: str, message: str) -> dict:
    return {"severity": severity, "check": code, "message": message}


def _check_calendar(frame: pd.DataFrame, ppy: int, issues: list[dict]) -> None:
    dates = sorted(frame["date"].unique())
    freq = {52: "W-FRI", 12: "ME", 4: "QE", 2: "6ME", 1: "YE"}[ppy]
    if ppy == 52:
        return
    expected = pd.date_range(dates[0], dates[-1], freq=freq).date
    gaps = sorted(set(expected) - set(dates))
    if gaps:
        issues.append(_issue("medium", "missing_periods", f"Takvimde {len(gaps)} dönem eksik (ilki: {gaps[0]}). "
                             "Bu dönemler için pay hesaplanmadı."))
    per_entity = frame.groupby("entity")["date"].nunique()
    partial = per_entity[per_entity < len(dates)]
    if len(partial):
        issues.append(_issue(
            "info", "partial_entity",
            f"{len(partial)} oyuncunun bazı dönemlerde kaydı yok (ör. {', '.join(partial.index[:3])}). "
            "Bu dönemler tutar verisinde sıfır kabul edildi, pay verisinde boş bırakıldı.",
        ))  # fmt: skip


# --------------------------------------------------------------------------------------------- shares


@dataclass(frozen=True)
class ShareTables:
    shares: pd.DataFrame  # period × entity, percentage share on the selected basis
    period_shares: pd.DataFrame  # period × entity, per-period share (for substitution analysis)
    values: pd.DataFrame | None  # period × entity, amount on the selected basis (measure=value)
    market: pd.DataFrame  # date, listed, market, coverage, residual
    basis: str


def _wide(frame: pd.DataFrame, fill_zero: bool) -> pd.DataFrame:
    w = frame.groupby(["date", "entity"])["value"].sum().unstack("entity").sort_index()
    w.index = pd.to_datetime(w.index)
    return w.fillna(0.0) if fill_zero else w


def resolve_basis(cfg: ShareConfig, ppy: int, n_dates: int) -> str:
    if cfg.basis != "auto":
        if cfg.basis == "ttm" and (cfg.measure != "value" or cfg.kind != "flow"):
            raise InvalidInput("Son 12 ay (ttm) tabanı yalnızca akım niteliğindeki tutar/adet verisinde kullanılabilir")
        return cfg.basis
    if cfg.measure == "value" and cfg.kind == "flow" and 1 < ppy <= 12 and n_dates >= 2 * ppy:
        return "ttm"
    return "period"


def compute_shares(prep: Prepared, cfg: ShareConfig) -> ShareTables:
    ppy = prep.periods_per_year
    if cfg.measure == "share":
        period = _wide(prep.frame, fill_zero=False)
        listed = period.sum(axis=1)
        market = pd.DataFrame({"listed": listed, "market": 100.0, "coverage": listed, "residual": (100 - listed).clip(lower=0)})
        if (listed > 100.5).any():
            raise InvalidInput(f"Payların toplamı %100'ü aşıyor (ör. {listed.idxmax().date()}: {tr_pct(listed.max())})")
        return ShareTables(period, period, None, market, "period")

    values = _wide(prep.frame, fill_zero=True)
    listed = values.sum(axis=1)
    totals = prep.totals.groupby("date")["value"].sum() if not prep.totals.empty else pd.Series(dtype=float)
    totals.index = pd.to_datetime(totals.index)
    market_total = totals.reindex(values.index).fillna(listed) if len(totals) else listed.copy()
    over = listed > market_total * 1.005
    if over.any():
        raise InvalidInput(f"Oyuncuların toplamı pazar toplamını aşıyor (ör. {over.idxmax().date()}); toplam satırlarını kontrol edin")
    period_shares = values.div(market_total.replace(0, np.nan), axis=0) * 100
    basis = resolve_basis(cfg, ppy, len(values))
    if basis == "ttm":
        v_b = values.rolling(ppy, min_periods=ppy).sum()
        m_b = market_total.rolling(ppy, min_periods=ppy).sum()
        l_b = listed.rolling(ppy, min_periods=ppy).sum()
        keep = m_b.notna()
        v_b, m_b, l_b = v_b[keep], m_b[keep], l_b[keep]
    else:
        v_b, m_b, l_b = values, market_total, listed
    shares = v_b.div(m_b.replace(0, np.nan), axis=0) * 100
    coverage = l_b / m_b.replace(0, np.nan) * 100
    market = pd.DataFrame({"listed": l_b, "market": m_b, "coverage": coverage, "residual": (100 - coverage).clip(lower=0)})
    return ShareTables(shares, period_shares, v_b, market, basis)


# ----------------------------------------------------------------------------------- concentration


def hhi_class(hhi: float) -> str:
    if hhi < HHI_LOW:
        return "low"
    return "moderate" if hhi <= HHI_HIGH else "high"


def concentration(tables: ShareTables, ppy: int) -> pd.DataFrame:
    s = tables.shares
    residual = tables.market["residual"].reindex(s.index).fillna(0.0)
    listed_sq = (s.fillna(0.0) ** 2).sum(axis=1)
    ordered = np.sort(s.fillna(0.0).to_numpy(), axis=1)[:, ::-1]
    out = pd.DataFrame(index=s.index)
    out["hhi"] = listed_sq
    out["hhi_upper"] = listed_sq + residual**2  # if the unlisted remainder is a single entity
    out["cr1"] = ordered[:, :1].sum(axis=1)
    out["cr3"] = ordered[:, :3].sum(axis=1)
    out["cr5"] = ordered[:, :5].sum(axis=1)
    out["active_players"] = (s.fillna(0.0) > 0).sum(axis=1)
    out["equivalent_players"] = 10_000 / out["hhi"].replace(0, np.nan)
    out["instability"] = 0.5 * (s - s.shift(ppy)).abs().sum(axis=1, min_count=1)
    out.loc[out.index[:ppy], "instability"] = np.nan
    return out


# ---------------------------------------------------------------------------------- entity dynamics


def _trend(y: pd.Series, ppy: int) -> tuple[float | None, float | None]:
    y = y.dropna()
    if len(y) < MIN_TREND_OBS:
        return None, None
    x = np.arange(len(y), dtype=float) / ppy
    slope, intercept = np.polyfit(x, y.to_numpy(dtype=float), 1)
    resid = y.to_numpy() - (slope * x + intercept)
    dof = len(y) - 2
    sxx = ((x - x.mean()) ** 2).sum()
    if dof <= 0 or sxx == 0:
        return float(slope), None
    se = math.sqrt((resid**2).sum() / dof / sxx)
    return float(slope), (float(slope / se) if se > 0 else None)


def trend_label(slope: float | None, t: float | None) -> str:
    if slope is None:
        return "insufficient_data"
    if t is not None and abs(t) >= 2 and abs(slope) >= MIN_TREND_PP:
        return "gaining" if slope > 0 else "losing"
    return "flat"


def player_dynamics(tables: ShareTables, ppy: int, cfg: ShareConfig) -> pd.DataFrame:
    s = tables.shares
    last = s.index[-1]
    cur = s.loc[last]
    prev = s.iloc[-2] if len(s) > 1 else pd.Series(np.nan, index=s.columns)
    year_ago = s.iloc[-1 - ppy] if len(s) > ppy else pd.Series(np.nan, index=s.columns)
    window = s.iloc[-cfg.trend_years * ppy :]
    # The trend is estimated from per-period shares: overlapping trailing 12-month sums would overstate the t-statistic.
    trend_window = tables.period_shares.loc[:last].iloc[-cfg.trend_years * ppy :]
    rank = cur.rank(ascending=False, method="min")
    rank_year = year_ago.rank(ascending=False, method="min")
    rows = []
    for entity in s.columns:
        share = cur[entity]
        if pd.isna(share):
            continue
        others = cur.drop(entity).max()
        slope, t = _trend(trend_window[entity] if entity in trend_window else window[entity], ppy)
        row = {
            "entity": entity,
            "share": share,
            "d_prev_pp": share - prev[entity],
            "d_year_pp": share - year_ago[entity],
            "rank": int(rank[entity]),
            "rank_year_ago": None if pd.isna(rank_year.get(entity)) else int(rank_year[entity]),
            "relative_share": share / others if others and not pd.isna(others) else None,
            "trend_pp_per_year": slope,
            "trend_t": t,
            "trend": trend_label(slope, t),
            "volatility_pp": float(window[entity].diff().std()) if window[entity].notna().sum() > 2 else None,
        }
        if tables.values is not None and len(tables.values) > ppy:
            v_now, v_ago = tables.values[entity].iloc[-1], tables.values[entity].iloc[-1 - ppy]
            m_now, m_ago = tables.market["market"].iloc[-1], tables.market["market"].iloc[-1 - ppy]
            growth = (v_now / v_ago - 1) * 100 if v_ago else None
            market_growth = (m_now / m_ago - 1) * 100 if m_ago else None
            row["growth_yoy_pct"] = growth
            row["growth_vs_market_pp"] = growth - market_growth if growth is not None and market_growth is not None else None
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["share", "entity"], ascending=[False, True]).reset_index(drop=True)


# ----------------------------------------------------------------------------------- competitor set


def substitution(tables: ShareTables, focus: str, ppy: int, years: int = 5) -> pd.DataFrame:
    """Correlation between the focus entity's per-period share changes and those of the other entities.

    A strong negative correlation indicates that the share gained by the focus comes from that entity (direct
    substitution). It is supporting evidence and does not establish causality.
    """
    d = tables.period_shares.diff().iloc[-years * ppy :]
    rows = []
    for entity in d.columns:
        if entity == focus:
            continue
        pair = d[[focus, entity]].dropna()
        n = len(pair)
        r = float(pair[focus].corr(pair[entity])) if n >= 3 and pair.std().min() > 0 else None
        threshold = 2 / math.sqrt(n) if n else None
        rows.append({
            "entity": entity, "r": r, "n": n,
            "significant": bool(r is not None and threshold is not None and n >= 12 and abs(r) >= threshold),
        })  # fmt: skip
    return pd.DataFrame(rows, columns=["entity", "r", "n", "significant"])


def segment_mix(values: pd.DataFrame) -> pd.DataFrame:
    """entity × segment → distribution of the entity's volume across segments (row sum 1)."""
    totals = values.sum(axis=1).replace(0, np.nan)
    return values.div(totals, axis=0).fillna(0.0)


def overlap(mix: pd.DataFrame, focus: str) -> pd.Series:
    """Bray–Curtis similarity: the common part of two entities' segment mix (0–1)."""
    base = mix.loc[focus]
    return mix.drop(index=focus).apply(lambda row: float(np.minimum(row, base).sum()), axis=1)


def competitor_candidates(players: pd.DataFrame, subst: pd.DataFrame, overlaps: pd.Series | None, focus: str) -> list[dict]:
    ordered = players.reset_index(drop=True)
    idx = ordered.index[ordered["entity"] == focus]
    reasons: dict[str, list[str]] = {}
    if len(idx):
        i = int(idx[0])
        for j in (i - 1, i + 1):
            if 0 <= j < len(ordered):
                reasons.setdefault(ordered.loc[j, "entity"], []).append("pay sıralamasında komşu")
        if i > 0:
            reasons.setdefault(ordered.loc[0, "entity"], []).append("pazar lideri")
    for row in subst.itertuples():
        if row.significant and row.r is not None and row.r <= SUBSTITUTION_R:
            reasons.setdefault(row.entity, []).append(f"pay değişimleri ters yönde (r={tr_num(row.r, '.2f')}, n={row.n})")
    if overlaps is not None:
        for entity, value in overlaps.drop(index=focus, errors="ignore").nlargest(3).items():
            if value >= OVERLAP_MIN:
                reasons.setdefault(entity, []).append(f"segment karması en çok örtüşen oyunculardan ({tr_pct(value * 100, '.0f')})")
    share = dict(zip(players["entity"], players["share"], strict=True))
    r_of = {row.entity: row.r for row in subst.itertuples() if row.r is not None and not pd.isna(row.r)}
    out = [{"entity": e, "share": share.get(e), "r": r_of.get(e), "reasons": r} for e, r in reasons.items() if e != focus]
    return sorted(out, key=lambda c: (-len(c["reasons"]), c["r"] if c["r"] is not None else 1.0, -(c["share"] or 0)))


# ------------------------------------------------------------------------------------------- segments


def segment_analysis(prep: Prepared, cfg: ShareConfig, basis: str, focus: str | None) -> dict | None:
    if not prep.has_segments:
        return None
    ppy = prep.periods_per_year
    long = prep.frame.groupby(["date", "segment", "entity"])["value"].sum()
    cube = long.unstack(["segment", "entity"]).sort_index().fillna(0.0)
    cube.index = pd.to_datetime(cube.index)
    listed_tot = cube.T.groupby(level="segment").sum().T
    seg_tot = listed_tot
    if not prep.totals.empty:
        given = prep.totals.groupby(["date", "segment"])["value"].sum().unstack("segment")
        given.index = pd.to_datetime(given.index)
        seg_tot = given.reindex(index=cube.index, columns=listed_tot.columns).fillna(listed_tot)
    if basis == "ttm":
        cube = cube.rolling(ppy, min_periods=ppy).sum().dropna(how="all")
        seg_tot = seg_tot.rolling(ppy, min_periods=ppy).sum().reindex(cube.index)
    if len(cube) == 0:
        return None
    weights = seg_tot.div(seg_tot.sum(axis=1).replace(0, np.nan), axis=0)
    last = cube.index[-1]
    ago = cube.index[-1 - ppy] if len(cube) > ppy else None
    segments = []
    for seg in seg_tot.columns:
        block = cube[seg]
        shares_now = block.loc[last] / (seg_tot.loc[last, seg] or np.nan) * 100
        row = {
            "segment": seg,
            "weight": float(weights.loc[last, seg] * 100),
            "hhi": float((shares_now.fillna(0.0) ** 2).sum()),
            "leader": str(shares_now.idxmax()) if shares_now.notna().any() else None,
            "leader_share": float(shares_now.max()) if shares_now.notna().any() else None,
        }
        if focus:
            row["focus_share"] = float(shares_now.get(focus, np.nan))
        if ago is not None:
            row["weight_d_pp"] = float((weights.loc[last, seg] - weights.loc[ago, seg]) * 100)
            if focus:
                prev = block.loc[ago].get(focus, np.nan) / (seg_tot.loc[ago, seg] or np.nan) * 100
                row["focus_share_d_pp"] = float(row["focus_share"] - prev)
        segments.append(row)
    out: dict = {"date": last.date().isoformat(), "segments": segments}
    if focus and ago is not None:
        out["shift_share"] = shift_share(cube, seg_tot, weights, focus, last, ago)
    if focus:
        mix = segment_mix(cube.loc[last].unstack("segment").fillna(0.0))
        if focus in mix.index:
            out["overlap"] = {k: round(v, 4) for k, v in overlap(mix, focus).items()}
    return out


def shift_share(cube: pd.DataFrame, seg_tot: pd.DataFrame, weights: pd.DataFrame, focus: str, last, ago) -> dict:
    """Shift-share: ΔS = Σ w̄·Δs (within-segment share effect) + Σ s̄·Δw (segment mix effect). Midpoint; decomposes exactly."""
    rows, share_eff, mix_eff = [], 0.0, 0.0
    for seg in seg_tot.columns:
        s1 = cube[seg].loc[last].get(focus, 0.0) / (seg_tot.loc[last, seg] or np.nan)
        s0 = cube[seg].loc[ago].get(focus, 0.0) / (seg_tot.loc[ago, seg] or np.nan)
        w1, w0 = weights.loc[last, seg], weights.loc[ago, seg]
        s1, s0 = (0.0 if pd.isna(s1) else s1), (0.0 if pd.isna(s0) else s0)
        a = (w1 + w0) / 2 * (s1 - s0) * 100
        b = (s1 + s0) / 2 * (w1 - w0) * 100
        share_eff += a
        mix_eff += b
        rows.append({"segment": seg, "share_effect_pp": a, "mix_effect_pp": b})
    return {"from": ago.date().isoformat(), "to": last.date().isoformat(), "share_effect_pp": share_eff,
            "mix_effect_pp": mix_eff, "total_pp": share_eff + mix_eff, "by_segment": rows}  # fmt: skip


# ------------------------------------------------------------------------------------- macro drivers


def to_share_frequency(panel: pd.DataFrame, ppy: int) -> pd.DataFrame:
    rule = {12: "ME", 4: "QE", 2: "6ME", 1: "YE"}.get(ppy)
    if rule is None:
        raise InvalidInput("Makro sürücü analizi haftalık pazar payı verisinde desteklenmiyor")
    numeric = panel.select_dtypes("number")
    return numeric if rule == "ME" else numeric.resample(rule).mean()


def driver_correlations(market_growth: pd.Series, panel: pd.DataFrame, ppy: int, max_lag: int = 2, min_obs: int = 8) -> list[dict]:
    """Lagged correlation between market growth and panel variables (variable t−lag, market t)."""
    target = market_growth.dropna()
    target.index = pd.to_datetime(target.index).to_period("M")
    feats = to_share_frequency(panel, ppy)
    feats.index = pd.to_datetime(feats.index).to_period("M")
    rows = []
    for col in feats.columns:
        for lag in range(max_lag + 1):
            pair = pd.concat([target.rename("y"), feats[col].shift(lag).rename("x")], axis=1, join="inner").dropna()
            if len(pair) < min_obs or pair["x"].std() == 0:
                continue
            rows.append({"feature": col, "lag": lag, "r": float(pair["y"].corr(pair["x"])), "n": len(pair)})
    best: dict[str, dict] = {}
    for r in rows:
        if r["feature"] not in best or abs(r["r"]) > abs(best[r["feature"]]["r"]):
            best[r["feature"]] = r
    return sorted(best.values(), key=lambda r: -abs(r["r"]))


# ------------------------------------------------------------------------------------------ analysis


def _clean(obj):
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (pd.Timestamp, date)):
        return obj.date().isoformat() if isinstance(obj, pd.Timestamp) else obj.isoformat()
    if isinstance(obj, (np.floating, float)):
        return None if not math.isfinite(float(obj)) else round(float(obj), 4)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def _series_payload(tables: ShareTables, players: pd.DataFrame, focus: str | None, top_n: int) -> dict:
    chosen = list(players["entity"].head(top_n))
    if focus and focus not in chosen:
        chosen.append(focus)
    s = tables.shares
    rest = s.drop(columns=chosen).sum(axis=1, min_count=1)
    series = {e: s[e].tolist() for e in chosen}
    if rest.notna().any():
        series["Diğer oyuncular"] = rest.tolist()
    if tables.market["residual"].fillna(0).gt(0.05).any():
        series["Listelenmeyen"] = tables.market["residual"].reindex(s.index).tolist()
    return {"dates": [d.date().isoformat() for d in s.index], "series": series}


def analyze(raw: pd.DataFrame, cfg: ShareConfig, panel: pd.DataFrame | None = None) -> dict:
    prep = prepare(raw, cfg)
    focus = None
    if cfg.focus:
        focus = next(e for e in prep.frame["entity"].unique() if e.casefold() == cfg.focus.casefold())
    tables = compute_shares(prep, cfg)
    if tables.shares.dropna(how="all").empty:
        raise InvalidInput("Pay hesaplanabilecek dönem yok (ttm tabanı için en az bir tam yıllık veri gerekir)")
    tables = ShareTables(tables.shares.dropna(how="all"), tables.period_shares, tables.values, tables.market, tables.basis)
    ppy = prep.periods_per_year
    issues = list(prep.issues)
    conc = concentration(tables, ppy)
    players = player_dynamics(tables, ppy, cfg)
    latest_cov = tables.market["coverage"].iloc[-1]
    if prep.totals.empty and cfg.measure == "value":
        issues.append(_issue("medium", "coverage_unknown",
                             "Pazar toplamı verilmedi; paylar yalnızca listelenen oyuncuların toplamına göre hesaplandı."))  # fmt: skip
    elif latest_cov < 80:
        issues.append(_issue("medium", "low_coverage", f"Listelenen oyuncular pazarın yalnızca {tr_pct(latest_cov)} kadarını kapsıyor."))

    market = tables.market.copy()
    market["growth_yoy_pct"] = (market["market"] / market["market"].shift(ppy) - 1) * 100 if cfg.measure == "value" else np.nan

    seg = segment_analysis(prep, cfg, tables.basis, focus)
    result: dict = {
        "meta": {
            "focus": focus, "measure": cfg.measure, "kind": cfg.kind, "unit": cfg.unit, "basis": tables.basis,
            "periods_per_year": ppy, "first": tables.shares.index[0], "last": tables.shares.index[-1],
            "entities": int(prep.frame["entity"].nunique()), "has_total": not prep.totals.empty,
            "has_segments": prep.has_segments, "thresholds": {"hhi_low": HHI_LOW, "hhi_high": HHI_HIGH},
        },  # fmt: skip
        "issues": issues,
        "market": {
            "dates": list(market.index), "total": market["market"].tolist(), "coverage": market["coverage"].tolist(),
            "growth_yoy_pct": market["growth_yoy_pct"].tolist(),
        },
        "concentration": {
            "dates": list(conc.index), **{c: conc[c].tolist() for c in conc.columns},
            "latest": {**conc.iloc[-1].to_dict(), "class": hhi_class(conc["hhi"].iloc[-1]),
                       "class_upper": hhi_class(conc["hhi_upper"].iloc[-1])},  # fmt: skip
            "year_ago": conc.iloc[-1 - ppy].to_dict() if len(conc) > ppy else None,
        },
        "players": players.to_dict("records"),
        "shares": _series_payload(tables, players, focus, cfg.top_n),
        "segments": seg,
    }
    if focus:
        subst = substitution(tables, focus, ppy)
        overlaps = pd.Series(seg["overlap"]) if seg and "overlap" in seg else None
        result["competitors"] = {
            "focus": focus,
            "substitution": subst.sort_values("r", na_position="last").to_dict("records"),
            "candidates": competitor_candidates(players, subst, overlaps, focus),
        }
    if panel is not None and not panel.empty and cfg.measure == "value":
        growth = market["growth_yoy_pct"].dropna()
        if ppy != 52 and len(growth) >= 8:
            result["drivers"] = driver_correlations(growth, panel, ppy)
    return _clean(result)
