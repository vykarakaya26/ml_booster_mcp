from mlbooster.adapters import REGISTRY
from mlbooster.mapping import render_markdown, sector_map

MVP_SECTORS = {"bankacilik", "sigorta", "telekom", "otomotiv", "akaryakit"}
MVP_SOURCES = {"evds3", "tuik", "bddk", "tbb", "epias", "epdk", "btk", "tsb", "odmd", "bkm"}


def test_catalog_has_mvp_sources_and_sectors(catalog):
    assert MVP_SOURCES <= set(catalog.sources)
    assert MVP_SECTORS <= set(catalog.sectors)


def test_every_source_has_required_metadata(catalog):
    for s in catalog.sources.values():
        assert s.license.notes, s.id
        assert s.frequency, s.id
        assert s.publication_lag_days >= 0, s.id
        assert s.auth.type, s.id
        assert s.adapter is None or s.adapter in REGISTRY, s.id


def test_secrets_are_env_vars_not_values(catalog):
    for s in catalog.sources.values():
        if s.auth.type == "header":
            assert s.auth.env_var and s.auth.env_var.isupper()


def test_every_sector_has_market_share_source(catalog):
    for sector_id in MVP_SECTORS:
        assert catalog.sector(sector_id).market_share_sources


def test_map_renders_for_all_sectors(catalog):
    for sector_id in catalog.sectors:
        md = render_markdown(sector_map(catalog, sector_id))
        assert "| Kaynak |" in md
