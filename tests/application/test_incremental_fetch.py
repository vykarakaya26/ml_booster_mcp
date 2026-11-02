"""Incremental history for sources with `history.revision_days` (EPİAŞ): the store keeps what was fetched and later
calls only request the missing history and the last days."""

from dataclasses import replace
from datetime import UTC, date, datetime

import pandas as pd
import pytest
from pydantic import ValidationError

from fakes import InMemoryObservationStore
from mlbooster.application.dto import FetchRequest
from mlbooster.application.use_cases.series import FetchSeries
from mlbooster.domain.model import HistoryPolicy, StoragePolicy
from mlbooster.domain.observations import empty_observations
from mlbooster.domain.validation import DateRange
from mlbooster.infrastructure.catalog_yaml import HistorySchema

LAST_DAY = date(2026, 10, 31)  # the fake source has data up to this day


class DailyProvider:
    def __init__(self, calls):
        self.calls = calls

    def fetch(self, request):
        self.calls.append(request)
        days = pd.date_range(request.period.start, min(request.period.end, LAST_DAY), freq="D")
        if days.empty:
            return empty_observations()
        frames = [
            pd.DataFrame({"series_id": code, "date": days.date, "value": range(len(days))})
            for code in request.series_ids
        ]
        return pd.concat(frames).assign(
            source_id="epias", dataset_id="dam-mcp", entity_id="", entity_name=None, metric="level", freq="D",
            unit="try_per_mwh", currency="TRY", is_proxy=False, vintage_ts=datetime.now(UTC), publication_lag_days=1,
        )  # fmt: skip

    def close(self):
        pass


class Factory:
    def __init__(self):
        self.calls = []

    def available(self):
        return {"epias", "evds3"}

    def create(self, source):
        return DailyProvider(self.calls)


class Catalog:
    def __init__(self, catalog, **changes):
        self._catalog, self._changes = catalog, changes

    def load(self):
        return self

    def source(self, source_id):
        return replace(self._catalog.source(source_id), **self._changes)


@pytest.fixture
def setup(catalog):
    factory, store = Factory(), InMemoryObservationStore()
    return FetchSeries(Catalog(catalog), factory, store), factory, store


def _req(start, end, codes=("EPIAS.PTF",), **kw):
    return FetchRequest.create("epias", list(codes), start, end, **kw)


def _periods(factory):
    return [(c.period.start, c.period.end) for c in factory.calls]


def test_catalog_history_policy(catalog):
    assert catalog.source("epias").history == HistoryPolicy(default_years=5, revision_days=7)
    assert catalog.source("evds3").history == HistoryPolicy() and not catalog.source("evds3").history.incremental
    with pytest.raises(ValidationError):
        HistorySchema(default_years=0)
    with pytest.raises(ValidationError):
        HistorySchema(revision_days=-1)


def test_default_start_is_the_source_history_depth(setup):
    fetch, _, _ = setup
    assert fetch.default_start("epias", date(2010, 1, 1), today=date(2026, 11, 3)) == date(2021, 11, 1)
    assert fetch.default_start("evds3", date(2010, 1, 1), today=date(2026, 11, 3)) == date(2010, 1, 1)


def test_second_call_is_served_from_the_store(setup):
    fetch, factory, _ = setup
    first = fetch(_req(date(2024, 1, 1), date(2026, 10, 31)))
    again = fetch.summarize(_req(date(2024, 1, 1), date(2026, 10, 31)))
    assert len(factory.calls) == 2 and _periods(factory)[1] == (date(2026, 10, 24), date(2026, 10, 31))
    assert again.rows == len(first) == 1035 and again.fetched == [("2026-10-24", "2026-10-31")]


def test_a_window_inside_the_store_needs_no_request(setup):
    fetch, factory, _ = setup
    fetch(_req(date(2024, 1, 1), date(2026, 10, 31)))
    window = fetch.summarize(_req(date(2024, 3, 1), date(2024, 6, 30)))
    assert len(factory.calls) == 1 and window.fetched == []
    assert window.rows == 122 and window.by_series[0]["min"] == "2024-03-01"


