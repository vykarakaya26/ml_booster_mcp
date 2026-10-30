"""Katalog sorguları (yan etkisiz)."""

from __future__ import annotations

from mlbooster.application.ports import CatalogRepository, ProviderFactory
from mlbooster.domain.model import Sector, Source
from mlbooster.domain.services.sector_map import SectorMap, build_sector_map
from mlbooster.domain.validation import slug


class ListSectors:
    def __init__(self, catalog: CatalogRepository):
        self._catalog = catalog

    def __call__(self) -> list[Sector]:
        return list(self._catalog.load().sectors.values())


class ListSources:
    def __init__(self, catalog: CatalogRepository):
        self._catalog = catalog

    def __call__(self, sector_id: str | None = None) -> list[Source]:
        catalog = self._catalog.load()
        if sector_id:
            return catalog.sources_for_sector(slug(sector_id, "sektör"))
        return catalog.sources_by_priority()


class GetSectorMap:
    def __init__(self, catalog: CatalogRepository, providers: ProviderFactory):
        self._catalog = catalog
        self._providers = providers

    def __call__(self, sector_id: str) -> SectorMap:
        return build_sector_map(self._catalog.load(), slug(sector_id, "sektör"), self._providers.available())
