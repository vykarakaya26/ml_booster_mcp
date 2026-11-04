"""Composition root: the single place that wires concrete port implementations into the use cases.

In the onion architecture the inner layers do not know the outer ones; dependencies are injected here, at the
outermost layer.
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
    TableReader,
    UpdateSource,
)
from mlbooster.application.use_cases import (
    AnalyzeMarketShare,
    AuditPanel,
    BuildPanel,
    CheckForUpdates,
    DiscoverSeries,
    EngineerFeatures,
    ExportTable,
    FetchSeries,
    GetSectorMap,
    ListReadyProviders,
    ListSectors,
    ListShareSeries,
    ListSources,
    PreparePanel,
    RunHealthChecks,
    SearchSeries,
    SetFeatureSeries,
    SourceShareTable,
)
from mlbooster.infrastructure.catalog_yaml import YamlCatalogRepository
from mlbooster.infrastructure.providers.registry import ProviderRegistry
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.settings import Settings
from mlbooster.infrastructure.storage.observation_store import ParquetObservationStore
from mlbooster.infrastructure.storage.panel_store import ParquetPanelStore
from mlbooster.infrastructure.storage.raw_store import RawStore
from mlbooster.infrastructure.table_reader import LocalTableReader
from mlbooster.infrastructure.update_check import GitHubReleaseSource


@dataclass
class Container:
    catalog: CatalogRepository
    providers: ProviderFactory
    observations: ObservationStore
    panels: PanelStore
    overrides: CatalogOverrides
    updates: UpdateSource | None = None
    tables: TableReader | None = None

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
    def check_updates(self) -> CheckForUpdates:
        from mlbooster import __version__

        return CheckForUpdates(__version__, self.updates or _NoUpdates())

    @cached_property
    def prepare_panel(self) -> PreparePanel:
        return PreparePanel(self.build_panel)

    @cached_property
    def engineer_features(self) -> EngineerFeatures:
        return EngineerFeatures(self.build_panel)

    @cached_property
    def ready_providers(self) -> ListReadyProviders:
        return ListReadyProviders(self.providers)

    @cached_property
    def export_table(self) -> ExportTable:
        return ExportTable(self.panels)

    @cached_property
    def share_series(self) -> ListShareSeries:
        return ListShareSeries(self.catalog, self.providers)

    @cached_property
    def market_share(self) -> AnalyzeMarketShare:
        return AnalyzeMarketShare(
            self.build_panel, self._tables, SourceShareTable(self.catalog, self.fetch_series, self.observations)
        )

    @property
    def _tables(self) -> TableReader:
        return self.tables or LocalTableReader()


class _NoUpdates:
    def latest_version(self) -> str | None:
        return None


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or Settings.from_env()
    catalog = YamlCatalogRepository(settings.catalog_dir, settings.overrides_path)
    return Container(
        catalog=catalog,
        overrides=catalog,
        providers=ProviderRegistry(EnvSecretProvider(), RawStore(settings.data_dir / "bronze")),
        observations=ParquetObservationStore(settings.data_dir / "silver"),
        panels=ParquetPanelStore(settings.data_dir / "gold"),
        updates=GitHubReleaseSource(settings.data_dir / "update_check.json"),
        tables=LocalTableReader(),
    )