def test_new_days_and_older_history_are_requested_separately(setup):
    fetch, factory, store = setup
    fetch(_req(date(2024, 1, 1), date(2026, 9, 30)))
    df = fetch(_req(date(2022, 1, 1), date(2026, 10, 31)))
    assert _periods(factory)[1:] == [(date(2022, 1, 1), date(2023, 12, 31)), (date(2026, 9, 23), date(2026, 10, 31))]
    assert df["date"].min() == date(2022, 1, 1) and df["date"].max() == LAST_DAY
    assert df.groupby("date").size().max() == 1  # one row per day despite the overlapping tail
    assert store.requested_start("epias", "EPIAS.PTF", "level") == date(2022, 1, 1)


def test_an_older_window_fills_the_gap_up_to_the_stored_history(setup):
    """Asking 2020 when the store starts in 2024 fetches 2020-01 → 2023-12, so the store stays contiguous."""
    fetch, factory, _ = setup
    fetch(_req(date(2024, 1, 1), date(2026, 10, 31)))
    df = fetch(_req(date(2020, 1, 1), date(2020, 12, 31)))
    assert _periods(factory)[1:] == [(date(2020, 1, 1), date(2023, 12, 31))]
    assert df["date"].min() == date(2020, 1, 1) and df["date"].max() == date(2020, 12, 31)


def test_overlapping_head_and_tail_become_one_request(setup):
    fetch, factory, _ = setup
    fetch(_req(date(2026, 10, 20), date(2026, 10, 25)))  # the tail (from 10-18) reaches into the head (to 10-19)
    fetch(_req(date(2026, 10, 1), date(2026, 11, 2)))
    assert _periods(factory)[1:] == [(date(2026, 10, 1), date(2026, 11, 2))]
    fetch(_req(date(2026, 9, 1), date(2026, 11, 2)))  # here the cached days in between are not requested again
    assert _periods(factory)[2:] == [(date(2026, 9, 1), date(2026, 9, 30)), (date(2026, 10, 24), date(2026, 11, 2))]


def test_series_with_different_histories(setup):
    fetch, factory, _ = setup
    fetch(_req(date(2025, 1, 1), date(2026, 10, 31), codes=["EPIAS.PTF"]))
    fetch(_req(date(2026, 1, 1), date(2026, 10, 31), codes=["EPIAS.SMF"]))
    fetch(_req(date(2025, 6, 1), date(2026, 10, 31), codes=["EPIAS.PTF", "EPIAS.SMF"]))
    assert _periods(factory)[2:] == [(date(2025, 6, 1), date(2025, 12, 31)), (date(2026, 10, 24), date(2026, 10, 31))]


@pytest.mark.parametrize("kw", [{"refresh": True}])
def test_refresh_fetches_the_whole_range(setup, kw):
    fetch, factory, _ = setup
    fetch(_req(date(2024, 1, 1), date(2026, 10, 31)))
    summary = fetch.summarize(_req(date(2024, 1, 1), date(2026, 10, 31)), **kw)
    assert _periods(factory)[1] == (date(2024, 1, 1), date(2026, 10, 31))
    assert summary.fetched == [("2024-01-01", "2026-10-31")]


def test_plan_is_off_without_policy_storage_or_for_transformed_requests(catalog):
    store, factory = InMemoryObservationStore(), Factory()
    plain = FetchSeries(Catalog(catalog, history=HistoryPolicy()), factory, store)
    plain(_req(date(2026, 1, 1), date(2026, 10, 31)))
    assert plain.plan(_req(date(2026, 1, 1), date(2026, 10, 31))) is None
    no_store = FetchSeries(Catalog(catalog, storage=StoragePolicy(observations=False)), factory, store)
    assert no_store.plan(_req(date(2026, 1, 1), date(2026, 10, 31))) is None
    incremental = FetchSeries(Catalog(catalog), factory, store)
    assert incremental.plan(_req(date(2026, 1, 1), date(2026, 10, 31), formula=1)) is None
    assert incremental.plan(_req(date(2026, 1, 1), date(2026, 10, 31))) == [
        DateRange(date(2026, 10, 24), date(2026, 10, 31))
    ]


def test_empty_store_after_an_empty_answer(setup):
    fetch, factory, _ = setup
    df = fetch(_req(date(2027, 1, 1), date(2027, 1, 31)))  # nothing published yet
    assert df.empty and len(factory.calls) == 1
    assert fetch.summarize(_req(date(2027, 1, 1), date(2027, 1, 31))).rows == 0
