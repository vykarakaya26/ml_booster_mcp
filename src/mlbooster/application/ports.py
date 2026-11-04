"""Ports: the capabilities the core expects from the outside world. Infrastructure implements them."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Protocol

import pandas as pd

from mlbooster.application.dto import FetchRequest
from mlbooster.domain.model import Catalog, HealthResult, MacroFeature, SeedSeries, Source


class CatalogRepository(Protocol):
    def load(self) -> Catalog: ...


class CatalogOverrides(Protocol):
    """Persists series codes discovered by the user without touching the packaged catalog."""

    def set_feature(
        self,
        sector_id: str,
        feature: str,
        seed: SeedSeries,
        source_id: str,
        new_feature: MacroFeature | None = None,
    ) -> str:
        """Writes the override and returns its location; the next `load()` sees the new value.
        If `new_feature` is given, a new feature is added to the sector."""
        ...


class SupportsSearch(Protocol):
    """Optional provider capability: keyword search over data groups/series."""

    def search(self, text: str, limit: int = 20) -> pd.DataFrame:
        """DataFrame[datagroup, name, freq, start, end]"""
        ...


class SeriesProvider(Protocol):
    """Access to a single data source. `fetch` returns an observation frame (domain.observations)."""

    def list_series(self, query: str | None = None) -> pd.DataFrame:
        """DataFrame[series_id, name, freq, start, end, datagroup]"""
        ...

    def fetch(self, request: FetchRequest) -> pd.DataFrame: ...

    def healthcheck(self) -> HealthResult: ...

    def close(self) -> None: ...


class ProviderFactory(Protocol):
    def available(self) -> set[str]:
        """`provider` keys of the sources that have an implemented provider."""
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
        """For each key, the latest vintage up to the `as_of` instant."""
        ...

    def record_requested_start(self, source_id: str, series_ids: list[str], metric: str, start: date) -> None:
        """Records that the provider was asked for the full history of these series from `start` onwards.
        The record is only ever moved to an earlier date."""
        ...

    def requested_start(self, source_id: str, series_id: str, metric: str) -> date | None:
        """Earliest start the provider was asked for, for this series and metric; None without a record.
        If the series' first observation is later than this date, the source has no earlier data."""
        ...


class PanelStore(Protocol):
    def save(self, sector_id: str, panel: pd.DataFrame) -> str:
        """Persists the panel and returns its location."""
        ...

    def export(self, panel: pd.DataFrame, path: Path) -> str:
        """Exports to a path explicitly given by the user (local CLI only)."""
        ...


class UpdateSource(Protocol):
    def latest_version(self) -> str | None:
        """Latest published version (e.g. '0.4.0'); None if unknown."""
        ...


class TableReader(Protocol):
    def read(self, path: Path) -> pd.DataFrame:
        """Reads a local CSV/JSON table supplied by the user (CLI only; MCP does not accept file paths)."""
        ...

    def parse(self, text: str, fmt: str) -> pd.DataFrame:
        """Parses CSV/JSON table text held in memory (fmt: 'csv' | 'json'); writes nothing to disk."""
        ...
