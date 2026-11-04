"""Search interest of a brand: its own terms, its competitors and the category terms of its sector.

Reports per term what the Google Trends lists hold, and what can honestly be derived from it (domain/services/
search_share.py explains the limits). Missing terms are reported, never filled.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Protocol

import pandas as pd

from mlbooster.application.ports import CatalogRepository
from mlbooster.domain.errors import DomainError, InvalidInput
from mlbooster.domain.model import BrandEntry, Catalog
from mlbooster.domain.services.search_share import (
    ARAMA_PAYI_WITHHELD,
    BRAND,
    COMPETITOR,
    FEATURE_COLUMNS,
    FOUND,
    GENERIC,
    NOT_FOUND,
    PROXY_NOTE,
    RELATED,
    SectorSearchTerms,
    TermSet,
    interest_summary,
    list_ranking,
    monthly_features,
    normalize,
    province_summary,
    refresh_plan,
    scale_check,
    term_report,
    term_set,
    weekly_series,
)
from mlbooster.domain.validation import slug

SOURCE_ID = "bq_trends"
MAX_NAME_LENGTH = 80
MAX_COMPETITORS = 20
MONTHS_SHOWN = 6
STATUS_LABELS = {FOUND: "bulundu", RELATED: "yalnızca ilişkili terim", NOT_FOUND: "listede yok"}
MODE_LABELS = {BRAND: "Marka", COMPETITOR: "Rakip", GENERIC: "Kategori"}
COVERAGE_NOTES = {
    "bigquery": "Kaynak yalnızca son 30 güne yakın günlük listeyi tutar; terimler bu aralıkta aranmıştır.",
    "local_copy": "Terimler yerel kopyadaki bütün günlük listelerde aranmıştır; geçmiş ilk kopyadan itibaren birikir.",
}
NOT_RUN_MESSAGE = (
    "Arama ilgisi çalıştırılmadı. İsteğe bağlıdır: BigQuery sorgusu çalıştırır, Google Cloud projenize işlenir (ilk "
    "çalıştırma yaklaşık 17 GiB; aylık 1 TiB ücretsizdir) ve analizi uzatır. Çalıştırmak için CLI'da --search-interest, "
    "MCP'de search_interest=true, panelde \"Arama ilgisini getir\" düğmesi kullanılır."
)


class TrendsLookup(Protocol):
    bytes_billed: int
    queries: int

    def listed_terms(self) -> pd.DataFrame:
        """DataFrame[table, refresh_date, term, rank]: the retained Türkiye lists."""
        ...

    def interest(self, table: str, terms: dict) -> pd.DataFrame:
        """DataFrame[term, refresh_date, week, region_code, region_name, score] for term → refresh day."""
        ...

    def data_source(self) -> dict:
        """{'mode': 'bigquery' | 'local_copy', 'label': text for the output}"""
        ...


class SearchTermCatalog(Protocol):
    def load(self) -> dict[str, SectorSearchTerms]: ...


def _name(value: str, what: str) -> str:
    if not isinstance(value, str) or not 2 <= len(value.strip()) <= MAX_NAME_LENGTH or not normalize(value):
        raise InvalidInput(f"Geçersiz {what}: {value!r} (2–{MAX_NAME_LENGTH} karakter)")
    return value.strip()


def _entry(catalog: Catalog, name: str) -> BrandEntry | None:
    wanted = normalize(name)
    return next((e for e in catalog.brands.entries if wanted in {normalize(n) for n in (e.name, *e.aliases)}), None)


def _spellings(catalog: Catalog, name: str) -> tuple[str, ...]:
    entry = _entry(catalog, name)
    return (name, entry.name, *entry.aliases) if entry else (name,)


def search_not_run() -> dict:
    """Block of a report whose search interest was not asked for: nothing was queried."""
    return {"status": "not_run", "is_proxy": True, "note": PROXY_NOTE, "message": NOT_RUN_MESSAGE}


def search_section(result: dict) -> dict:
    """JSON form of a SearchInterest result, for reports, the panel and MCP."""
    features: pd.DataFrame = result["features"]
    rows = features.tail(MONTHS_SHOWN).round(3).astype(object).where(features.tail(MONTHS_SHOWN).notna(), None)
    return {
        **{k: v for k, v in result.items() if k not in {"features", "feature_notes", "terms", "cost"}},
        "status": "done",
        "terms": [{**t, "status_label": STATUS_LABELS[t["status"]], "mode_label": MODE_LABELS[t["mode"]]} for t in result["terms"]],
        "withheld": {"arama_payi": result["scale"]["reason"]},
        "monthly": {
            "columns": [{"name": n, "description": d} for n, d in result["feature_notes"].items()],
            "rows": [{"date": i.date().isoformat(), **row} for i, row in zip(rows.index, rows.to_dict("records"), strict=True)],
        },
        "cost": {**result["cost"], "gib": round(result["cost"]["bytes_billed"] / 1024**3, 2)},
    }


def run_search(search: Callable[[], SearchInterest] | None, brand: str, sector: str | None, competitors: list[str] | None = None) -> dict:
    """The search block of a brand analysis. A missing Google setup never stops the analysis: it is reported."""
    try:
        if search is None:
            raise InvalidInput("Arama ilgisi bu kurulumda kullanılamıyor")
        names = [c for c in competitors or [] if len(c.strip()) <= MAX_NAME_LENGTH][:MAX_COMPETITORS]
        return search_section(search()(brand, sector, names if competitors else None))
    except DomainError as exc:
        return {"status": "unavailable", "is_proxy": True, "note": PROXY_NOTE, "message": f"Arama ilgisi alınamadı: {exc}"}


class SearchInterest:
    def __init__(
        self,
        catalog: CatalogRepository,
        terms: SearchTermCatalog,
        lookup: TrendsLookup,
        today: Callable[[], date] = date.today,
    ):
        self._catalog = catalog
        self._terms = terms
        self._lookup = lookup
        self._today = today

    def __call__(self, brand: str, sector: str | None = None, competitors: list[str] | None = None) -> dict:
        catalog = self._catalog.load()
        brand = _name(brand, "marka adı")
        sector_id = self._sector(catalog, brand, sector)
        sets = self._term_sets(catalog, brand, sector_id, competitors)
        before = self._lookup.bytes_billed, self._lookup.queries

        listing = self._lookup.listed_terms()
        listing = listing.assign(normalized=listing["term"].astype(str).map(normalize))
        reports = [term_report(s, listing) for s in sets]
        rows = self._interest_rows(listing, reports)
        brand_report = reports[0]
        brand_terms = brand_report["terms"]
        rival_reports = [r for r in reports if r["mode"] == COMPETITOR]
        generic_reports = [r for r in reports if r["mode"] == GENERIC]

        for report in reports:
            summary = interest_summary(weekly_series(rows, report["terms"]))
            if summary is not None:
                report["interest"] = summary
        lag = catalog.source(SOURCE_ID).publication_lag_days
        features = monthly_features(
            weekly_series(rows, brand_terms),
            {r["label"]: weekly_series(rows, r["terms"]) for r in generic_reports},
            listing,
            brand_terms,
            [t for r in rival_reports for t in r["terms"]],
            lag,
            self._today(),
        )
        days = sorted(set(listing["refresh_date"]))
        source = self._lookup.data_source()
        return {
            "brand": brand, "sector": sector_id, "is_proxy": True, "note": PROXY_NOTE,
            "attribution": f"Kaynak: {catalog.source(SOURCE_ID).owner} — {catalog.source(SOURCE_ID).name}",
            "coverage": {
                "first_day": days[0].isoformat() if days else None, "last_day": days[-1].isoformat() if days else None,
                "list_days": len(days),
                "note": COVERAGE_NOTES[source["mode"]],
            },
            "data_source": source,
            "terms": reports,
            "ranking": list_ranking([brand_report, *rival_reports]),
            "provinces": province_summary(rows, brand_terms),
            "scale": {**scale_check(rows), "share_of_search": None, "reason": ARAMA_PAYI_WITHHELD},
            "features": features,
            "feature_notes": {c: FEATURE_COLUMNS[c] for c in features.columns},
            "cost": {
                "bytes_billed": self._lookup.bytes_billed - before[0], "queries": self._lookup.queries - before[1],
            },
        }  # fmt: skip

    def _sector(self, catalog: Catalog, brand: str, sector: str | None) -> str:
        entry = _entry(catalog, brand)
        chosen = sector or (entry.sector if entry else None)
        if chosen is None:
            raise InvalidInput(f"{brand} için sektör bilinmiyor; sektörü belirtin (ör. otomotiv)")
        return catalog.sector(slug(chosen, "sector")).id

    def _term_sets(self, catalog: Catalog, brand: str, sector_id: str, competitors: list[str] | None) -> list[TermSet]:
        sector_terms = self._terms.load().get(sector_id, SectorSearchTerms())
        own = term_set(brand, BRAND, _spellings(catalog, brand))
        names = sector_terms.brands if competitors is None else tuple(_name(c, "rakip adı") for c in competitors)
        if len(names) > MAX_COMPETITORS:
            raise InvalidInput(f"En fazla {MAX_COMPETITORS} rakip verilebilir")
        rivals = [term_set(n, COMPETITOR, _spellings(catalog, n)) for n in names]
        rivals = [r for r in rivals if not set(r.spellings) & set(own.spellings)]
        return [own, *rivals, *(term_set(g, GENERIC, [g]) for g in sector_terms.generic)]

    def _interest_rows(self, listing: pd.DataFrame, reports: list[dict]) -> pd.DataFrame:
        """One batched lookup per table for every term that was found."""
        wanted = {t for r in reports if r["status"] == FOUND for t in r["terms"]}
        frames = []
        for table in sorted(set(listing["table"]), reverse=True):  # top first; a term in both lists is read once
            group = listing[(listing["table"] == table) & listing["term"].isin(wanted)]
            if group.empty:
                continue
            days = group.groupby("term")["refresh_date"].agg(set).to_dict()
            frames.append(self._lookup.interest(str(table), refresh_plan(days)))
            wanted -= set(days)
        if not frames:
            return pd.DataFrame(columns=["term", "refresh_date", "week", "region_code", "region_name", "score"])
        return pd.concat(frames, ignore_index=True)
