from datetime import date

import pandas as pd
import pytest

from fakes import FakeProviderFactory, InMemoryObservationStore, InMemoryPanelStore
from mlbooster.application.dto import FetchRequest
from mlbooster.application.errors import ProviderNotImplemented
from mlbooster.application.use_cases import BuildPanel, FetchSeries, GetSectorMap, RunHealthChecks
from mlbooster.domain.errors import InvalidInput, NotFound
from mlbooster.domain.model import HealthStatus

MONTHS = pd.date_range("2024-01-31", "2026-09-30", freq="ME")
DAYS = pd.bdate_range("2024-01-01", "2026-09-30")
SERIES = {
    "TP.FG.J0": pd.Series([100.0 + i for i in range(len(MONTHS))], index=MONTHS),
    "TP.DK.USD.A.YTL": pd.Series([30 + i / 100 for i in range(len(DAYS))], index=DAYS),
}


@pytest.fixture
def deps(catalog_repo):
    return catalog_repo, FakeProviderFactory(SERIES), InMemoryObservationStore(), InMemoryPanelStore()


def test_build_panel_point_in_time_and_caching(deps):
    catalog, providers, store, panels = deps
    build = BuildPanel(catalog, providers, store, panels)
    result = build("telekom", date(2024, 1, 1), date(2026, 9, 30))

    assert set(result.panel.columns) == {"tufe_yoy", "usdtry_logdiff"}
    assert "haberlesme_tufe" in result.skipped  # TÜİK sağlayıcısı yok
    # TÜFE 3 gün gecikmeli: Ocak 2024 değeri Şubat satırında; ilk YoY Şubat 2025'te.
    assert result.panel["tufe_yoy"].dropna().index.min() == pd.Timestamp("2025-02-28")
    assert result.location == "memory://telekom"
    assert all(p.closed for p in providers.created)

    calls = sum(len(p.calls) for p in providers.created)
    build("telekom", date(2024, 1, 1), date(2026, 9, 30))
    assert sum(len(p.calls) for p in providers.created) == calls  # ikinci çalışma depodan


def test_build_panel_validates_sector(deps):
    build = BuildPanel(*deps)
    with pytest.raises(InvalidInput):
        build("../etc")
    with pytest.raises(NotFound):
        build("uzay")


def test_fetch_series_writes_and_summarizes(deps):
    catalog, providers, store, _ = deps
    summary = FetchSeries(catalog, providers, store).summarize(
        FetchRequest.create("evds3", ["TP.FG.J0"], date(2024, 1, 1), date(2026, 9, 30)), max_rows=10_000
    )
    assert summary.rows == len(MONTHS) and len(summary.tail) == len(MONTHS)  # ≤ MAX_TAIL_ROWS
    assert "TCMB" in summary.attribution and store.frames


def test_unimplemented_provider_is_reported(deps):
    catalog, providers, store, _ = deps
    with pytest.raises(ProviderNotImplemented):
        FetchSeries(catalog, providers, store)(FetchRequest.create("tuik", ["X"], date(2024, 1, 1)))
    results = {r.source_id: r.status for r in RunHealthChecks(catalog, providers)()}
    assert results["evds3"] is HealthStatus.OK and results["tuik"] is HealthStatus.SKIPPED


def test_sector_map_statuses(deps):
    catalog, providers, *_ = deps
    m = GetSectorMap(catalog, providers)("otomotiv")
    statuses = {e.feature.name: e.status for e in m.features}
    assert statuses["usdtry_logdiff"] == "hazir" and statuses["tuketici_guveni"] == "kesif_bekliyor"


@pytest.mark.parametrize(
    "kwargs",
    [{"freq": "X"}, {"agg": "median"}, {"formula": 9}, {"series_ids": ["TP&x=1"]}, {"source_id": "EVDS3"}],
)
def test_fetch_request_validation(kwargs):
    base = {"source_id": "evds3", "series_ids": ["TP.FG.J0"], "start": date(2024, 1, 1)}
    with pytest.raises(InvalidInput):
        FetchRequest.create(**(base | kwargs))
