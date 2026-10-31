"""ProviderFactory uygulaması. Yeni sağlayıcı: sınıfı yaz, PROVIDERS'a ekle, katalogda `adapter:` ver."""

from __future__ import annotations

from collections.abc import Callable

from mlbooster.application.errors import ProviderNotImplemented
from mlbooster.application.ports import SeriesProvider
from mlbooster.domain.model import Source
from mlbooster.infrastructure.http_client import RateLimiter, SecureHttpClient
from mlbooster.infrastructure.providers.evds3 import Evds3Provider
from mlbooster.infrastructure.providers.sdmx import SdmxCsvProvider
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.storage.raw_store import RawStore

PROVIDERS: dict[str, type] = {
    "evds3": Evds3Provider,
    "sdmx": SdmxCsvProvider,
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
        # Kaynak başına tek limiter: her çağrı yeni sağlayıcı açsa da rps sınırı korunur.
        self._limiters: dict[str, RateLimiter] = {}

    def _default_http(self, source: Source) -> SecureHttpClient:
        limiter = self._limiters.setdefault(source.id, RateLimiter(source.rate_limit.rps))
        return SecureHttpClient(rps=source.rate_limit.rps, limiter=limiter)

    def available(self) -> set[str]:
        return set(PROVIDERS)

    def create(self, source: Source) -> SeriesProvider:
        cls = PROVIDERS.get(source.provider or "")
        if cls is None:
            raise ProviderNotImplemented(f"{source.id} için sağlayıcı henüz yazılmadı")
        return cls(source, http=self._http_factory(source), secrets=self._secrets, raw_store=self._raw_store)
