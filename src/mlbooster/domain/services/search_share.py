"""Search-interest layer of the brand flow: which brand, competitor and category terms appear in the Google Trends
lists, and what can honestly be derived from them.

Limits of the source (bigquery-public-data.google_trends, verified live 2026-11-04):
- Only terms that reached a daily Top 25 / Top 25 Rising list exist, and only the last ~30 lists are kept.
- Every (term, province) series is scaled on its own: all 26 terms of a day had a maximum of exactly 100 in every
  province with data. Levels therefore compare neither across terms nor across provinces, so a share of search
  (brand ÷ brand + competitors) cannot be computed from them. It is withheld. What stays comparable: list days and list
  rank (one ranking of all terms of the day), and scale-free changes of one term against its own past.
- A backfill comes from one recent refresh: its maximum is set by weeks after the row, and the term is there because
  it trends now. Levels of past rows are not point-in-time; ratios to the term's own past do not depend on the scale.

Nothing is filled: a term without list days is `not_found`, which does not mean nobody searched for it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from mlbooster.domain.model import Frequency
from mlbooster.domain.services.brand_resolution import fold
from mlbooster.domain.services.frequency import to_monthly

BRAND, COMPETITOR, GENERIC = "brand", "competitor", "generic"
FOUND, RELATED, NOT_FOUND = "found", "related", "not_found"

MIN_RELATED_LENGTH = 3  # shorter spellings (TT, PO, VW) only match a term exactly
PER_SERIES_SHARE = 0.95  # series at exactly 100 at or above this share: scaled one by one
MIN_ACTIVE_WEEKS = 12  # fewer weeks with interest: a one-off spike, not a usable series
RECENT_WEEKS = 4
BASE_WEEKS = 52
MIN_LIST_DAYS_PER_MONTH = 25  # a month with fewer retained lists has no list feature
MAX_PROVINCES = 10

PROXY_NOTE = "Arama ilgisi; pazar payı değildir."
ARAMA_PAYI_WITHHELD = (
    "arama_payi hesaplanmadı. Google Trends her terimi ve her ili kendi içinde 0–100 arasına ölçekler; farklı terimlerin "
    "skorları birbirine oranlanamaz. Karşılaştırma için liste günü sayısı ve liste sırası kullanılır."
)
NOT_FOUND_NOTE = (
    "Veri seti yalnızca Top 25 ve Rising listelerine giren terimleri içerir. Listede olmamak, terimin aranmadığı "
    "anlamına gelmez."
)
FEATURE_COLUMNS = {
    "marka_ilgi_degisim": "Marka ilgisinin bir önceki aya göre log değişimi (ölçekten bağımsız)",
    "generic_ilgi": "Kategori terimlerinin kendi geçmiş ortalamalarına oranının ortalaması (1 = olağan düzey)",
    "marka_generic_orani": "Markanın kendi geçmişine oranı ÷ generic_ilgi (1'den büyükse marka kategoriden güçlü)",
    "liste_gun_sayisi": "Markanın ay içinde Top 25 ya da Rising listesinde göründüğü gün sayısı",
    "liste_en_iyi_sira": "Markanın ay içindeki en iyi liste sırası (1 = en üst)",
    "rakip_liste_gun_sayisi": "Rakiplerin ay içinde listelerde göründüğü gün sayılarının toplamı",
}  # fmt: skip

_NON_WORD = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class SectorSearchTerms:
    """Catalog terms of a sector: `generic` category terms (a term also matches longer words, so stems are allowed) and
    `brands`, the competitor set used when the brand analysis does not supply one."""

    generic: tuple[str, ...] = ()
    brands: tuple[str, ...] = ()


@dataclass(frozen=True)
class TermSet:
    label: str
    mode: str
    spellings: tuple[str, ...]


def normalize(text: str) -> str:
    """Folded words separated by single spaces: "Türk Telekom'da" → 'turk telekom da'."""
    return " ".join(w for w in _NON_WORD.split(fold(text)) if w)


def term_set(label: str, mode: str, spellings: Iterable[str]) -> TermSet:
    unique = dict.fromkeys(s for s in map(normalize, spellings) if s)
    return TermSet(label, mode, tuple(unique))


def _classify(term: str, spellings: tuple[str, ...], mode: str) -> str | None:
    """Kind of match of a normalized list term. Brands match whole words, category terms also longer last words."""
    if term in spellings:
        return FOUND
    tail = r"" if mode == GENERIC else r"( |$)"
    for spelling in spellings:
        if len(spelling) >= MIN_RELATED_LENGTH and re.search(rf"(^| ){re.escape(spelling)}{tail}", term):
            return FOUND if mode == GENERIC else RELATED
    return None


def term_report(terms: TermSet, listing: pd.DataFrame) -> dict:
    """Where a term set appears in the lists. `listing`: table, refresh_date, term, rank, normalized."""
    kinds = listing["normalized"].map(lambda t: _classify(t, terms.spellings, terms.mode))
    exact, related = listing[kinds == FOUND], listing[kinds == RELATED]
    status = FOUND if not exact.empty else RELATED if not related.empty else NOT_FOUND
    report = {
        "label": terms.label, "mode": terms.mode, "status": status,
        "terms": sorted(set(exact["term"])), "related_terms": sorted(set(related["term"])),
        "list_days": int(exact["refresh_date"].nunique()), "related_list_days": int(related["refresh_date"].nunique()),
        "top_days": int(exact.loc[exact["table"] == "top", "refresh_date"].nunique()),
        "rising_days": int(exact.loc[exact["table"] == "rising", "refresh_date"].nunique()),
        "best_rank": int(exact["rank"].min()) if not exact.empty else None,
        "first_day": min(exact["refresh_date"]).isoformat() if not exact.empty else None,
        "last_day": max(exact["refresh_date"]).isoformat() if not exact.empty else None,
    }  # fmt: skip
    if status != FOUND:
        report["note"] = NOT_FOUND_NOTE if status == NOT_FOUND else (
            "Terimin kendisi listede yok; adı geçen başka terimler var. Bunlar markayı kastetmeyebilir, sayılmadı."
        )  # fmt: skip
    return report


def list_ranking(reports: list[dict]) -> list[dict]:
    """Brand and competitors ordered by list days, then best rank; terms without list days get no rank."""
    listed = sorted((r for r in reports if r["list_days"]), key=lambda r: (-r["list_days"], r["best_rank"], r["label"]))
    out, rank, previous = [], 0, None
    for i, r in enumerate(listed, start=1):
        key = (r["list_days"], r["best_rank"])
        rank = rank if key == previous else i
        previous = key
        out.append({"label": r["label"], "mode": r["mode"], "rank": rank, "list_days": r["list_days"], "best_rank": r["best_rank"]})
    return out + [{"label": r["label"], "mode": r["mode"], "rank": None, "list_days": 0, "best_rank": None}
                  for r in reports if not r["list_days"]]  # fmt: skip


def refresh_plan(term_days: dict[str, set[date]]) -> dict[str, date]:
    """One refresh day per term, reusing days so that few partitions are read (each costs about 0.4 GiB): repeatedly
    the day that serves most remaining terms, the newest on a tie."""
    remaining, plan = dict(term_days), {}
    while remaining:
        counts: dict[date, int] = {}
        for days in remaining.values():
            for day in days:
                counts[day] = counts.get(day, 0) + 1
        best = max(counts, key=lambda d: (counts[d], d))
        for term in [t for t, days in remaining.items() if best in days]:
            plan[term] = best
            del remaining[term]
    return plan


def scale_check(rows: pd.DataFrame) -> dict:
    """Are the province series scaled one by one? `rows`: term, region_code, score (national rows have no code)."""
    provinces = rows[rows["region_code"] != ""]
    if provinces.empty:
        return {"series": 0, "at_100": 0, "per_series": None}
    peaks = provinces.groupby(["term", "region_code"])["score"].max()
    at_100 = int((peaks >= 99.5).sum())
    return {"series": int(len(peaks)), "at_100": at_100, "per_series": at_100 / len(peaks) >= PER_SERIES_SHARE}


def weekly_series(rows: pd.DataFrame, terms: list[str]) -> pd.Series:
    """National weekly index of a term (several spellings: their mean), dated at the end of the week."""
    national = rows[(rows["region_code"] == "") & rows["term"].isin(terms)]
    if national.empty:
        return pd.Series(dtype=float)
    dates = pd.to_datetime(national["week"]) + pd.Timedelta(days=6)
    return national["score"].astype(float).groupby(dates.to_numpy()).mean().sort_index()


def interest_summary(series: pd.Series) -> dict | None:
    """What one term's own series shows. `momentum`: recent weeks ÷ the year before them, free of the scale."""
    if series.empty:
        return None
    active = int((series > 0).sum())
    recent, base = series.tail(RECENT_WEEKS), series.iloc[-(RECENT_WEEKS + BASE_WEEKS) : -RECENT_WEEKS]
    momentum = float(recent.mean() / base.mean()) if len(base) and base.mean() > 0 else None
    return {
        "weeks": int(len(series)), "active_weeks": active, "sparse": active < MIN_ACTIVE_WEEKS,
        "last_week": series.index[-1].date().isoformat(), "last_value": round(float(series.iloc[-1]), 2),
        "peak_week": series.idxmax().date().isoformat(),
        "momentum": round(momentum, 2) if momentum is not None else None,
    }  # fmt: skip


