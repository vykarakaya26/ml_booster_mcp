import shutil

import pytest

from mlbooster.domain.errors import InconsistentCatalog
from mlbooster.infrastructure.catalog_yaml import YamlCatalogRepository
from mlbooster.infrastructure.providers.registry import PROVIDERS
from mlbooster.infrastructure.settings import PACKAGE_DIR

MVP_SECTORS = {"bankacilik", "sigorta", "telekom", "otomotiv", "akaryakit"}
MVP_SOURCES = {"evds3", "tuik", "bddk", "tbb", "epias", "epdk", "btk", "tsb", "odmd", "bkm"}


def test_catalog_has_mvp_sources_and_sectors(catalog):
    assert MVP_SOURCES <= set(catalog.sources)
    assert MVP_SECTORS <= set(catalog.sectors)


def test_every_source_has_required_metadata(catalog):
    for s in catalog.sources.values():
        assert s.license.notes and s.frequencies and s.publication_lag_days >= 0, s.id
        assert s.base_url.startswith("https://"), s.id
        assert s.provider is None or s.provider in PROVIDERS, s.id


def test_every_mvp_sector_has_market_share_source(catalog):
    assert all(catalog.sector(s).market_share_sources for s in MVP_SECTORS)


def test_market_share_series_exist_in_their_source(catalog):
    """Series a sector offers for market share (web panel source mode) must be defined in the source catalog."""
    listed = [(sector.id, m.source, code) for sector in catalog.sectors.values() for m in sector.market_share_sources for code in m.series]
    assert ("bankacilik", "bddk", "BDDK.AKTIF") in listed
    missing = [x for x in listed if catalog.source(x[1]).seed(x[2]) is None]
    assert not missing, missing


def _write(tmp_path, source_yaml: str, sector_yaml: str | None = None):
    (tmp_path / "sources").mkdir()
    (tmp_path / "sectors").mkdir()
    (tmp_path / "sources" / "x.yaml").write_text(source_yaml)
    if sector_yaml:
        (tmp_path / "sectors" / "y.yaml").write_text(sector_yaml)
    return YamlCatalogRepository(tmp_path)


SOURCE = """
id: {id}
name: X
owner: X
base_url: {url}
access: rest_api
auth: {{type: none}}
license: {{notes: test}}
frequency: [M]
formats: [json]
sectors: []
publication_lag_days: 0
verification_status: reported
"""


def test_rejects_http_base_url(tmp_path):
    with pytest.raises(InconsistentCatalog, match="https"):
        _write(tmp_path, SOURCE.format(id="x", url="http://example.com")).load()


@pytest.mark.parametrize(("extra", "limit"), [("", 50), ("max_response_mb: 150\n", 150), ("max_response_mb: 200\n", 200)])
def test_response_size_limit_defaults_to_50_mb(tmp_path, extra, limit):
    catalog = _write(tmp_path, SOURCE.format(id="x", url="https://example.com") + extra).load()
    assert catalog.source("x").max_response_mb == limit


@pytest.mark.parametrize("value", ["201", "0", "1000"])
def test_rejects_response_size_limit_outside_bounds(tmp_path, value):
    with pytest.raises(InconsistentCatalog, match="max_response_mb"):
        _write(tmp_path, SOURCE.format(id="x", url="https://example.com") + f"max_response_mb: {value}\n").load()


def test_rejects_path_traversal_id(tmp_path):
    with pytest.raises(InconsistentCatalog):
        _write(tmp_path, SOURCE.format(id="'../x'", url="https://example.com")).load()


def test_rejects_unknown_fields(tmp_path):
    with pytest.raises(InconsistentCatalog):
        _write(tmp_path, SOURCE.format(id="x", url="https://example.com") + "evil: 1\n").load()


def test_rejects_yaml_object_tags(tmp_path):
    payload = "!!python/object/apply:os.system ['echo hacked']\n"
    with pytest.raises(InconsistentCatalog, match="Geçersiz YAML"):
        _write(tmp_path, payload).load()


def test_rejects_unknown_source_reference(tmp_path):
    sector = "id: y\nname: Y\nsector_sources: [nope]\n"
    with pytest.raises(InconsistentCatalog, match="nope"):
        _write(tmp_path, SOURCE.format(id="x", url="https://example.com"), sector).load()


def test_brand_lexicon_is_loaded_and_consistent(catalog):
    lexicon = catalog.brands
    assert {"as", "tas"} <= lexicon.legal_tokens and "sigorta" in lexicon.generic_tokens
    names = {e.name for e in lexicon.entries}
    assert {"Volkswagen", "Garanti BBVA", "Petrol Ofisi", "Koç Grubu", "Migros"} <= names
    seen: set[str] = set()
    for entry in lexicon.entries:
        assert entry.targets or entry.sector
        keys = {a.casefold() for a in (entry.name, *entry.aliases)}
        assert not keys & seen, entry.name  # an alias must lead to one entry
        seen |= keys


def test_entity_index_and_segments_are_part_of_the_series_list(catalog):
    fuel = catalog.sector("akaryakit").market_share_sources[0]
    assert fuel.index_series == ("EPDK.PETROL.TOPLAM", "EPDK.LPG.TOPLAM") and len(fuel.segments) == 5
    cars = catalog.sector("otomotiv").market_share_sources[0]
    assert cars.index_series == ("ODMD.TOPLAM",)
    assert catalog.sector("telekom").market_share_sources[0].history_years == 3


def _catalog_copy(tmp_path):
    root = tmp_path / "catalog"
    shutil.copytree(PACKAGE_DIR / "catalog_data", root)
    return root


def test_rejects_a_segment_outside_the_series_list(tmp_path):
    root = _catalog_copy(tmp_path)
    path = root / "sectors" / "otomotiv.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("segments: [ODMD.OTOMOBIL,", "segments: [ODMD.YOK,"), encoding="utf-8")
    with pytest.raises(InconsistentCatalog, match="ODMD.YOK"):
        YamlCatalogRepository(root).load()


def test_rejects_brand_entries_with_unknown_references_or_nothing_to_point_at(tmp_path):
    root = _catalog_copy(tmp_path)
    path = root / "brands.yaml"
    original = path.read_text(encoding="utf-8")
    for bad, message in (
        ("  - {name: Yok, targets: [{source: yok, entities: [A]}]}\n", "bilinmeyen kaynak"),
        ("  - {name: Yok, sector: yok}\n", "bilinmeyen sektör"),
        ("  - {name: Yok}\n", "targets ya da sector"),
    ):
        path.write_text(original + bad, encoding="utf-8")
        with pytest.raises(InconsistentCatalog, match=message):
            YamlCatalogRepository(root).load()


def test_catalog_without_a_brand_file_still_loads(tmp_path):
    root = _catalog_copy(tmp_path)
    (root / "brands.yaml").unlink()
    assert YamlCatalogRepository(root).load().brands.entries == ()
