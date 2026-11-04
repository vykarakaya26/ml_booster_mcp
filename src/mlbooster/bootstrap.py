"""Composition root: the single place that wires concrete port implementations into the use cases.

In the onion architecture the inner layers do not know the outer ones; dependencies are injected here, at the
outermost layer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property

from mlbooster.application.dto import RunnerReadiness
from mlbooster.application.errors import ProviderNotImplemented
from mlbooster.application.ports import (
    CatalogOverrides,
    CatalogRepository,
    DocumentReader,
    EvidenceStore,
    ObservationStore,
    PanelStore,
    ProviderFactory,
    ResearchRunner,
    TableReader,
    UpdateSource,
)
from mlbooster.application.use_cases import (
    AnalyzeBrand,
    AnalyzeMarketShare,
    AuditPanel,
    BuildBrandFeatures,
    BuildPanel,
    CheckForUpdates,
    DiscoverSeries,
    EngineerFeatures,
    EvidenceTable,
    ExportTable,
    FetchSeries,
    GetSectorMap,
    ListEvidence,
    ListReadyProviders,
    ListSectors,
    ListShareSeries,
    ListSources,
    PlanEvidenceSearch,
    PreparePanel,
    ReadEvidenceDocument,
    RecordEvidence,
    RejectEvidence,
    ResolveBrand,
    RunHealthChecks,
    SearchInterest,
    SearchSeries,
    SearchTermCatalog,
    SetFeatureSeries,
    SourceShareTable,
    StoreEntityDirectory,
    TrendsLookup,
    TrendsMirrorControl,
    WebResearch,
)
from mlbooster.application.use_cases.trends_mirror import MirrorSource, MirrorState
from mlbooster.domain.errors import InvalidInput
from mlbooster.infrastructure.catalog_yaml import YamlCatalogRepository
from mlbooster.infrastructure.claude_runner import ClaudeCodeRunner
from mlbooster.infrastructure.document_reader import PdfDocumentReader
from mlbooster.infrastructure.providers.bigquery_mirror import TermLookups
from mlbooster.infrastructure.providers.registry import ProviderRegistry
from mlbooster.infrastructure.search_terms_yaml import YamlSearchTerms
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.settings import Settings
from mlbooster.infrastructure.storage.evidence_store import DuckDbEvidenceStore
from mlbooster.infrastructure.storage.observation_store import ParquetObservationStore
from mlbooster.infrastructure.storage.panel_store import ParquetPanelStore
from mlbooster.infrastructure.storage.raw_store import RawStore
from mlbooster.infrastructure.storage.trends_mirror import TrendsMirror
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
    evidence_store: EvidenceStore | None = None
    search_terms: SearchTermCatalog | None = None
    term_lookup: Callable[[], TrendsLookup] | None = None  # opened on first use: nothing runs without an explicit request
    mirror_state: MirrorState | None = None
    mirror_source: Callable[[], MirrorSource] | None = None
    documents: DocumentReader | None = None
    research_runner: ResearchRunner | None = None

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
    def share_tables(self) -> SourceShareTable:
        return SourceShareTable(self.catalog, self.fetch_series, self.observations)

    @cached_property
    def market_share(self) -> AnalyzeMarketShare:
        return AnalyzeMarketShare(self.build_panel, self._tables, self.share_tables)

    @cached_property
    def resolve_brand(self) -> ResolveBrand:
        directory = StoreEntityDirectory(self.catalog, self.providers, self.fetch_series, self.observations)
        return ResolveBrand(self.catalog, directory, self.fetch_series, self.list_evidence)

    @cached_property
    def analyze_brand(self) -> AnalyzeBrand:
        return AnalyzeBrand(self.catalog, self.resolve_brand, self.market_share, self.share_tables, self.list_evidence, lambda: self.search_interest)

    @cached_property
    def brand_features(self) -> BuildBrandFeatures:
        return BuildBrandFeatures(self.catalog, self.resolve_brand, self.share_tables, self.build_panel, self.panels, lambda: self.search_interest)

    @property
    def search_interest(self) -> SearchInterest:
        """Built per use: the user's answer about the local copy may change while the process runs."""
        if self.term_lookup is None or self.search_terms is None:
            raise ProviderNotImplemented("Arama ilgisi bu kurulumda yapılandırılmamış")
        return SearchInterest(self.catalog, self.search_terms, self.term_lookup())

    @cached_property
    def trends_mirror(self) -> TrendsMirrorControl:
        if self.mirror_state is None or self.mirror_source is None:
            raise ProviderNotImplemented("Yerel kopya bu kurulumda yapılandırılmamış")
        return TrendsMirrorControl(self.mirror_state, self.mirror_source)

    @cached_property
    def plan_evidence(self) -> PlanEvidenceSearch:
        return PlanEvidenceSearch(self.catalog, self._evidence)

    @cached_property
    def read_evidence(self) -> ReadEvidenceDocument:
        return ReadEvidenceDocument(self.catalog, self.documents or PdfDocumentReader())

    @cached_property
    def record_evidence(self) -> RecordEvidence:
        return RecordEvidence(self.catalog, self._evidence)

    @cached_property
    def list_evidence(self) -> ListEvidence:
        return ListEvidence(self.catalog, self._evidence)

    @cached_property
    def reject_evidence(self) -> RejectEvidence:
        return RejectEvidence(self.catalog, self._evidence)

    @cached_property
    def web_research(self) -> WebResearch:
        return WebResearch(self.catalog, self.research_runner or _NoRunner(), self._evidence)

    @cached_property
    def evidence_table(self) -> EvidenceTable:
        return EvidenceTable(self.catalog, self._evidence)

    @property
    def _tables(self) -> TableReader:
        return self.tables or LocalTableReader()

    @property
    def _evidence(self) -> EvidenceStore:
        return self.evidence_store or _NoEvidence()


