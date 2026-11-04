from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from fakes import PLAYERS, BrandWorld, StubPanel
from mlbooster.domain.errors import InvalidInput, NotFound


@pytest.fixture
def world(catalog):
    return BrandWorld(catalog)


# ------------------------------------------------------------------------------------------- directory


def test_directory_reads_a_source_once_and_then_serves_from_the_store(world):
    scan = world.directory.scan()
    assert {r.source for r in scan.records} == set(PLAYERS) and not scan.skipped
    first = len(world.factory.calls)
    assert sorted(c.source_id for c in world.factory.calls) == sorted(PLAYERS)  # one pass per source
    world.directory.scan()
    assert len(world.factory.calls) == first


def test_directory_reads_every_index_series_of_a_source(world):
    world.directory.scan()
    calls = {c.source_id: set(c.series_ids) for c in world.factory.calls}
    assert calls["epdk"] == {"EPDK.PETROL.TOPLAM", "EPDK.LPG.TOPLAM"}
    assert calls["odmd"] == {"ODMD.TOPLAM"}


def test_directory_drops_totals_and_residual_rows(world):
    names = {r.name for r in world.directory.scan().records}
    assert "Diğer" not in names and "Toplam" not in names and "TOGG" in names


def test_directory_refreshes_an_old_list_and_keeps_it_when_the_source_is_down(catalog):
    world = BrandWorld(catalog)
    world.directory.scan()
    world.store.frames = [f.assign(vintage_ts=datetime.now(UTC) - timedelta(days=40)) for f in world.store.frames]
    calls = len(world.factory.calls)
    world.factory.down = {"odmd"}
    scan = world.directory.scan()
    assert len(world.factory.calls) > calls
    assert "TOGG" in {r.name for r in scan.records} and "odmd" not in scan.skipped


def test_refresh_keeps_the_stored_list_when_the_source_is_down(catalog):
    world = BrandWorld(catalog)
    world.directory.scan()
    world.factory.down = {"odmd"}
    assert "TOGG" in {r.name for r in world.directory.scan(refresh=True).records}


def test_source_that_cannot_be_read_is_reported(catalog):
    world = BrandWorld(catalog, down={"tbb"})
    scan = world.directory.scan()
    assert scan.skipped == {"tbb": "tbb yanıt vermedi"}
    assert "tbb" not in {r.source for r in scan.records}


# ------------------------------------------------------------------------------------------ resolution


def test_resolve_reports_the_brand_with_others(world):
    out = world.resolve("Anadolu Sigorta")
    assert out["status"] == "resolved" and out["matches"][0]["id"] == "tsb:1005"
    assert out["matches"][0]["sector"] == "sigorta"
    assert [m["id"] for m in out["others"]] == ["tsb:3004"]


def test_resolve_returns_candidates_for_an_unclear_name(world):
    out = world.resolve("Anadolu")
    assert out["status"] == "ambiguous" and "aday seçin" in out["message"]
    assert {m["id"] for m in out["matches"]} == {"tsb:1005", "tsb:3004"}


def test_not_found_says_what_is_available_and_gives_no_share(world):
    out = world.resolve("Migros")
    assert out["status"] == "not_found" and "pazar payı hesaplanmadı" in out["message"]
    assert out["sector_candidates"][0]["sector"] == "perakende"
    kinds = [a["kind"] for a in out["available"]]
    assert kinds[:3] == ["web_evidence", "sector_panel", "share_source"] and "google_trends" in kinds and "user_table" in kinds
    proxy = next(a for a in out["available"] if a["kind"] == "share_source")
    assert proxy["source"] == "bkm" and proxy["is_proxy"] is True
    trends = next(a for a in out["available"] if a["kind"] == "google_trends")
    assert trends["series"] == "BQT.ILGI.MIGROS" and trends["listed"] is None and trends["is_proxy"] is True
    assert not ({"share", "position", "panel"} & set(out))


def test_not_found_carries_the_catalog_note_and_unread_sources(catalog):
    world = BrandWorld(catalog, down={"tbb"})
    message = world.resolve("Kuveyt Türk")["message"]
    assert "katılım bankalarını içermiyor" in message and "tbb" in message and "kesin değil" in message


def test_not_found_for_an_unknown_name_uses_keywords(world):
    out = world.resolve("Yılmaz Market")
    assert [s["sector"] for s in out["sector_candidates"]] == ["perakende"]
    assert world.resolve("Xyzw Qwer")["sector_candidates"] == []


