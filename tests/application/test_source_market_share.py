from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from fakes import EntityFactory, InMemoryObservationStore, InMemoryPanelStore
from mlbooster.application.use_cases.market_share import AnalyzeMarketShare, SourceShareTable
from mlbooster.application.use_cases.panel import BuildPanel
from mlbooster.application.use_cases.series import FetchSeries
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Catalog, MacroFeature, SeriesKind, Transform
from mlbooster.domain.services.market_share import ShareConfig


class StaticCatalog:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def load(self):
        return self.catalog


@pytest.fixture
def setup(catalog):
    factory, store = EntityFactory(), InMemoryObservationStore()
    repo = StaticCatalog(catalog)
    fetch = FetchSeries(repo, factory, store)
    return SourceShareTable(repo, fetch, store), factory, store


def test_entity_rows_become_players_and_the_total_becomes_toplam(setup):
    tables, factory, _ = setup
    table, meta = tables("bddk", "BDDK.AKTIF", date(2024, 1, 1), date(2025, 12, 31))
    assert set(table["entity"]) == {"TOPLAM", "Mevduat-Kamu", "Mevduat-Yerli Özel", "Katılım"}
    assert len(table) == 24 * 4 and table["date"].iloc[0] == "2024-01-31"
    assert meta == {
        "source": "bddk", "series": "BDDK.AKTIF", "name": "Toplam aktifler, banka grubuna göre (milyon TL)",
        "unit": "million_try", "kind": "level", "is_proxy": False, "attribution": "Kaynak: BDDK — BDDK Aylık Bülten",
    }  # fmt: skip
    assert len(factory.calls) == 1


def test_cache_is_reused_and_a_stale_cache_is_topped_up(setup):
    tables, factory, store = setup
    tables("bddk", "BDDK.AKTIF", date(2020, 1, 1), date(2025, 12, 31))
    tables("bddk", "BDDK.AKTIF", date(2021, 1, 1), date(2025, 12, 31))
    assert len(factory.calls) == 1  # covered by the cache
    store.frames = [f.assign(vintage_ts=datetime.now(UTC) - timedelta(days=10)) for f in store.frames]
    tables("bddk", "BDDK.AKTIF", date(2020, 1, 1), date(2025, 12, 31))
    assert len(factory.calls) == 2
    assert factory.calls[-1].period.start == date(2024, 11, 26)  # about a year before the last cached month
    tables("bddk", "BDDK.AKTIF", date(2020, 1, 1), date(2025, 12, 31), refresh=True)
    assert factory.calls[-1].period.start == date(2020, 1, 1)


def test_rejects_unknown_series_series_without_entities_and_empty_periods(setup, catalog):
    tables, _, _ = setup
    with pytest.raises(InvalidInput, match="BDDK.YOK serisi yok"):
        tables("bddk", "BDDK.YOK", date(2024, 1, 1))
    with pytest.raises(InvalidInput, match="Geçersiz seri"):
        tables("bddk", "BDDK.AKTIF&x=1", date(2024, 1, 1))
    store = InMemoryObservationStore()
    no_groups = SourceShareTable(StaticCatalog(catalog), FetchSeries(StaticCatalog(catalog), EntityFactory(groups={}), store), store)
    with pytest.raises(InvalidInput, match="kırılımı içermiyor"):
        no_groups("bddk", "BDDK.AKTIF", date(2024, 1, 1), date(2024, 12, 31))
    with pytest.raises(InvalidInput, match="gözlem yok"):
        tables("bddk", "BDDK.AKTIF", date(2024, 1, 10), date(2024, 1, 20))


def test_entity_named_like_the_total_is_rejected(catalog):
    store = InMemoryObservationStore()
    factory = EntityFactory(groups={"10009": ("Toplam", 1.0), "10003": ("Katılım", 2.0)})
    tables = SourceShareTable(StaticCatalog(catalog), FetchSeries(StaticCatalog(catalog), factory, store), store)
    with pytest.raises(InvalidInput, match="çakışıyor"):
        tables("bddk", "BDDK.AKTIF", date(2024, 1, 1), date(2024, 12, 31))


def test_renamed_entity_keeps_its_latest_name(setup):
    tables, _, store = setup
    tables("bddk", "BDDK.AKTIF", date(2024, 1, 1), date(2024, 12, 31))
    old = store.frames[0]
    store.frames[0] = old.assign(entity_name=old["entity_name"].where(old["date"] > date(2024, 6, 30), old["entity_name"] + " (eski)"))
    table, _ = tables("bddk", "BDDK.AKTIF", date(2024, 1, 1), date(2024, 12, 31))
    assert set(table["entity"]) == {"TOPLAM", "Mevduat-Kamu", "Mevduat-Yerli Özel", "Katılım"}


def _tables_with(catalog, groups):
    store = InMemoryObservationStore()
    factory = EntityFactory(groups=groups)
    return SourceShareTable(StaticCatalog(catalog), FetchSeries(StaticCatalog(catalog), factory, store), store)


