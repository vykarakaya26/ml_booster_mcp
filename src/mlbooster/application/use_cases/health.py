from __future__ import annotations

from mlbooster.application.errors import ProviderNotImplemented
from mlbooster.application.ports import CatalogRepository, ProviderFactory
from mlbooster.application.use_cases._providers import open_provider
from mlbooster.domain.model import HealthResult, HealthStatus
from mlbooster.domain.validation import slug


class RunHealthChecks:
    def __init__(self, catalog: CatalogRepository, providers: ProviderFactory):
        self._catalog = catalog
        self._providers = providers

    def __call__(self, source_id: str | None = None) -> list[HealthResult]:
        catalog = self._catalog.load()
        sources = [catalog.source(slug(source_id, "kaynak"))] if source_id else catalog.sources_by_priority()
        results = []
        for source in sources:
            try:
                with open_provider(self._providers, source) as provider:
                    results.append(provider.healthcheck())
            except ProviderNotImplemented:
                results.append(HealthResult(source.id, HealthStatus.SKIPPED, "sağlayıcı henüz yazılmadı"))
        return results