def test_trends_check_is_opt_in_and_reports_a_failure_without_raising(catalog):
    world = BrandWorld(catalog, down={"bq_trends"})
    out = world.resolve("Migros", check_trends=True)
    trends = next(a for a in out["available"] if a["kind"] == "google_trends")
    assert trends["listed"] is None and "Kontrol edilemedi" in trends["message"]


def test_trends_check_says_whether_the_term_is_listed(catalog, monkeypatch):
    world = BrandWorld(catalog)
    calls = []

    def fetch(request):
        calls.append(request)
        return pd.DataFrame({"value": [1.0]})

    monkeypatch.setattr(world.resolve, "_fetch", fetch)
    out = world.resolve("Migros", check_trends=True)
    assert next(a for a in out["available"] if a["kind"] == "google_trends")["listed"] is True
    assert calls[0].source_id == "bq_trends" and calls[0].series_ids == ("BQT.ILGI.MIGROS",)


def test_resolve_validates_the_text(world):
    with pytest.raises(InvalidInput, match="Marka adı"):
        world.resolve("x")


# -------------------------------------------------------------------------------------------- analysis


def test_analysis_of_a_car_brand(world):
    out = world.analyze("Togg", start=date(2022, 1, 1), end=date(2025, 12, 31))
    assert out["status"] == "resolved" and out["brand"]["id"] == "odmd:TOGG"
    assert out["sector"]["id"] == "otomotiv"
    assert out["position"]["series"] == "ODMD.TOPLAM" and out["position"]["rank"] == 3 and out["position"]["players"] == 3
    assert out["position"]["trend"] == "gaining" and out["position"]["d_year_pp"] > 0
    assert out["concentration"]["hhi"] > 0 and out["concentration"]["class"] in {"low", "moderate", "high"}
    assert [s["series"] for s in out["series"]] == ["ODMD.TOPLAM", "ODMD.OTOMOBIL", "ODMD.OTOMOBIL.YERLI", "ODMD.OTOMOBIL.ITHAL", "ODMD.TOPLAM.YERLI"]
    assert out["skipped_series"] == {"ODMD.HAFIF_TICARI": "Oyuncu bu seride yer almıyor"}
    assert out["headline"].startswith("TOGG, Otomotiv sektöründe ODMD.TOPLAM serisinde Aralık 2025")
    assert out["period"]["last"] == "2025-12-31"


def test_analysis_fetches_the_series_of_the_source_in_one_pass(world):
    world.analyze("Togg", start=date(2022, 1, 1), end=date(2025, 12, 31))
    full = [c for c in world.factory.calls if c.source_id == "odmd" and c.period.start == date(2022, 1, 1)]
    assert len(full) == 1 and len(full[0].series_ids) == 6


def test_every_competitor_has_a_reason_and_the_brand_itself_is_not_listed(world):
    out = world.analyze("Togg", start=date(2022, 1, 1), end=date(2025, 12, 31))
    assert out["competitors"] and all(c["reasons"] for c in out["competitors"])
    assert {c["entity"] for c in out["competitors"]} <= {"RENAULT", "Fiat"}


def test_segment_series_give_the_mix_and_the_shift_share(world):
    out = world.analyze("Togg", start=date(2022, 1, 1), end=date(2025, 12, 31))
    assert {s["segment"] for s in out["segments"]["segments"]} == {"ODMD.OTOMOBIL", "ODMD.HAFIF_TICARI"}
    assert "shift_share" in out["segments"]


def test_report_carries_attribution_and_licence(world):
    source = world.analyze("Togg", start=date(2022, 1, 1), end=date(2025, 12, 31))["sources"][0]
    assert source["attribution"].startswith("Kaynak: Otomotiv Distribütörleri") and source["commercial"] is False
    assert source["redistribution"] is False and "ODMD" in source["license"]


def test_proxy_data_stays_marked_proxy(catalog):
    world = BrandWorld(catalog, proxy=True)
    out = world.analyze("Togg", start=date(2022, 1, 1), end=date(2025, 12, 31))
    assert out["is_proxy"] is True and out["sources"][0]["is_proxy"] is True and out["position"]["is_proxy"] is True
    assert any(i["check"] == "proxy" for i in out["issues"])
    assert world.features("Togg", start=date(2022, 1, 1), end=date(2025, 12, 31)).is_proxy is True