class _NoUpdates:
    def latest_version(self) -> str | None:
        return None


class _NoRunner:
    def readiness(self) -> RunnerReadiness:
        return RunnerReadiness(False, "Web araması bu kurulumda kullanılamıyor.", "Paneli `mlbooster web` ile başlatın.")

    def run(self, brand, sector, on_event, cancel):
        raise InvalidInput("Web araması bu kurulumda kullanılamıyor")


class _NoEvidence:
    """Without a store nothing can be recorded; reading simply finds nothing."""

    def add(self, evidence) -> bool:
        raise InvalidInput("Bulgu deposu bağlı değil")

    def list(self, brand_keys=None) -> list:
        return []

    def remove(self, evidence_id, reason, at):
        return None

    def rejection(self, evidence_id):
        return None


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or Settings.from_env()
    catalog = YamlCatalogRepository(settings.catalog_dir, settings.overrides_path)
    mirror = TrendsMirror(settings.data_dir / "mirror" / "bq_trends")
    lookups = TermLookups(lambda: catalog.load().source("bq_trends"), mirror)
    return Container(
        catalog=catalog,
        overrides=catalog,
        providers=ProviderRegistry(EnvSecretProvider(), RawStore(settings.data_dir / "bronze")),
        observations=ParquetObservationStore(settings.data_dir / "silver"),
        panels=ParquetPanelStore(settings.data_dir / "gold"),
        updates=GitHubReleaseSource(settings.data_dir / "update_check.json"),
        tables=LocalTableReader(),
        evidence_store=DuckDbEvidenceStore(settings.data_dir / "evidence"),
        research_runner=ClaudeCodeRunner(settings.data_dir),
        search_terms=YamlSearchTerms(settings.catalog_dir / "search_terms.yaml"),
        term_lookup=lookups.current,
        mirror_state=mirror,
        mirror_source=lookups.local,
    )
