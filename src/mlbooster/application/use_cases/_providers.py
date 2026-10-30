from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from mlbooster.application.errors import ProviderNotImplemented
from mlbooster.application.ports import ProviderFactory, SeriesProvider
from mlbooster.domain.model import Source


@contextmanager
def open_provider(factory: ProviderFactory, source: Source) -> Iterator[SeriesProvider]:
    if source.provider is None or source.provider not in factory.available():
        raise ProviderNotImplemented(f"{source.id} için sağlayıcı henüz yazılmadı")
    provider = factory.create(source)
    try:
        yield provider
    finally:
        provider.close()