def test_percent_series_are_shares_and_siblings_are_one_brand(world):
    out = world.analyze("Turkcell", start=date(2023, 1, 1), end=date(2025, 12, 31))
    by_series = {s["series"]: s for s in out["series"]}
    assert by_series["BTK.MOBIL.ABONE.PAY"]["focus"] == "Turkcell" and by_series["BTK.MOBIL.ABONE.PAY"]["measure"] == "share"
    assert by_series["BTK.MOBIL.ABONE.PAY"]["growth_yoy_pct"] is None
    assert by_series["BTK.SGB.ABONE.PAY"]["focus"] == "Superonline"
    assert by_series["BTK.GELIR"]["measure"] == "value"
    names = {c["entity"] for c in out["competitors"]}
    assert "Superonline" not in names and "Diğer" not in names


def test_competitor_names_use_the_catalog_brand(world):
    out = world.analyze("Vodafone", start=date(2023, 1, 1), end=date(2025, 12, 31))
    assert "Türk Telekom" in {c["entity"] for c in out["competitors"]}  # TT Mobil belongs to the wider brand


def test_default_history_is_shorter_for_report_files(world):
    out = world.analyze("Turkcell")
    assert out["period"]["requested_start"] == f"{date.today().year - 3}-01-01"
    assert world.analyze("Togg")["period"]["requested_start"] == f"{date.today().year - 5}-01-01"


def test_ambiguous_text_gives_candidates_not_a_share(world):
    out = world.analyze("Anadolu")
    assert out["status"] == "ambiguous" and "position" not in out and len(out["matches"]) == 2


def test_not_found_text_gives_limits_not_a_share(world):
    out = world.analyze("Migros")
    assert out["status"] == "not_found" and "position" not in out and "competitors" not in out


def test_choice_continues_from_a_candidate(world):
    out = world.analyze("Anadolu", choice="tsb:3004", start=date(2022, 1, 1), end=date(2025, 12, 31))
    assert out["status"] == "resolved" and out["brand"]["entity_ids"] == ["3004"]
    assert out["brand"]["name"] == "Anadolu Hayat Emeklilik"


def test_bad_choice_is_refused(world):
    with pytest.raises(InvalidInput, match="aday"):
        world.analyze("x y", choice="../etc")
    with pytest.raises(NotFound, match="bulunamadı"):
        world.analyze("Anadolu", choice="tsb:9999")


def test_brand_without_rows_in_the_period_reports_no_data(world):
    out = world.analyze("Togg", start=date(2000, 1, 1), end=date(2000, 12, 31))
    assert out["status"] == "no_data" and "alınamadı" in out["message"] and "position" not in out


def test_brand_without_volume_in_a_series_is_skipped(catalog):
    world = BrandWorld(catalog)
    world.directory.scan()
    frames = []
    world.tables.prefetch("odmd", ["ODMD.TOPLAM", "ODMD.OTOMOBIL"], date(2022, 1, 1), date(2025, 12, 31))
    for frame in world.store.frames:
        zero = (frame["series_id"] == "ODMD.OTOMOBIL") & (frame["entity_id"] == "TOGG")
        frames.append(frame.assign(value=frame["value"].where(~zero, 0.0)))
    world.store.frames = frames
    out = world.analyze("Togg", start=date(2022, 1, 1), end=date(2025, 12, 31))
    assert "ODMD.OTOMOBIL" not in {s["series"] for s in out["series"]}
    assert "hacmi yok" in out["skipped_series"]["ODMD.OTOMOBIL"]


def test_fetch_failure_falls_back_to_cached_series(catalog):
    world = BrandWorld(catalog)
    world.analyze("Togg", start=date(2022, 1, 1), end=date(2025, 12, 31))
    world.factory.down = {"odmd"}
    out = world.analyze("Togg", start=date(2020, 1, 1), end=date(2025, 12, 31))  # asks for more history than is cached
    assert out["status"] == "resolved" and "fetch" in out["skipped_series"]
    assert any(i["check"] == "source_unavailable" for i in out["issues"])


# -------------------------------------------------------------------------------------------- features


def _macro():
    idx = pd.date_range("2021-01-31", "2025-12-31", freq="ME")
    return pd.DataFrame({"tufe_yoy": [float(i % 7) for i in range(len(idx))]}, index=idx)


