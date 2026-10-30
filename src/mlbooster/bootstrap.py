"""Composition root: port'ların somut uygulamalarını use case'lere bağlayan tek yer.

Onion mimarisinde içteki katmanlar dıştakileri bilmez; bağımlılıklar burada, en dışta enjekte edilir.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from mlbooster.application.ports import (
    CatalogOverrides,
    CatalogRepository,
    ObservationStore,
    PanelStore,
    ProviderFactory,
)
from mlbooster.application.use_cases import (
    AuditPanel,
    BuildPanel,
    RunBacktest,
    SearchSeries,
    SetFeatureSeries,
    DiscoverSeries,
    FetchSeries,
    GetSectorMap,
    ListSectors,
    ListSources,
    RunHealthChecks,
)
from mlbooster.infrastructure.catalog_yaml import YamlCatalogRepository
from mlbooster.infrastructure.providers.registry import ProviderRegistry
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.settings import Settings
from mlbooster.infrastructure.storage.observation_store import ParquetObservationStore
from mlbooster.infrastructure.storage.panel_store import ParquetPanelStore
from mlbooster.infrastructure.storage.raw_store import RawStore


@dataclass
class Container:
    catalog: CatalogRepository
    providers: ProviderFactory
    observations: ObservationStore
    panels: PanelStore
    overrides: CatalogOverrides

    @cached_property
    def list_sectors(self) -> ListSectors:
        return ListSectors(self.catalog)

    @cached_property
    def list_sources(self) -> ListSources:
        return ListSources(self.catalog)

    @cached_property
    def sector_map(self) -> GetSectorMap:
        return GetSectorMap(self.catalog, self.providers)

    @cached_property
    def discover_series(self) -> DiscoverSeries:
        return DiscoverSeries(self.catalog, self.providers)

    @cached_property
    def fetch_series(self) -> FetchSeries:
        return FetchSeries(self.catalog, self.providers, self.observations)

    @cached_property
    def build_panel(self) -> BuildPanel:
        return BuildPanel(self.catalog, self.providers, self.observations, self.panels)

    @cached_property
    def health(self) -> RunHealthChecks:
        return RunHealthChecks(self.catalog, self.providers)

    @cached_property
    def search_series(self) -> SearchSeries:
        return SearchSeries(self.catalog, self.providers)

    @cached_property
    def set_feature_series(self) -> SetFeatureSeries:
        return SetFeatureSeries(self.catalog, self.providers, self.overrides)

    @cached_property
    def audit_panel(self) -> AuditPanel:
        return AuditPanel(self.build_panel)

    @cached_property
    def run_backtest(self) -> RunBacktest:
        return RunBacktest(self.build_panel)


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or Settings.from_env()
    catalog = YamlCatalogRepository(settings.catalog_dir, settings.overrides_path)
    return Container(
        catalog=catalog,
        overrides=catalog,
        providers=ProviderRegistry(EnvSecretProvider(), RawStore(settings.data_dir / "bronze")),
        observations=ParquetObservationStore(settings.data_dir / "silver"),
        panels=ParquetPanelStore(settings.data_dir / "gold"),
    )
