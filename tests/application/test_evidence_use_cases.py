from datetime import date, timedelta

import pandas as pd
import pytest

from fakes import BrandWorld, InMemoryEvidenceStore, StaticCatalog
from mlbooster.application.use_cases.brand import AnalyzeBrand, ResolveBrand
from mlbooster.application.use_cases.evidence import (
    EvidenceTable,
    ListEvidence,
    PlanEvidenceSearch,
    RecordEvidence,
    RejectEvidence,
)
from mlbooster.application.use_cases.market_share import AnalyzeMarketShare
from mlbooster.domain.errors import InvalidInput, NotFound
from mlbooster.domain.services.evidence import EvidenceDraft

LAST_YEAR = str(date.today().year - 1)
PUBLISHED = f"{date.today().year}-01-01"


def migros(**kwargs) -> EvidenceDraft:
    base = {"brand": "Migros", "value": 24.5, "basis": "revenue", "period": LAST_YEAR, "published": PUBLISHED, "publisher": "Migros Ticaret",
            "url": "https://www.migroskurumsal.com/rapor.pdf", "quote": "pazar payımız %24,5 oldu", "sector": "perakende"}  # fmt: skip
    return EvidenceDraft(**{**base, **kwargs})


@pytest.fixture
def store():
    return InMemoryEvidenceStore()


@pytest.fixture
def repo(catalog):
    return StaticCatalog(catalog)


def test_plan_gives_queries_domains_budget_and_what_is_recorded(repo, store):
    RecordEvidence(repo, store)(migros())
    plan = PlanEvidenceSearch(repo, store)("migros ticaret")
    assert plan["brand"] == "Migros" and plan["sector"] == "perakende"
    assert {q["language"] for q in plan["queries"]} == {"tr", "en"}
    assert "rekabet.gov.tr" in plan["tiers"][0]["domains"] and "migroskurumsal.com" in plan["tiers"][1]["domains"]
    assert plan["budget"]["max_searches"] <= 10 and plan["budget"]["max_fetches"] <= 15
    assert plan["restricted"][0]["domain"] == "kap.org.tr" and "nielsen" in plan["paid_research"]["names"]
    assert plan["already_recorded"][0]["value"] == 24.5


def test_plan_finds_the_sector_of_a_brand_with_a_source_and_takes_a_free_sector(repo, store):
    assert PlanEvidenceSearch(repo, store)("Garanti")["sector"] == "bankacilik"
    assert PlanEvidenceSearch(repo, store)("Abdi İbrahim", "ilac")["sector"] == "ilac"
    assert PlanEvidenceSearch(repo, store)("Abdi İbrahim")["sector"] is None
    with pytest.raises(InvalidInput):
        PlanEvidenceSearch(repo, store)("Migros", "../x")


def test_a_record_is_stored_once_and_a_rejected_claim_stays_out(repo, store):
    record = RecordEvidence(repo, store)
    first = record(migros())
    assert first["status"] == "recorded" and first["evidence"]["tier"] == "company"
    assert record(migros())["status"] == "duplicate" and len(store.items) == 1
    gone = RejectEvidence(repo, store)(first["evidence"]["id"], "Rakam başka bir yıla ait")
    assert gone["status"] == "rejected" and not store.items
    with pytest.raises(InvalidInput, match="başka bir yıla ait"):
        record(migros())


def test_reject_needs_a_valid_known_id_and_a_reason(repo, store):
    reject = RejectEvidence(repo, store)
    with pytest.raises(InvalidInput):
        reject("../x", "gerekçe")
    with pytest.raises(NotFound):
        reject("0" * 16, "gerekçe")
    with pytest.raises(InvalidInput):
        reject("0" * 16, " ")
    with pytest.raises(InvalidInput):
        RecordEvidence(repo, store)(migros(sector="../x"))