def test_features_join_brand_metrics_with_the_sector_panel(catalog):
    world = BrandWorld(catalog, panel=StubPanel(_macro(), {"kredi": "seri kodu bekleniyor"}))
    out = world.features("Togg", start=date(2022, 1, 1), end=date(2025, 12, 31))
    panel = out.panel
    assert {"marka_pay", "marka_pay_degisim", "marka_sira", "lider_farki", "goreli_buyume", "rakip_pay", "tufe_yoy"} <= set(panel.columns)
    assert {"marka_pay__chg3", "marka_pay__mean12", "tufe_yoy__l3", "is_gunu"} <= set(panel.columns)
    assert "marka_pay_otomobil" in panel.columns and "marka_pay_otomobil__l3" not in panel.columns
    assert world.panel.calls == [("otomotiv", date(2022, 1, 1), False)]
    assert out.skipped["kredi"] == "seri kodu bekleniyor" and out.skipped["ODMD.HAFIF_TICARI"]
    assert out.notes and out.sources[0]["id"] == "odmd"
    specs = {s.name: s for s in out.specs}
    assert specs["marka_sira"].family == "brand" and specs["marka_pay_otomobil"].family == "base"


def test_features_are_point_in_time_and_saved_as_a_panel(catalog):
    world = BrandWorld(catalog)
    out = world.features("Togg", start=date(2022, 1, 1), end=date(2025, 12, 31))
    assert out.panel.index[0] == pd.Timestamp("2023-01-31")  # 12-month basis to Dec 2022, published in January
    assert list(out.panel.index) == list(pd.date_range(out.panel.index[0], out.panel.index[-1], freq="ME"))
    stem = next(iter(world.panels.saved))
    assert stem.startswith("marka_togg_") and out.location == f"memory://{stem}"


def test_quarterly_brand_features_follow_the_publication_lag(world):
    out = world.features("Garanti", start=date(2023, 1, 1), end=date(2025, 12, 31))
    assert out.panel.index[0] == pd.Timestamp("2023-07-31")  # March figures: 120 days later
    assert out.panel["marka_pay"].notna().all()


def test_features_can_stay_in_memory(world):
    out = world.features("Togg", start=date(2022, 1, 1), end=date(2025, 12, 31), save=False)
    assert out.location is None and not world.panels.saved


def test_columns_from_a_source_that_forbids_storing_are_not_saved(catalog):
    held = next(f.name for f in catalog.sector("bankacilik").macro_features if f.source == "bkm")
    macro = _macro().rename(columns={"tufe_yoy": held})
    world = BrandWorld(catalog, panel=StubPanel(macro))
    out = world.features("Garanti", start=date(2022, 1, 1), end=date(2025, 12, 31))
    saved = next(iter(world.panels.saved.values()))
    assert held in out.panel.columns and held in out.withheld and f"{held}__l3" in out.withheld
    assert held not in saved.columns and f"{held}__l3" not in saved.columns and "marka_pay" in saved.columns


def test_features_return_the_resolution_for_an_unclear_name(world):
    assert world.features("Anadolu")["status"] == "ambiguous"
    assert world.features("Migros")["status"] == "not_found"


def test_features_refuse_a_brand_without_data(world):
    with pytest.raises(InvalidInput, match="feature üretilecek"):
        world.features("Togg", start=date(2000, 1, 1), end=date(2000, 12, 31))


# ---------------------------------------------------------------------------- share table helpers


def test_labels_give_the_table_label_of_an_entity(world):
    world.tables.prefetch("tbb", ["TBB.AKTIF"], date(2024, 1, 1), date(2025, 12, 31))
    assert world.tables.labels("tbb", "TBB.AKTIF", date(2024, 1, 1), date(2025, 12, 31))["8"] == "Akbank T.A.Ş."
    assert world.tables.labels("tbb", "TBB.KREDI", date(2024, 1, 1), date(2025, 12, 31)) == {}


def test_prefetch_skips_cached_series(world):
    world.tables.prefetch("tbb", ["TBB.AKTIF"], date(2024, 1, 1), date(2025, 12, 31))
    world.tables.prefetch("tbb", ["TBB.AKTIF", "TBB.KREDI"], date(2024, 1, 1), date(2025, 12, 31))
    assert [c.series_ids for c in world.factory.calls] == [("TBB.AKTIF",), ("TBB.KREDI",)]
