from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from mlbooster.application.ports import ProviderFactory, SeriesProvider
from mlbooster.domain.model import Source


@contextmanager
def open_provider(factory: ProviderFactory, source: Source) -> Iterator[SeriesProvider]:
    provider = factory.create(source)  # ProviderNotImplemented if there is no provider
    try:
        yield provider
    finally:
        provider.close()
