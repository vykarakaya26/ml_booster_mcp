"""Series discovery and fetching."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pandas as pd

from mlbooster.application.dto import FetchRequest, FetchSummary
from mlbooster.application.ports import CatalogRepository, ObservationStore, ProviderFactory
from mlbooster.application.use_cases._providers import open_provider
from mlbooster.domain.model import Source
from mlbooster.domain.validation import DateRange, series_code, slug

MAX_TAIL_ROWS = 500


class DiscoverSeries:
    def __init__(self, catalog: CatalogRepository, providers: ProviderFactory):
        self._catalog = catalog
        self._providers = providers

    def __call__(self, source_id: str, query: str | None = None) -> pd.DataFrame:
        source = self._catalog.load().source(slug(source_id, "source"))
        with open_provider(self._providers, source) as provider:
            return provider.list_series(series_code(query) if query else None)


class FetchSeries:
    """Fetches from the provider and writes to the observation store, unless the source's terms forbid storing data
    (`storage.observations: false`); such sources are fetched again on every request.

    Sources with an incremental history policy (`history.revision_days`, e.g. EPİAŞ, whose services answer at most a
    few months per call) are not refetched in full: only the history before the earliest earlier request and the tail
    from (last cached day - revision_days) are requested; the result is read back from the store for the whole
    requested range. `refresh=True` refetches everything."""

    def __init__(self, catalog: CatalogRepository, providers: ProviderFactory, store: ObservationStore):
        self._catalog = catalog
        self._providers = providers
        self._store = store

    def default_start(self, source_id: str, fallback: date, today: date | None = None) -> date:
        """Start used when the user gives none: `history.default_years` back for sources that set it (from the first
        day of that month), otherwise `fallback`."""
        years = self._catalog.load().source(slug(source_id, "source")).history.default_years
        if not years:
            return fallback
        today = today or date.today()
        return date(today.year - years, today.month, 1)

    def __call__(self, request: FetchRequest, refresh: bool = False) -> pd.DataFrame:
        return self._run(request, refresh)[0]

    def plan(self, request: FetchRequest, refresh: bool = False) -> list[DateRange] | None:
        """Ranges to request from the source; None = the whole request (no incremental policy, refresh, transformed
        request or nothing usable in the store). An empty list means the store already holds the range."""
        source = self._catalog.load().source(request.source_id)
        if refresh or not _incremental(source) or request.formula or request.agg or request.freq:
            return None
        cached = self._store.read(source.id, list(request.series_ids))
        cached = cached[cached["metric"] == request.metric] if not cached.empty else cached
        asked, last = [], []
        for code in request.series_ids:
            rows = cached[cached["series_id"] == code] if not cached.empty else cached
            first = self._store.requested_start(source.id, code, request.metric)
            if rows.empty or first is None:
                return None
            asked.append(first)
            last.append(pd.Timestamp(rows["date"].max()).date())
        start, end = request.period.start, request.period.end
        ranges = []
        if start < max(asked):  # keeps the store contiguous from the recorded start: the gap is filled entirely
            ranges.append(DateRange(start, max(asked) - timedelta(days=1)))
        tail = max(start, min(last) - timedelta(days=source.history.revision_days or 0))
        if tail <= end:
            if ranges and tail <= ranges[-1].end + timedelta(days=1):
                ranges[-1] = DateRange(ranges[-1].start, max(end, ranges[-1].end))
            else:
                ranges.append(DateRange(tail, end))
        return ranges

    def _run(self, request: FetchRequest, refresh: bool) -> tuple[pd.DataFrame, list[DateRange]]:
        source = self._catalog.load().source(request.source_id)
        ranges = self.plan(request, refresh)
        if ranges is None:
            observations = self._fetch(source, request)
            return observations, [request.period]
        for part in ranges:
            self._fetch(source, replace(request, period=part))
        stored = self._store.read(source.id, list(request.series_ids))
        if stored.empty:
            return stored, ranges
        dates = pd.to_datetime(stored["date"])
        keep = (stored["metric"] == request.metric) & (dates >= pd.Timestamp(request.period.start)) & (
            dates <= pd.Timestamp(request.period.end)
        )
        return stored[keep].sort_values(["series_id", "entity_id", "date"], ignore_index=True), ranges

    def _fetch(self, source: Source, request: FetchRequest) -> pd.DataFrame:
        with open_provider(self._providers, source) as provider:
            observations = provider.fetch(request)
        if not observations.empty and source.storage.observations:
            self._store.write(observations)
            returned = sorted(set(observations["series_id"]))
            self._store.record_requested_start(source.id, returned, request.metric, request.period.start)
        return observations

    def summarize(self, request: FetchRequest, max_rows: int = 50, refresh: bool = False) -> FetchSummary:
        df, fetched = self._run(request, refresh)
        source = self._catalog.load().source(request.source_id)
        attribution = f"Kaynak: {source.owner} — {source.name}"
        if not source.storage.observations:
            attribution += " (kullanım koşulları gereği veriler yerel depoya yazılmadı)"
        ranges = [(r.start.isoformat(), r.end.isoformat()) for r in fetched]
        if df.empty:
            return FetchSummary(0, [], [], attribution, ranges)
        by_series = df.groupby(["series_id", "metric", "freq"])["date"].agg(["count", "min", "max"]).reset_index()
        tail = df.sort_values("date").tail(max(0, min(max_rows, MAX_TAIL_ROWS)))
        return FetchSummary(
            rows=len(df),
            by_series=by_series.astype(str).to_dict(orient="records"),
            tail=tail[["series_id", "metric", "date", "value"]].astype({"date": str}).to_dict(orient="records"),
            attribution=attribution,
            fetched=ranges,
        )


def _incremental(source: Source) -> bool:
    return source.history.incremental and source.storage.observations
