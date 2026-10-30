"""Sektör → kaynak haritası (sunumdan bağımsız veri yapısı)."""

from __future__ import annotations

from dataclasses import dataclass

from mlbooster.domain.model import Catalog, MacroFeature, ShareSource, Source


@dataclass(frozen=True)
class SourceEntry:
    source: Source
    roles: tuple[str, ...]
    provider_ready: bool


@dataclass(frozen=True)
class FeatureEntry:
    feature: MacroFeature
    status: str  # hazir | kesif_bekliyor | saglayici_yok


@dataclass(frozen=True)
class SectorMap:
    sector_id: str
    sector_name: str
    ml_use_cases: tuple[str, ...]
    sources: tuple[SourceEntry, ...]
    features: tuple[FeatureEntry, ...]
    market_share: tuple[ShareSource, ...]


def build_sector_map(catalog: Catalog, sector_id: str, available_providers: set[str]) -> SectorMap:
    sector = catalog.sector(sector_id)
    roles: dict[str, list[str]] = {}
    for source_id in dict.fromkeys(f.source for f in sector.macro_features):
        roles.setdefault(source_id, []).append("makro")
    for source_id in sector.sector_sources:
        roles.setdefault(source_id, []).append("sektör")
    for m in sector.market_share_sources:
        roles.setdefault(m.source, []).append("pazar payı (proxy)" if m.is_proxy else "pazar payı")

    def ready(source_id: str) -> bool:
        return catalog.source(source_id).provider in available_providers

    def status(f: MacroFeature) -> str:
        if not ready(f.source):
            return "saglayici_yok"
        return "hazir" if f.is_resolved else "kesif_bekliyor"

    return SectorMap(
        sector_id=sector.id,
        sector_name=sector.name,
        ml_use_cases=sector.ml_use_cases,
        sources=tuple(
            SourceEntry(s, tuple(roles[s.id]), ready(s.id)) for s in catalog.sources_for_sector(sector_id)
        ),
        features=tuple(FeatureEntry(f, status(f)) for f in sector.macro_features),
        market_share=sector.market_share_sources,
    )
