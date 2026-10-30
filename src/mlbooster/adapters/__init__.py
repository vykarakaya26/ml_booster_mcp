"""Adapter kaydı. Katalogda `adapter:` alanı bu sözlüğün anahtarlarından biridir."""

from __future__ import annotations

from mlbooster.adapters.base import BaseAdapter, HealthResult
from mlbooster.adapters.evds3 import Evds3Adapter
from mlbooster.catalog import Source

REGISTRY: dict[str, type[BaseAdapter]] = {
    "evds3": Evds3Adapter,
}


def get_adapter(source: Source, **kwargs) -> BaseAdapter:
    if source.adapter is None:
        raise NotImplementedError(f"{source.id} için adapter henüz yazılmadı (katalogda adapter: null)")
    return REGISTRY[source.adapter](source, **kwargs)


__all__ = ["REGISTRY", "BaseAdapter", "HealthResult", "get_adapter"]
