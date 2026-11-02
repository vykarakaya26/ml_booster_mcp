"""ProviderFactory implementation. New provider: write the class, add it to PROVIDERS, set `adapter:` in the catalog."""

from __future__ import annotations

from collections.abc import Callable

from mlbooster.application.errors import ProviderNotImplemented
from mlbooster.application.ports import SeriesProvider
from mlbooster.domain.model import Source
from mlbooster.infrastructure.http_client import RateLimiter, SecureHttpClient
from mlbooster.infrastructure.providers.bddk import BddkProvider
from mlbooster.infrastructure.providers.epdk import EpdkProvider
from mlbooster.infrastructure.providers.epias import EpiasProvider
from mlbooster.infrastructure.providers.evds3 import Evds3Provider
from mlbooster.infrastructure.providers.odmd import OdmdProvider
from mlbooster.infrastructure.providers.sdmx import SdmxCsvProvider
from mlbooster.infrastructure.providers.tbb import TbbProvider
from mlbooster.infrastructure.providers.tuik import TuikProvider
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.storage.raw_store import RawStore

PROVIDERS: dict[str, type] = {
    "bddk": BddkProvider,
    "epdk": EpdkProvider,
    "epias": EpiasProvider,
    "evds3": Evds3Provider,
    "odmd": OdmdProvider,
    "sdmx": SdmxCsvProvider,
    "tbb": TbbProvider,
    "tuik": TuikProvider,
}


class ProviderRegistry:
    def __init__(
        self,
        secrets: EnvSecretProvider,
        raw_store: RawStore | None = None,
        http_factory: Callable[[Source], SecureHttpClient] | None = None,
    ):
        self._secrets = secrets
        self._raw_store = raw_store
        self._http_factory = http_factory or self._default_http
        # One limiter per source: the rps limit holds even if every call opens a new provider.
        self._limiters: dict[str, RateLimiter] = {}

    def _default_http(self, source: Source) -> SecureHttpClient:
        limiter = self._limiters.setdefault(source.id, RateLimiter(source.rate_limit.rps))
        return SecureHttpClient(rps=source.rate_limit.rps, limiter=limiter)

    def available(self) -> set[str]:
        return set(PROVIDERS)

    def create(self, source: Source) -> SeriesProvider:
        cls = PROVIDERS.get(source.provider or "")
        if cls is None:
            raise ProviderNotImplemented(f"{source.id} için provider henüz uygulanmadı")
        return cls(source, http=self._http_factory(source), secrets=self._secrets, raw_store=self._raw_store)