def test_small_negative_net_amounts_count_as_zero_and_large_ones_are_refused(catalog):
    small = {"10009": ("Mevduat-Kamu", 100.0), "10008": ("Mevduat-Yerli Özel", 99.0), "10003": ("Katılım", -0.2)}
    table, meta = _tables_with(catalog, small)("bddk", "BDDK.AKTIF", date(2024, 1, 1), date(2024, 12, 31))
    assert meta["negative_rows"] == 12 and (table["value"] >= 0).all()
    assert table.loc[table["entity"] == "Katılım", "value"].eq(0).all()
    large = {**small, "10003": ("Katılım", -5.0)}
    with pytest.raises(InvalidInput, match="negatif net tutarları"):
        _tables_with(catalog, large)("bddk", "BDDK.AKTIF", date(2024, 1, 1), date(2024, 12, 31))


def test_from_source_reports_zeroed_negative_amounts(catalog):
    small = {"10009": ("Mevduat-Kamu", 100.0), "10008": ("Mevduat-Yerli Özel", 99.0), "10003": ("Katılım", -0.2)}
    uc = AnalyzeMarketShare(StubPanel(), None, _tables_with(catalog, small))
    out = uc.from_source("bddk", "BDDK.AKTIF", ShareConfig(), date(2023, 1, 1), date(2024, 12, 31))
    assert any(i["check"] == "negative_values" and "24 satırda" in i["message"] for i in out["issues"])


class StubPanel:
    def __call__(self, sector_id, start, end, save=True):
        raise AssertionError("no sector given")


def test_from_source_uses_catalog_kind_unit_and_reports_source(setup):
    tables, _, _ = setup
    uc = AnalyzeMarketShare(StubPanel(), None, tables)
    out = uc.from_source("bddk", "BDDK.AKTIF", ShareConfig(focus="Katılım"), date(2023, 1, 1), date(2025, 12, 31))
    assert out["meta"]["kind"] == "stock" and out["meta"]["unit"] == "million_try" and out["meta"]["has_total"]
    assert out["meta"]["source"]["series"] == "BDDK.AKTIF" and out["meta"]["entities"] == 3
    assert out["market"]["coverage"][-1] == pytest.approx(100)
    katilim = next(p for p in out["players"] if p["entity"] == "Katılım")
    assert katilim["trend"] == "gaining" and out["competitors"]["focus"] == "Katılım"
    assert not any(i["check"] == "proxy" for i in out["issues"])
    flows = uc.from_source("bddk", "BDDK.AKTIF", ShareConfig(unit="TL"), date(2023, 1, 1), date(2025, 12, 31), kind="flow")
    assert flows["meta"]["kind"] == "flow" and flows["meta"]["unit"] == "TL"


def test_from_source_flags_proxy_series_and_flow_kind(catalog):
    store = InMemoryObservationStore()
    source = catalog.source("bddk")
    flow_seed = replace(source.seed("BDDK.AKTIF"), kind=SeriesKind.FLOW)
    patched = replace(source, series=(flow_seed,))
    cat = StaticCatalog(Catalog(sources={**catalog.sources, "bddk": patched}, sectors=catalog.sectors))
    tables = SourceShareTable(cat, FetchSeries(cat, EntityFactory(proxy=True), store), store)
    out = AnalyzeMarketShare(StubPanel(), None, tables).from_source("bddk", "BDDK.AKTIF", ShareConfig(), date(2023, 1, 1), date(2025, 12, 31))
    assert out["meta"]["kind"] == "flow" and out["meta"]["source"]["is_proxy"] is True
    assert any(i["check"] == "proxy" and "proxy" in i["message"] for i in out["issues"])


def test_from_source_requires_wiring():
    with pytest.raises(InvalidInput, match="kullanılamıyor"):
        AnalyzeMarketShare(StubPanel(), None).from_source("bddk", "BDDK.AKTIF", ShareConfig(), date(2024, 1, 1))


def test_panel_uses_only_the_total_of_series_with_entities(catalog):
    sector = catalog.sector("bankacilik")
    feature = MacroFeature(name="sektor_aktif", source="bddk", series="BDDK.AKTIF", transform=Transform.LEVEL)
    cat = StaticCatalog(
        Catalog(sources=catalog.sources, sectors={"bankacilik": replace(sector, macro_features=(feature,), feature_recipes=())})
    )
    store = InMemoryObservationStore()
    result = BuildPanel(cat, EntityFactory(), store, InMemoryPanelStore())("bankacilik", date(2024, 1, 1), date(2024, 12, 31))
    assert not result.skipped
    obs = store.read("bddk", ["BDDK.AKTIF"])
    total = obs[obs["entity_id"] == ""].set_index(pd.to_datetime(obs.loc[obs["entity_id"] == "", "date"]))["value"]
    # A 30-day lag is a fixed two-month shift for monthly series (Jan 31 + 30 days falls in March).
    assert result.panel.loc["2024-12-31", "sektor_aktif"] == pytest.approx(total.loc["2024-10-31"])
    assert result.panel["sektor_aktif"].notna().all()


