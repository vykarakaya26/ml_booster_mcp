"""Search interest use case with a fake lookup: no HTTP."""

from dataclasses import dataclass, field
from datetime import date

import pandas as pd
import pytest

from mlbooster.application.use_cases.search_interest import SearchInterest
from mlbooster.domain.errors import InvalidInput, NotFound
from mlbooster.domain.services.search_share import SectorSearchTerms
from mlbooster.infrastructure.search_terms_yaml import YamlSearchTerms
from mlbooster.infrastructure.settings import PACKAGE_DIR

D1, D2 = date(2026, 10, 10), date(2026, 10, 20)


def rows(term, refresh, values, region=""):
    weeks = pd.date_range("2025-01-05", periods=len(values), freq="7D").date
    return pd.DataFrame({"term": term, "refresh_date": refresh, "week": weeks, "region_code": region,
                         "region_name": "İstanbul" if region else None, "score": values})  # fmt: skip


@dataclass
class FakeLookup:
    lists: list = field(default_factory=lambda: [
        ("top", D1, "Togg", 12), ("rising", D2, "togg", 3), ("top", D1, "togg t6x", 5), ("top", D2, "tesla", 2),
        ("top", D2, "sıfır araba fiyatları", 8), ("top", D1, "galatasaray", 1),
    ])  # fmt: skip
    bytes_billed: int = 0
    queries: int = 0
    asked: list = field(default_factory=list)

    def listed_terms(self):
        self.bytes_billed += 1000
        self.queries += 1
        return pd.DataFrame(self.lists, columns=["table", "refresh_date", "term", "rank"])

    def interest(self, table, terms):
        self.bytes_billed += 500
        self.queries += 1
        self.asked.append((table, dict(terms)))
        frames = [rows(t, d, [100.0, 50.0] * 30) for t, d in terms.items()]
        frames += [rows(t, d, [100.0, 50.0] * 30, "TR-34") for t, d in terms.items()]
        return pd.concat(frames, ignore_index=True)


class Terms:
    def load(self):
        return {"otomotiv": SectorSearchTerms(generic=("sıfır araba", "ötv"), brands=("Togg", "Tesla", "Renault"))}


@pytest.fixture
def lookup():
    return FakeLookup()


@pytest.fixture
def use(catalog_repo, lookup):
    return SearchInterest(catalog_repo, Terms(), lookup, today=lambda: date(2026, 11, 4))


def test_reports_every_term_by_mode(use):
    result = use("Togg", "otomotiv")
    by_label = {t["label"]: t for t in result["terms"]}
    assert [t["mode"] for t in result["terms"]] == ["brand", "competitor", "competitor", "generic", "generic"]
    assert by_label["Togg"]["status"] == "found" and by_label["Togg"]["list_days"] == 2
    assert by_label["Tesla"]["status"] == "found" and by_label["Renault"]["status"] == "not_found"
    assert by_label["sıfır araba"]["status"] == "found" and by_label["ötv"]["status"] == "not_found"
    assert "interest" in by_label["Togg"] and "interest" not in by_label["Renault"]
    assert result["is_proxy"] is True and "pazar payı değildir" in result["note"]


def test_share_of_search_is_withheld(use):
    result = use("Togg", "otomotiv")
    assert result["scale"]["share_of_search"] is None and result["scale"]["per_series"] is True
    assert "arama_payi hesaplanmadı" in result["scale"]["reason"]
    assert "arama_payi" not in result["features"].columns


def test_ranking_and_cost(use):
    result = use("Togg", "otomotiv")
    assert [(r["label"], r["rank"]) for r in result["ranking"]] == [("Togg", 1), ("Tesla", 2), ("Renault", None)]
    assert result["cost"] == {"bytes_billed": 2000, "queries": 3}  # one list read, one lookup per table
    assert result["coverage"]["list_days"] == 2 and result["coverage"]["first_day"] == D1.isoformat()


def test_terms_are_batched_per_table_and_read_once(use, lookup):
    use("Togg", "otomotiv")
    assert [table for table, _ in lookup.asked] == ["top", "rising"]
    top, rising = lookup.asked[0][1], lookup.asked[1][1]
    assert set(top) == {"Togg", "tesla", "sıfır araba fiyatları"} and top["tesla"] == D2
    assert set(rising) == {"togg"}


def test_nothing_found_sends_no_interest_query(catalog_repo, lookup):
    lookup.lists = [("top", D1, "galatasaray", 1)]
    result = SearchInterest(catalog_repo, Terms(), lookup)("Togg", "otomotiv")
    assert lookup.asked == [] and result["provinces"] is None and result["terms"][0]["status"] == "not_found"
    assert result["ranking"][0]["rank"] is None


def test_empty_lists(catalog_repo, lookup):
    lookup.lists = []
    result = SearchInterest(catalog_repo, Terms(), lookup)("Togg", "otomotiv")
    assert result["coverage"]["list_days"] == 0 and result["features"].empty


def test_competitors_from_the_brand_analysis_win(use):
    result = use("Togg", "otomotiv", competitors=["BYD", "Tesla"])
    assert [t["label"] for t in result["terms"] if t["mode"] == "competitor"] == ["BYD", "Tesla"]


def test_own_aliases_are_not_competitors(use):
    assert [t["label"] for t in use("Togg", "otomotiv", competitors=["togg"])["terms"] if t["mode"] == "competitor"] == []


def test_catalog_aliases_are_used(catalog_repo, lookup):
    lookup.lists = [("top", D1, "Mavi Jeans", 4)]
    terms = type("T", (), {"load": lambda self: {}})()
    result = SearchInterest(catalog_repo, terms, lookup)("Mavi")  # sector comes from the brand catalog
    assert result["sector"] == "perakende" and result["terms"][0]["status"] == "found"


def test_input_validation(use):
    for bad in ("", "x", "!!", "a" * 81):
        with pytest.raises(InvalidInput):
            use(bad, "otomotiv")
    with pytest.raises(InvalidInput, match="sektör bilinmiyor"):
        use("Bilinmeyen Marka")
    with pytest.raises(InvalidInput):
        use("Togg", "../etc")
    with pytest.raises(NotFound):
        use("Togg", "yok_boyle")
    with pytest.raises(InvalidInput, match="En fazla"):
        use("Togg", "otomotiv", competitors=[f"Rakip {i}" for i in range(21)])


def test_packaged_search_terms_cover_known_sectors(catalog):
    terms = YamlSearchTerms(PACKAGE_DIR / "catalog_data" / "search_terms.yaml").load()
    assert {"bankacilik", "sigorta", "otomotiv", "akaryakit", "telekom", "perakende"} <= set(terms)
    assert set(terms) <= set(catalog.sectors)
    assert all(t.generic and t.brands for t in terms.values())
