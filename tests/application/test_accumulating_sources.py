"""Sources that publish only a recent window (Google Trends lists are kept for 30 days upstream) build their history in
the observation store: the panel reads the store back after every fetch instead of using only the latest window."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from fakes import InMemoryObservationStore, InMemoryPanelStore
from mlbooster.application.use_cases import BuildPanel
from mlbooster.domain.model import Catalog, MacroFeature, Sector, StoragePolicy

CODE = "BQT.TOP.EKONOMI.SAYI"


class WindowProvider:
    """Returns one value per day, but only for the last `window` days before `today` (like the upstream retention)."""

    def __init__(self, state):
        self.state = state

    def fetch(self, request):
        today, window = self.state["today"], 30
        days = pd.date_range(max(request.period.start, today - timedelta(days=window)), min(request.period.end, today))
        self.state["calls"] += 1
        return pd.DataFrame({
            "source_id": "bq_trends", "dataset_id": "international_top_terms", "series_id": CODE, "entity_id": "",
            "entity_name": None, "metric": "level", "date": days.date, "freq": "D", "value": 2.0, "unit": "count",
            "currency": None, "is_proxy": True, "vintage_ts": datetime.now(UTC), "publication_lag_days": 1,
        })  # fmt: skip

    def close(self):
        pass


class Factory:
    def __init__(self, state):
        self.state = state

    def available(self):
        return {"bq_trends"}

    def create(self, source):
        return WindowProvider(self.state)


class Repo:
    def __init__(self, catalog):
        self.catalog = catalog

    def load(self):
        return self.catalog


def _catalog(catalog, storage=None):
    source = replace(catalog.source("bq_trends"), storage=storage or StoragePolicy())
    sector = Sector("deneme", "Deneme", (MacroFeature("ekonomi_gundemi", "bq_trends", CODE),))
    return Repo(Catalog(sources={"bq_trends": source}, sectors={"deneme": sector}))


def test_history_accumulates_in_the_store(catalog):
    state = {"today": date(2026, 6, 30), "calls": 0}
    store = InMemoryObservationStore()
    build = BuildPanel(_catalog(catalog), Factory(state), store, InMemoryPanelStore())
    build("deneme", date(2026, 1, 1), date(2026, 6, 30), refresh=True)
    state["today"] = date(2026, 9, 30)
    result = build("deneme", date(2026, 1, 1), date(2026, 9, 30), refresh=True)
    assert state["calls"] == 2
    filled = result.panel["ekonomi_gundemi"].dropna()
    # June comes only from the first fetch (the second window starts on 31 August), September from the second.
    assert pd.Timestamp("2026-06-30") in filled.index and pd.Timestamp("2026-09-30") in filled.index


def test_without_storage_the_latest_window_is_used(catalog):
    state = {"today": date(2026, 6, 30), "calls": 0}
    store = InMemoryObservationStore()
    build = BuildPanel(_catalog(catalog, StoragePolicy(raw=False, observations=False)), Factory(state), store, InMemoryPanelStore())
    result = build("deneme", date(2026, 1, 1), date(2026, 6, 30), refresh=True)
    assert store.frames == [] and not result.panel.empty