class DailyMixProvider:
    """EPİAŞ-like daily generation by source: dam hydro loses 3 points a year with a strong season (peak in May,
    trough at the end of October), natural gas and wind share the rest 60/40. The last day is 2025-12-31 + `extra`."""

    def __init__(self, source, calls, extra_days=0):
        self.source, self.calls, self.extra_days = source, calls, extra_days

    def fetch(self, request):
        self.calls.append(request)
        end = min(request.period.end, date(2025, 12, 31) + timedelta(days=self.extra_days))
        days = pd.date_range(request.period.start, end, freq="D")
        years = (days - pd.Timestamp("2023-01-01")).days / 365.25
        hydro = 30 - 3 * years + 8 * np.cos(2 * np.pi * (days.day_of_year - 121) / 365.25)
        rows = []
        for d, h in zip(days.date, hydro, strict=True):
            parts = {"dammedHydro": ("Barajlı hidroelektrik", 10 * h), "naturalGas": ("Doğal gaz", 6 * (100 - h)),
                     "wind": ("Rüzgâr", 4 * (100 - h))}  # fmt: skip
            rows.append(("", None, d, sum(v for _, v in parts.values())))
            rows += [(code, name, d, v) for code, (name, v) in parts.items()]
        df = pd.DataFrame(rows, columns=["entity_id", "entity_name", "date", "value"])
        return df.assign(
            source_id="epias", dataset_id="realtime-generation", series_id=request.series_ids[0], metric="level",
            freq="D", unit="mwh", currency=None, is_proxy=False, vintage_ts=datetime.now(UTC), publication_lag_days=1,
        )  # fmt: skip

    def close(self):
        pass


class DailyMixFactory:
    def __init__(self, **kwargs):
        self.calls, self.kwargs = [], kwargs

    def available(self):
        return {"epias"}

    def create(self, source):
        return DailyMixProvider(source, self.calls, **self.kwargs)


def _daily_tables(catalog, **kwargs):
    store, repo = InMemoryObservationStore(), StaticCatalog(catalog)
    return SourceShareTable(repo, FetchSeries(repo, DailyMixFactory(**kwargs), store), store)


def test_daily_series_are_aggregated_to_calendar_months(catalog):
    table, meta = _daily_tables(catalog, extra_days=2)("epias", "EPIAS.URETIM.KAYNAK", date(2023, 1, 1), date(2026, 1, 2))
    assert meta["aggregated_from"] == "D" and meta["kind"] == "flow"
    assert sorted(set(table["date"]))[-1] == "2025-12-31"  # the incomplete January 2026 is dropped
    assert len(set(table["date"])) == 36 and set(table["entity"]) == {"TOPLAM", "Barajlı hidroelektrik", "Doğal gaz", "Rüzgâr"}
    january = table[table["date"] == "2023-01-31"].set_index("entity")["value"]
    assert january["TOPLAM"] == pytest.approx(31 * 1000)  # flow: the month's daily values are summed
    assert january.drop("TOPLAM").sum() == pytest.approx(january["TOPLAM"])


def test_daily_mix_year_change_and_trend_agree(catalog):
    """Regression: on the daily table the analysis took 52 days for a year, so the 'yıllık' change of dam hydro was
    positive (autumn trough to winter) while its trend said 'pay kaybediyor'. Monthly periods give a 12-month change."""
    tables = _daily_tables(catalog)
    uc = AnalyzeMarketShare(StubPanel(), None, tables)
    out = uc.from_source("epias", "EPIAS.URETIM.KAYNAK", ShareConfig(), date(2023, 1, 1), date(2025, 12, 31))
    hydro = next(p for p in out["players"] if p["entity"] == "Barajlı hidroelektrik")
    assert hydro["trend"] == "losing" and hydro["d_year_pp"] == pytest.approx(-3, abs=0.3)
    for p in out["players"]:
        if p["trend"] in ("gaining", "losing"):
            assert (p["d_year_pp"] > 0) == (p["trend"] == "gaining"), p["entity"]
    assert any(i["check"] == "aggregated_to_monthly" for i in out["issues"])

    store_rows = tables._store.read("epias", ["EPIAS.URETIM.KAYNAK"])  # what the old code analysed: daily rows
    names = {"": "TOPLAM", "dammedHydro": "Barajlı hidroelektrik", "naturalGas": "Doğal gaz", "wind": "Rüzgâr"}
    daily = pd.DataFrame({"entity": store_rows["entity_id"].map(names), "date": pd.to_datetime(store_rows["date"]).dt.strftime("%Y-%m-%d"),
                          "value": store_rows["value"]})  # fmt: skip
    old = AnalyzeMarketShare(StubPanel(), None)(daily, ShareConfig(kind="flow"))
    old_hydro = next(p for p in old["players"] if p["entity"] == "Barajlı hidroelektrik")
    assert old_hydro["trend"] == "losing" and old_hydro["d_year_pp"] > 0  # the mismatch this change removes


def test_daily_series_without_a_complete_month(catalog):
    with pytest.raises(InvalidInput, match="tamamlanmış ay yok"):
        _daily_tables(catalog)("epias", "EPIAS.URETIM.KAYNAK", date(2025, 12, 10), date(2025, 12, 20))