def province_summary(rows: pd.DataFrame, terms: list[str]) -> dict | None:
    """Spread of a term over the provinces. Levels are not compared: each province has its own scale, so provinces are
    ordered by the weeks with a published score in the last year, a rough sign of enough search volume."""
    provinces = rows[(rows["region_code"] != "") & rows["term"].isin(terms)]
    if provinces.empty:
        return None
    weeks = pd.to_datetime(provinces["week"])
    recent = provinces[weeks > weeks.max() - pd.Timedelta(weeks=BASE_WEEKS)]
    counts = recent.groupby(["region_code", "region_name"])["week"].nunique().sort_values(ascending=False, kind="stable")
    return {
        "provinces_with_data": int(provinces["region_code"].nunique()),
        "most_weeks": [{"province": name, "code": code, "weeks": int(n)} for (code, name), n in counts.head(MAX_PROVINCES).items()],
        "note": "İller kendi ölçeğinde olduğu için skorlar iller arasında karşılaştırılmaz.",
    }  # fmt: skip


def _own_ratio(monthly: pd.Series) -> pd.Series:
    """A month against the mean of the months before it: independent of the scale and of later months."""
    past = monthly.expanding().mean().shift(1)
    return monthly / past.replace(0, np.nan)


def monthly_features(
    brand: pd.Series,
    generics: dict[str, pd.Series],
    listing: pd.DataFrame,
    brand_terms: list[str],
    rival_terms: list[str],
    publication_lag_days: int,
    as_of: date,
) -> pd.DataFrame:
    """Monthly columns at their publication month; a column without data is left out, a month without data stays
    empty and the month still running at `as_of` is dropped. `listing`: table, refresh_date, term, rank."""
    columns: dict[str, pd.Series] = {}
    lag = publication_lag_days
    own = to_monthly(brand, Frequency.WEEKLY, publication_lag_days=lag) if not brand.empty else pd.Series(dtype=float)
    if not own.empty:
        # The level is left out: Google rescales the whole backfill to its current peak, so a past value carries look-ahead.
        columns["marka_ilgi_degisim"] = np.log(own.where(own > 0)).diff()
    ratios = [_own_ratio(to_monthly(s, Frequency.WEEKLY, publication_lag_days=lag)) for s in generics.values() if not s.empty]
    if ratios:
        columns["generic_ilgi"] = pd.concat(ratios, axis=1).mean(axis=1, skipna=True)
        if not own.empty:
            columns["marka_generic_orani"] = _own_ratio(own) / columns["generic_ilgi"].replace(0, np.nan)
    columns.update(_list_columns(listing, brand_terms, rival_terms, lag))
    frame = pd.DataFrame(columns).sort_index()
    return frame if frame.empty else frame[frame.index <= pd.Timestamp(as_of)]


def _list_columns(listing: pd.DataFrame, brand_terms: list[str], rival_terms: list[str], lag: int) -> dict[str, pd.Series]:
    if listing.empty:
        return {}
    months = (pd.to_datetime(listing["refresh_date"]) + pd.Timedelta(days=lag)) + pd.offsets.MonthEnd(0)
    days = listing["refresh_date"].groupby(months.to_numpy()).nunique()
    covered = days[days >= MIN_LIST_DAYS_PER_MONTH].index  # 0 is a real count only in a month with its lists at hand
    if covered.empty:
        return {}
    frame = listing.assign(month=months.to_numpy())
    frame = frame[frame["month"].isin(covered)]
    own, rivals = frame[frame["term"].isin(brand_terms)], frame[frame["term"].isin(rival_terms)]
    empty = pd.Series(0.0, index=covered)
    return {
        "liste_gun_sayisi": own.groupby("month")["refresh_date"].nunique().reindex(covered).fillna(0.0) + empty,
        "liste_en_iyi_sira": own.groupby("month")["rank"].min().reindex(covered).astype(float),
        "rakip_liste_gun_sayisi": rivals.groupby(["month", "term"])["refresh_date"].nunique().groupby("month").sum().reindex(covered).fillna(0.0) + empty,
    }
