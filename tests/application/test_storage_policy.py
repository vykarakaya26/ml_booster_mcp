"""Sources whose terms forbid storing data (`storage.observations: false`) are never written to the observation store."""

from dataclasses import replace
from datetime import date

import pandas as pd

from fakes import FakeProviderFactory, InMemoryObservationStore
from mlbooster.application.dto import FetchRequest
from mlbooster.application.use_cases.series import FetchSeries
from mlbooster.domain.model import StoragePolicy

CODE = "TP.DK.USD.A"


class OneSource:
    def __init__(self, source):
        self._source = source

    def load(self):
        return self

    def source(self, source_id):
        return self._source


def _fetch(catalog, storage: StoragePolicy):
    series = {CODE: pd.Series([1.0, 2.0], index=pd.date_range("2026-01-31", periods=2, freq="ME"))}
    factory, store = FakeProviderFactory(series), InMemoryObservationStore()
    source = replace(catalog.source("evds3"), storage=storage)
    return FetchSeries(OneSource(source), factory, store), factory, store


def test_observations_are_not_cached_when_the_source_forbids_storage(catalog):
    fetch, factory, store = _fetch(catalog, StoragePolicy(raw=False, observations=False))
    request = FetchRequest.create("evds3", [CODE], date(2026, 1, 1), date(2026, 2, 28))
    assert len(fetch(request)) == 2
    summary = fetch.summarize(request)
    assert summary.rows == 2 and "yerel depoya yazılmadı" in summary.attribution
    assert store.frames == [] and store.requested == {}
    assert len(factory.created) == 2  # fetched again instead of read from a cache


def test_observations_are_cached_by_default(catalog):
    fetch, _, store = _fetch(catalog, StoragePolicy())
    summary = fetch.summarize(FetchRequest.create("evds3", [CODE], date(2026, 1, 1), date(2026, 2, 28)))
    assert len(store.frames) == 1 and "yazılmadı" not in summary.attribution
