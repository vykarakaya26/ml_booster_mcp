"""Live checks of the brand flow: pytest -m live -k brand (keyless sources; reads the real entity lists)."""

from datetime import date, timedelta

import pytest

from mlbooster.application.use_cases.brand import AnalyzeBrand, ResolveBrand, StoreEntityDirectory
from mlbooster.application.use_cases.market_share import AnalyzeMarketShare, SourceShareTable
from mlbooster.bootstrap import build_container
from mlbooster.infrastructure.settings import Settings

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def flow(tmp_path_factory, request):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("MLBOOSTER_DATA_DIR", str(tmp_path_factory.mktemp("data")))
    c = build_container(Settings.from_env(load_env_files=False))
    tables = SourceShareTable(c.catalog, c.fetch_series, c.observations)
    directory = StoreEntityDirectory(c.catalog, c.providers, c.fetch_series, c.observations)
    resolver = ResolveBrand(c.catalog, directory, c.fetch_series)
    yield c, directory, resolver, AnalyzeBrand(c.catalog, resolver, AnalyzeMarketShare(c.build_panel, None, tables), tables)
    monkeypatch.undo()


def test_brand_catalog_targets_exist_in_the_sources(flow):
    c, directory, _, _ = flow
    scan = directory.scan()
    assert not scan.skipped
    seen = {(r.source, r.entity_id) for r in scan.records}
    missing = [
        f"{entry.name}: {target.source}:{entity}"
        for entry in c.catalog.load().brands.entries
        for target in entry.targets
        for entity in target.entities
        if (target.source, entity) not in seen
    ]
    assert not missing, missing


@pytest.mark.parametrize(
    ("query", "brand", "sector"),
    [("Togg", "odmd:TOGG", "otomotiv"), ("Garanti", "tbb:30", "bankacilik"), ("Anadolu Sigorta", "tsb:1005", "sigorta"),
     ("Turkcell", "btk:TURKCELL+SUPERONLINE", "telekom"), ("Opet", "epdk:OPET_PETROLCULUK", "akaryakit")],
)  # fmt: skip
def test_brand_resolves_and_is_analysed(flow, query, brand, sector):
    _, _, _, analyze = flow
    out = analyze(query, start=date.today() - timedelta(days=800))
    assert out["status"] == "resolved" and out["brand"]["id"] == brand and out["sector"]["id"] == sector
    assert 0 < out["position"]["share"] < 100 and out["position"]["rank"] >= 1
    assert out["competitors"] and all(c["reasons"] for c in out["competitors"])
    assert out["sources"][0]["attribution"].startswith("Kaynak:")


def test_retailer_is_reported_as_not_covered(flow):
    _, _, resolver, _ = flow
    out = resolver("Migros")
    assert out["status"] == "not_found" and out["sector_candidates"][0]["sector"] == "perakende"
