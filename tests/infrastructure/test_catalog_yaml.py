import pytest

from mlbooster.domain.errors import InconsistentCatalog
from mlbooster.infrastructure.catalog_yaml import YamlCatalogRepository
from mlbooster.infrastructure.providers.registry import PROVIDERS

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
