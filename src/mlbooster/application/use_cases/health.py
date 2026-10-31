from __future__ import annotations

from mlbooster.application.errors import ProviderError, ProviderNotImplemented
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
            except ProviderError as exc:  # ör. sağlayıcı kurulamadı (host sabitleme)
                results.append(HealthResult(source.id, HealthStatus.FAIL, f"{type(exc).__name__}: {exc}"))
        return results


class CheckForUpdates:
    """Kurulu sürümü yayımlanan en son sürümle karşılaştırır; güncelleme komutunu söyler."""

    def __init__(self, current: str, source):
        self._current = current
        self._source = source

    def __call__(self) -> dict:
        latest = self._source.latest_version()
        def key(v: str) -> tuple[int, ...]:
            return tuple(int(x) for x in v.split(".")[:3] if x.isdigit())
        available = bool(latest) and key(latest) > key(self._current)
        return {
            "current": self._current,
            "latest": latest,
            "update_available": available,
            "how": (
                "Claude Code: `claude plugin update ml-booster` (ya da /plugin → Marketplaces → ml-booster-tools → "
                "otomatik güncellemeyi aç). pip kurulumunda: "
                "`pip install -U \"git+https://github.com/vykarakaya26/ml_booster_mcp.git@v" + str(latest) + "\"`"
            ) if available else None,
        }
