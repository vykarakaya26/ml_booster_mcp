"""Port'lar: çekirdeğin dış dünyadan beklediği yetenekler. Altyapı bunları uygular."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

import pandas as pd

from mlbooster.application.dto import FetchRequest
from mlbooster.domain.model import Catalog, HealthResult, SeedSeries, Source


class CatalogRepository(Protocol):
    def load(self) -> Catalog: ...


class CatalogOverrides(Protocol):
    """Kullanıcının keşfettiği seri kodlarını paket kataloğuna dokunmadan kalıcılaştırır."""

    def set_feature(self, sector_id: str, feature: str, seed: SeedSeries, source_id: str) -> str:
        """Override'ı yazar, konumunu döner; sonraki `load()` yeni değeri görür."""
        ...


class SupportsSearch(Protocol):
    """Opsiyonel sağlayıcı yeteneği: anahtar kelimeyle veri grubu/seri arama."""

    def search(self, text: str, limit: int = 20) -> pd.DataFrame:
        """DataFrame[datagroup, name, freq, start, end]"""
        ...


class SeriesProvider(Protocol):
    """Tek bir veri kaynağına erişim. `fetch` gözlem çerçevesi (domain.observations) döner."""

    def list_series(self, query: str | None = None) -> pd.DataFrame:
        """DataFrame[series_id, name, freq, start, end, datagroup]"""
        ...

    def fetch(self, request: FetchRequest) -> pd.DataFrame: ...

    def healthcheck(self) -> HealthResult: ...

    def close(self) -> None: ...


class ProviderFactory(Protocol):
    def available(self) -> set[str]:
        """Sağlayıcısı yazılmış kaynakların `provider` anahtarları."""
        ...

    def create(self, source: Source) -> SeriesProvider: ...


class ObservationStore(Protocol):
    def write(self, observations: pd.DataFrame) -> None: ...

    def read(
        self,
        source_id: str,
        series_ids: list[str] | None = None,
        as_of: datetime | None = None,
    ) -> pd.DataFrame:
        """Her anahtar için `as_of` anına kadarki en son vintage."""
        ...


class PanelStore(Protocol):
    def save(self, sector_id: str, panel: pd.DataFrame) -> str:
        """Paneli kalıcı hale getirir ve konumunu döner."""
        ...

    def export(self, panel: pd.DataFrame, path: Path) -> str:
        """Kullanıcının açıkça verdiği yola dışa aktarır (yalnızca yerel CLI)."""
        ...
