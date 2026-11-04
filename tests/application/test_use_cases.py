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
    "TP.TUKFIY2025.GENEL": pd.Series([100.0 + i for i in range(len(MONTHS))], index=MONTHS),
    "TP.DK.USD.A": pd.Series([30 + i / 100 for i in range(len(DAYS))], index=DAYS),
}


def _known_calls(providers) -> int:
    """Calls made for series that have data (series without data are not cached and are retried every time)."""
    return sum(1 for p in providers.created for r in p.calls if r.series_ids[0] in SERIES)


@pytest.fixture
def deps(catalog_repo):
    return catalog_repo, FakeProviderFactory(SERIES), InMemoryObservationStore(), InMemoryPanelStore()


def test_build_panel_point_in_time_and_caching(deps):
    catalog, providers, store, panels = deps
    build = BuildPanel(catalog, providers, store, panels)
    result = build("telekom", date(2024, 1, 1), date(2026, 9, 30))

    assert set(result.panel.columns) == {"tufe_yoy", "usdtry_logdiff"}
    assert "haberlesme_tufe" in result.skipped  # no TÜİK provider
    # CPI (TÜFE) has a 3-day lag: the January 2024 value is in the February row; the first YoY is in February 2025
    # (the fake data starts in 2024; in reality buffer history is fetched, so the start of the panel is populated too).
    assert result.panel["tufe_yoy"].dropna().index.min() == pd.Timestamp("2025-02-28")
    assert result.location == "memory://telekom"
    assert all(p.closed for p in providers.created)

    calls = _known_calls(providers)
    build("telekom", date(2024, 1, 1), date(2026, 9, 30))
    assert _known_calls(providers) == calls  # second run comes from the store


def test_build_panel_validates_sector(deps):
    build = BuildPanel(*deps)
    with pytest.raises(InvalidInput):
        build("../etc")
    with pytest.raises(NotFound):
        build("space")


