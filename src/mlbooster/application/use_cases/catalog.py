"""Catalog queries (side-effect free)."""

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
            return catalog.sources_for_sector(slug(sector_id, "sector"))
        return catalog.sources_by_priority()


class ListReadyProviders:
    """`provider` keys that have an implementation, i.e. sources that can actually be fetched."""

    def __init__(self, providers: ProviderFactory):
        self._providers = providers

    def __call__(self) -> set[str]:
        return self._providers.available()


class GetSectorMap:
    def __init__(self, catalog: CatalogRepository, providers: ProviderFactory):
        self._catalog = catalog
        self._providers = providers

    def __call__(self, sector_id: str) -> SectorMap:
        return build_sector_map(self._catalog.load(), slug(sector_id, "sector"), self._providers.available())


class ListShareSeries:
    """Series that market share can be computed from directly: the `series` a sector's market share source names
    (catalog series with entity rows, e.g. BDDK bank groups) when that source's provider is implemented. One entry
    per source, with the sectors that list it."""

    def __init__(self, catalog: CatalogRepository, providers: ProviderFactory):
        self._catalog = catalog
        self._providers = providers

    def __call__(self) -> list[dict]:
        catalog = self._catalog.load()
        ready = self._providers.available()
        out: dict[str, dict] = {}
        for sector in catalog.sectors.values():
            for share in sector.market_share_sources:
                source = catalog.sources.get(share.source)
                if source is None or source.provider not in ready:
                    continue
                seeds = [seed for code in share.series if (seed := source.seed(code)) is not None]
                if not seeds:
                    continue
                entry = out.setdefault(source.id, {
                    "source": source.id, "name": source.name, "owner": source.owner, "sectors": [],
                    "is_proxy": share.is_proxy, "license": source.license.notes, "series": [],
                })  # fmt: skip
                entry["sectors"].append(sector.id)
                known = {s["code"] for s in entry["series"]}
                entry["series"] += [{"code": s.code, "name": s.name, "unit": s.unit} for s in seeds if s.code not in known]
        return list(out.values())
