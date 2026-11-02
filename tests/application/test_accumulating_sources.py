"""Google Trends lists are kept for 30 days upstream: with the catalog history policy (revision_days) FetchSeries
serves the whole requested range from the store, so the history accumulated by earlier fetches is kept."""

from datetime import UTC, date, datetime, timedelta

import pandas as pd

from fakes import InMemoryObservationStore
from mlbooster.application.dto import FetchRequest
from mlbooster.application.use_cases.series import FetchSeries

CODE = "BQT.TOP.EKONOMI.SAYI"


class WindowProvider:
    """One value per day, but only for the last 30 days before `today` (like the upstream retention)."""

    def __init__(self, state):
        self.state = state

    def fetch(self, request):
        today = self.state["today"]
        days = pd.date_range(max(request.period.start, today - timedelta(days=30)), min(request.period.end, today))
        self.state["calls"].append(request.period)
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


def test_history_accumulates_in_the_store(catalog_repo):
    assert catalog_repo.load().source("bq_trends").history.incremental
    state = {"today": date(2026, 6, 30), "calls": []}
    fetch = FetchSeries(catalog_repo, Factory(state), InMemoryObservationStore())
    start = fetch.default_start("bq_trends", date(2010, 1, 1), today=date(2026, 6, 30))
    assert start == date(2021, 6, 1)  # five years back by default
    first = fetch(FetchRequest.create("bq_trends", [CODE], start, date(2026, 6, 30)))
    assert first["date"].min() == date(2026, 5, 31)
    state["today"] = date(2026, 9, 30)
    second = fetch(FetchRequest.create("bq_trends", [CODE], start, date(2026, 9, 30)))
    # The second call asks only for the recent tail, yet returns the June days kept from the first call.
    assert state["calls"][1].start > start
    assert second["date"].min() == date(2026, 5, 31) and second["date"].max() == date(2026, 9, 30)
    assert len(second) == 31 + 31
