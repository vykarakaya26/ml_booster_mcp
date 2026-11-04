"""Published market share evidence: what a search may use, what it found, what the reports show.

The search itself runs outside the package, in the Claude Code plugin (agents/share-research.md); the package holds
the rules and the records. A record never reaches the observation store or a feature panel: the only way out as a table
is `EvidenceTable`, on explicit request and marked is_proxy.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from mlbooster.application.ports import CatalogRepository, EvidenceStore
from mlbooster.domain.errors import InvalidInput, NotFound
from mlbooster.domain.model import Catalog
from mlbooster.domain.services import evidence as rules
from mlbooster.domain.services.brand_resolution import fold
from mlbooster.domain.validation import brand_query, evidence_id, slug

REASON_MAX = 200
EXPORT_COLUMNS = ("brand", "date", "period", "published", "value", "value_high", "qualifier", "basis", "geography", "segment",
                  "publisher", "tier", "second_hand", "confidence", "url", "is_proxy")  # fmt: skip


def _sector_of(catalog: Catalog, brand: str) -> str | None:
    """Where a known brand belongs: its catalog sector, or the sector whose market share list holds its source."""
    entry = next((e for e in catalog.brands.entries if e.name == brand), None)
    if entry is None:
        return None
    if entry.sector:
        return entry.sector
    sources = {t.source for t in entry.targets}
    return next((s.id for s in catalog.sectors.values() for share in s.market_share_sources if share.series and share.source in sources), None)


class PlanEvidenceSearch:
    """The queries, sources and limits of one search, and what is already recorded."""

    def __init__(self, catalog: CatalogRepository, store: EvidenceStore):
        self._catalog = catalog
        self._store = store

    def __call__(self, brand: str, sector: str | None = None) -> dict:
        catalog = self._catalog.load()
        policy = catalog.evidence
        name = rules.canonical_brand(brand_query(brand), catalog.brands)
        sector_id = slug(sector, "sector") if sector else _sector_of(catalog, name)
        today = date.today()
        known = self._store.list([rules.brand_key(name, catalog.brands)])
        return {
            "brand": name, "sector": sector_id,
            "queries": rules.search_queries(policy, name, sector_id, today.year - 1),
            "tiers": rules.allowed_sources(policy, name, catalog.brands),
            "own_site_rule": f"Listede olmayan bir şirket sitesi yalnızca alan adı marka adıyla başlıyorsa kabul edilir; güven düzeyi {policy.brand_rule_max_confidence} kalır.",
            "restricted": [{"domain": r.domain, "reason": r.reason} for r in policy.restricted],
            "refused": [{"label": r.label, "reason": r.reason, "domains": list(r.domains)} for r in policy.refused],
            "paid_research": {"reason": policy.paid_reason, "names": list(policy.paid_names), "domains": list(policy.paid_domains)},
            "budget": {"max_searches": policy.max_searches, "max_fetches": policy.max_fetches, "min_independent_sources": policy.min_independent_sources},
            "bases": rules.BASIS_LABELS, "quote_max_words": policy.quote_max_words, "current_max_age_days": policy.current_max_age_days,
            "already_recorded": [rules.as_dict(e, policy) for e in known],
        }  # fmt: skip


class RecordEvidence:
    """Checks a claim against the source policy and stores it. A claim rejected earlier stays rejected."""

    def __init__(self, catalog: CatalogRepository, store: EvidenceStore):
        self._catalog = catalog
        self._store = store

    def __call__(self, draft: rules.EvidenceDraft) -> dict:
        catalog = self._catalog.load()
        if draft.sector is not None:
            slug(draft.sector, "sector")
        item, notes = rules.build(draft, catalog.evidence, catalog.brands, date.today())
        if (reason := self._store.rejection(item.id)) is not None:
            raise InvalidInput(f"Bu bulgu daha önce reddedildi: {reason}")
        added = self._store.add(item)
        return {"status": "recorded" if added else "duplicate", "evidence": rules.as_dict(item, catalog.evidence), "notes": notes}


class ListEvidence:
    def __init__(self, catalog: CatalogRepository, store: EvidenceStore):
        self._catalog = catalog
        self._store = store

    def __call__(self, brand: str | None = None, max_age_days: int | None = None) -> dict:
        """Evidence of a brand, or of every brand if none is given."""
        catalog = self._catalog.load()
        if brand is not None:
            return self.section([brand_query(brand)], max_age_days=max_age_days)
        return rules.section("<marka>", self._store.list(), catalog.evidence, date.today(), max_age_days=_age(max_age_days))

    def section(self, names: list[str], series: list[dict] | None = None, history: dict | None = None, max_age_days: int | None = None) -> dict:
        """`names` are the spellings of one brand; the first one is shown."""
        catalog = self._catalog.load()
        keys = list(dict.fromkeys(rules.brand_key(n, catalog.brands) for n in names))
        return rules.section(names[0], self._store.list(keys), catalog.evidence, date.today(), series, history, _age(max_age_days))


def _age(max_age_days: int | None) -> int | None:
    if max_age_days is not None and not 1 <= max_age_days <= 36500:
        raise InvalidInput("Azami yaş 1–36.500 gün arasında olmalıdır")
    return max_age_days


class RejectEvidence:
    """Removes a wrong record. Its facts are deleted; only its id and the reason stay, so it is not recorded again."""

    def __init__(self, catalog: CatalogRepository, store: EvidenceStore):
        self._catalog = catalog
        self._store = store

    def __call__(self, record: str, reason: str) -> dict:
        text = " ".join(str(reason).split())
        if not text or len(text) > REASON_MAX:
            raise InvalidInput(f"Ret gerekçesi yazılmalıdır (en fazla {REASON_MAX} karakter)")
        removed = self._store.remove(evidence_id(record), text, date.today())
        if removed is None:
            raise NotFound(f"Bulgu bulunamadı: {record}")
        return {"status": "rejected", "id": removed.id, "brand": removed.brand, "claim": rules.claim_text(removed), "publisher": removed.publisher, "reason": text}


class EvidenceTable:
    """Sparse table of the published shares of one brand, for an explicit export only. It is proxy data: figures from
    different pages have different definitions, so the rows do not form a series."""

    def __init__(self, catalog: CatalogRepository, store: EvidenceStore):
        self._catalog = catalog
        self._store = store

    def __call__(self, brand: str) -> pd.DataFrame:
        catalog = self._catalog.load()
        items = self._store.list([rules.brand_key(brand_query(brand), catalog.brands)])
        rows = [{
            "brand": e.brand, "date": e.period_end, "period": e.period, "published": e.published, "value": e.value, "value_high": e.value_high,
            "qualifier": e.qualifier, "basis": e.basis, "geography": e.geography, "segment": e.segment, "publisher": e.publisher, "tier": e.tier,
            "second_hand": e.second_hand, "confidence": e.confidence, "url": e.url, "is_proxy": True,
        } for e in sorted(items, key=lambda e: (e.period_end, fold(e.publisher))) if e.claim == rules.SHARE]  # fmt: skip
        if not rows:
            raise InvalidInput(f"{brand} için dışa aktarılacak pay bulgusu yok")
        return pd.DataFrame(rows, columns=list(EXPORT_COLUMNS)).set_index("date")