def test_fetch_series_writes_and_summarizes(deps):
    catalog, providers, store, _ = deps
    summary = FetchSeries(catalog, providers, store).summarize(
        FetchRequest.create("evds3", ["TP.TUKFIY2025.GENEL"], date(2024, 1, 1), date(2026, 9, 30)), max_rows=10_000
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
    assert statuses["usdtry_logdiff"] == "ready" and statuses["tuketici_guveni"] == "pending_discovery"


@pytest.mark.parametrize(
    "kwargs",
    [{"freq": "X"}, {"agg": "median"}, {"formula": 9}, {"series_ids": ["TP&x=1"]}, {"source_id": "EVDS3"}],
)
def test_fetch_request_validation(kwargs):
    base = {"source_id": "evds3", "series_ids": ["TP.TUKFIY2025.GENEL"], "start": date(2024, 1, 1)}
    with pytest.raises(InvalidInput):
        FetchRequest.create(**(base | kwargs))


def test_build_panel_refetches_when_cache_misses_requested_start(deps):
    catalog, providers, store, panels = deps
    build = BuildPanel(catalog, providers, store, panels)
    build("telekom", date(2025, 6, 1), date(2026, 9, 30))
    narrow_calls = _known_calls(providers)
    # The data the previous call wrote to the store starts in 2024 (the fake provider returns the full series);
    # so a 2024 start is served from the cache, while a 2015 start triggers a refetch.
    build("telekom", date(2024, 1, 1), date(2026, 9, 30))
    assert _known_calls(providers) == narrow_calls
    build("telekom", date(2015, 1, 1), date(2026, 9, 30))
    wide_calls = _known_calls(providers)
    assert wide_calls > narrow_calls


def test_series_starting_after_requested_start_is_not_refetched(deps):
    """A series that starts in 2024 at the source is read from the cache once it was requested from 2015."""
    catalog, providers, store, panels = deps
    build = BuildPanel(catalog, providers, store, panels)
    build("telekom", date(2015, 1, 1), date(2026, 9, 30))
    calls = _known_calls(providers)
    assert store.requested_start("evds3", "TP.DK.USD.A", "level") <= date(2015, 1, 1)
    build("telekom", date(2015, 1, 1), date(2026, 9, 30))
    build("telekom", date(2018, 6, 1), date(2026, 9, 30))
    assert _known_calls(providers) == calls
    build("telekom", date(2010, 1, 1), date(2026, 9, 30))  # an earlier start was never requested
    assert _known_calls(providers) > calls


def test_cached_rows_of_other_frequency_are_ignored(deps):
    catalog, providers, store, panels = deps
    quarterly = FetchRequest.create("evds3", ["TP.TUKFIY2025.GENEL"], date(2024, 1, 1), date(2026, 9, 30))
    df = providers.create(catalog.load().source("evds3")).fetch(quarterly)
    store.write(df.assign(freq="Q", value=999.0))  # cached copy at a different frequency
    result = BuildPanel(catalog, providers, store, panels)("telekom", date(2024, 1, 1), date(2026, 9, 30))
    assert result.panel["tufe_yoy"].abs().max() < 100  # YoY would be extreme if the 999 values had been mixed in


def test_health_reports_provider_construction_errors(catalog_repo):
    from mlbooster.application.errors import ProviderError

    class Broken(FakeProviderFactory):
        def create(self, source):
            raise ProviderError("host pinning")

    results = {r.source_id: r for r in RunHealthChecks(catalog_repo, Broken({}))()}
    assert results["evds3"].status is HealthStatus.FAIL and "host" in results["evds3"].detail
    assert len(results) == len(catalog_repo.load().sources)


@pytest.mark.parametrize("formula", [1.0, True, "1"])
def test_formula_must_be_int(formula):
    with pytest.raises(InvalidInput):
        FetchRequest.create("evds3", ["TP.TUKFIY2025.GENEL"], date(2024, 1, 1), formula=formula)


def test_set_feature_series_writes_override_and_reloads(tmp_path, deps):
    from mlbooster.application.use_cases import SetFeatureSeries
    from mlbooster.infrastructure.catalog_yaml import YamlCatalogRepository
    from mlbooster.infrastructure.settings import PACKAGE_DIR

    _, providers, *_ = deps
    overrides = tmp_path / "catalog" / "overrides.yaml"
    repo = YamlCatalogRepository(PACKAGE_DIR / "catalog_data", overrides)
    providers.series["TP.KREDI.X"] = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-08-31", "2026-09-30"]))

    binding = SetFeatureSeries(repo, providers, repo)("bankacilik", "kredi_faizi", "TP.KREDI.X", kind="level")
    assert binding.seed.publication_lag_days == 30  # conservative default for monthly
    assert overrides.exists() and oct(overrides.stat().st_mode)[-3:] == "600"
    providers.series["TP.TG2.Y01"] = pd.Series([90.0, 91.0], index=pd.to_datetime(["2026-08-31", "2026-09-30"]))
    SetFeatureSeries(repo, providers, repo)("otomotiv", "tuketici_guveni", "TP.TG2.Y01")  # second write
    reloaded = YamlCatalogRepository(PACKAGE_DIR / "catalog_data", overrides).load()
    assert next(f for f in reloaded.sector("otomotiv").macro_features if f.name == "tuketici_guveni").series == "TP.TG2.Y01"
    feature = next(f for f in reloaded.sector("bankacilik").macro_features if f.name == "kredi_faizi")
    assert feature.series == "TP.KREDI.X"
    assert reloaded.source("evds3").seed("TP.KREDI.X").publication_lag_days == 30


def test_set_feature_rejects_unknown_feature_and_stale_series(deps):
    from mlbooster.application.errors import ProviderError
    from mlbooster.application.use_cases import SetFeatureSeries

    catalog, providers, *_ = deps
    with pytest.raises(InvalidInput, match="feature"):
        SetFeatureSeries(catalog, providers, None)("bankacilik", "no_such_feature", "TP.TUKFIY2025.GENEL")
    providers.series["TP.TUKFIY2025.GENEL"] = pd.Series([1.0], index=pd.to_datetime(["2025-01-31"]))
    with pytest.raises(ProviderError, match="güncelliğini yitirmiş"):
        SetFeatureSeries(catalog, providers, None)("bankacilik", "kredi_faizi", "TP.TUKFIY2025.GENEL")


def test_audit_use_case(deps):
    from mlbooster.application.use_cases import AuditPanel

    build = BuildPanel(*deps)
    report, result = AuditPanel(build)("telekom", date(2024, 1, 1), date(2026, 9, 30))
    assert report.months == len(result.panel)


def test_set_feature_create_adds_new_feature_and_unknown_override_fails(tmp_path, deps):
    from mlbooster.application.use_cases import SetFeatureSeries
    from mlbooster.domain.errors import InconsistentCatalog
    from mlbooster.infrastructure.catalog_yaml import YamlCatalogRepository
    from mlbooster.infrastructure.settings import PACKAGE_DIR

    _, providers, *_ = deps
    overrides = tmp_path / "overrides.yaml"
    repo = YamlCatalogRepository(PACKAGE_DIR / "catalog_data", overrides)
    providers.series["TP.URETIM.X"] = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-08-31", "2026-09-30"]))
    with pytest.raises(InvalidInput, match="create"):
        SetFeatureSeries(repo, providers, repo)("otomotiv", "binek_uretim", "TP.URETIM.X")
    SetFeatureSeries(repo, providers, repo)(
        "otomotiv", "binek_uretim", "TP.URETIM.X", create=True, transform="yoy_pct", kind="flow"
    )
    feature = next(f for f in YamlCatalogRepository(PACKAGE_DIR / "catalog_data", overrides).load().sector("otomotiv").macro_features if f.name == "binek_uretim")
    assert feature.series == "TP.URETIM.X" and feature.transform.value == "yoy_pct"

    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\nfeatures:\n  bankacilik:\n    no_such_feature: TP.X\n")
    with pytest.raises(InconsistentCatalog, match="no_such_feature"):
        YamlCatalogRepository(PACKAGE_DIR / "catalog_data", bad).load()


def test_stale_cache_triggers_refetch(deps):
    from datetime import UTC, datetime, timedelta

    from mlbooster.application.use_cases.panel import _is_stale

    catalog, providers, store, panels = deps
    build = BuildPanel(catalog, providers, store, panels)
    build("telekom", date(2024, 1, 1), date(2026, 9, 30))
    calls = _known_calls(providers)
    for frame in store.frames:  # age the cache by 4 days: the daily (12h) and monthly (72h) TTLs are exceeded
        frame["vintage_ts"] = datetime.now(UTC) - timedelta(days=4)
    build("telekom", date(2024, 1, 1), date(2026, 9, 30))
    assert _known_calls(providers) > calls
    fresh = store.frames[-1]
    assert not _is_stale(fresh) and _is_stale(fresh, datetime.now(UTC) + timedelta(days=4))
