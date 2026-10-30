"""Seri keşfi ve çekme."""

from __future__ import annotations

import pandas as pd

from mlbooster.application.dto import FetchRequest, FetchSummary
from mlbooster.application.ports import CatalogRepository, ObservationStore, ProviderFactory
from mlbooster.application.use_cases._providers import open_provider
from mlbooster.domain.validation import series_code, slug

MAX_TAIL_ROWS = 500


class DiscoverSeries:
    def __init__(self, catalog: CatalogRepository, providers: ProviderFactory):
        self._catalog = catalog
        self._providers = providers

    def __call__(self, source_id: str, query: str | None = None) -> pd.DataFrame:
        source = self._catalog.load().source(slug(source_id, "kaynak"))
        with open_provider(self._providers, source) as provider:
            return provider.list_series(series_code(query) if query else None)


class FetchSeries:
    """Sağlayıcıdan çeker, gözlem deposuna yazar."""

    def __init__(self, catalog: CatalogRepository, providers: ProviderFactory, store: ObservationStore):
        self._catalog = catalog
        self._providers = providers
        self._store = store

    def __call__(self, request: FetchRequest) -> pd.DataFrame:
        source = self._catalog.load().source(request.source_id)
        with open_provider(self._providers, source) as provider:
            observations = provider.fetch(request)
        if not observations.empty:
            self._store.write(observations)
        return observations

    def summarize(self, request: FetchRequest, max_rows: int = 50) -> FetchSummary:
        df = self(request)
        source = self._catalog.load().source(request.source_id)
        attribution = f"Kaynak: {source.owner} — {source.name}"
        if df.empty:
            return FetchSummary(0, [], [], attribution)
        by_series = df.groupby(["series_id", "metric", "freq"])["date"].agg(["count", "min", "max"]).reset_index()
        tail = df.sort_values("date").tail(max(0, min(max_rows, MAX_TAIL_ROWS)))
        return FetchSummary(
            rows=len(df),
            by_series=by_series.astype(str).to_dict(orient="records"),
            tail=tail[["series_id", "metric", "date", "value"]].astype({"date": str}).to_dict(orient="records"),
            attribution=attribution,
        )