def test_list_by_brand_and_overall(repo, store):
    record = RecordEvidence(repo, store)
    record(migros())
    record(migros(brand="Togg", url="https://www.togg.com.tr/x", basis="units"))
    listing = ListEvidence(repo, store)
    assert [e["brand"] for e in listing("Migros")["current"]] == ["Migros"]
    assert len(listing()["current"]) == 2
    assert listing("Migros", max_age_days=1)["historical"]
    with pytest.raises(InvalidInput):
        listing("Migros", max_age_days=0)


def test_export_is_explicit_sparse_and_marked_proxy(repo, store):
    RecordEvidence(repo, store)(migros())
    RecordEvidence(repo, store)(migros(claim="rank", value=1, quote="sektörün lideri"))
    table = EvidenceTable(repo, store)("Migros")
    assert len(table) == 1 and table["is_proxy"].all() and "quote" not in table.columns
    with pytest.raises(InvalidInput):
        EvidenceTable(repo, store)("Togg")


@pytest.fixture
def world(catalog, store):
    w = BrandWorld(catalog)
    repo = StaticCatalog(catalog)
    w.listing = ListEvidence(repo, store)
    w.record = RecordEvidence(repo, store)
    w.resolve = ResolveBrand(repo, w.directory, None, w.listing)
    w.analyze = AnalyzeBrand(repo, w.resolve, AnalyzeMarketShare(None, None, w.tables), w.tables, w.listing)
    return w


def test_not_found_brand_carries_evidence_and_no_share(world):
    world.record(migros())
    out = world.analyze("Migros")
    assert out["status"] == "not_found" and out["evidence"]["current"][0]["value"] == 24.5
    assert out["available"][0]["kind"] == "web_evidence" and "share-research" in out["available"][0]["message"]
    assert "pazar payı hesaplanmadı" in out["message"] and not ({"share", "position", "headline"} & set(out))


def test_not_found_without_evidence_lists_the_search_as_a_next_step(world):
    out = world.analyze("Migros")
    assert out["evidence"]["current"] == [] and "share-research" in out["evidence"]["next_step"]


def test_resolved_brand_keeps_computed_fields_and_adds_a_comparison(world):
    start = date(2022, 1, 1)
    before = world.analyze("Togg", start=start, end=date(2025, 12, 31))
    assert before["evidence"]["current"] == [] and before["evidence"]["next_step"]
    world.record(migros(brand="Togg", value=1.5, quote="payımız %1,5", basis="units", url="https://www.togg.com.tr/x", period="2025-09",
                        published="2025-10-01", sector="otomotiv"))  # fmt: skip
    after = world.analyze("Togg", start=start, end=date(2025, 12, 31))
    for key in ("headline", "position", "series", "concentration", "competitors", "share_history"):
        assert after[key] == before[key]  # the computed side does not move
    row = after["evidence"]["comparison"][0]
    assert row["series"] == after["position"]["series"] and row["reasons"] and row["evidence_text"] == "%1,5"


def test_evidence_never_reaches_the_observation_store_or_the_panel(world):
    world.record(migros())
    world.record(migros(brand="Togg", url="https://www.togg.com.tr/x", basis="units"))
    start, end = date(2022, 1, 1), date(2025, 12, 31)
    built = world.features("Togg", start=start, end=end)
    assert not [c for c in built.panel.columns if "evidence" in c or "bulgu" in c]
    observed = pd.concat([world.store.read(s) for s in ("odmd", "tsb", "tbb")], ignore_index=True)
    assert "migroskurumsal" not in observed.to_string() and "togg.com.tr" not in observed.to_string()


def test_expired_evidence_stays_listed_as_historical(repo, store):
    period = (date.today() - timedelta(days=900)).strftime("%Y-%m")
    RecordEvidence(repo, store)(migros(period=period, published=PUBLISHED))
    out = ListEvidence(repo, store)("Migros")
    assert not out["current"] and out["historical"][0]["period"] == period and out["next_step"]
