import pandas as pd
import pytest

from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import Frequency
from mlbooster.domain.services.brand_features import brand_metrics, monthly_grid, panel_id, point_in_time
from mlbooster.domain.services.brand_report import headline, merge_competitors, merged_issues, movers, series_summary
from mlbooster.domain.services.market_share import ShareConfig, analyze
from mlbooster.domain.validation import slug


def _table(months=30, freq="ME"):
    dates = pd.date_range("2023-01-31", periods=months, freq=freq)
    rows = []
    for i, d in enumerate(dates):
        parts = {"Lider": 50 - i * 0.2, "Marka": 30 + i * 0.3, "Üçüncü": 15 - i * 0.1, "Diğer": 5, "Eski": 0.0}
        rows += [(name, d.date().isoformat(), value) for name, value in parts.items()]
        rows.append(("TOPLAM", d.date().isoformat(), sum(parts.values())))
    return pd.DataFrame(rows, columns=["entity", "date", "value"])


@pytest.fixture
def result():
    return analyze(_table(), ShareConfig(focus="Marka", kind="stock", unit="count"))


def test_series_summary_counts_active_real_players(result):
    summary = series_summary(result, "X.SERI", "Seri")
    assert summary["rank"] == 2 and summary["players"] == 3  # Diğer and the inactive player do not count
    assert summary["leader"] == "Lider" and summary["next_above"] == "Lider" and summary["next_below"] == "Üçüncü"
    assert summary["gap_to_leader_pp"] == pytest.approx(summary["leader_share"] - summary["share"])
    assert summary["trend"] == "gaining" and summary["d_year_pp"] > 0
    assert set(summary["concentration"]) >= {"hhi", "cr3", "cr5", "class", "d_hhi_year"}


def test_series_summary_leaves_siblings_out_of_the_ranking(result):
    assert series_summary(result, "X", "Seri", {"Lider"})["rank"] == 1


def test_leader_has_no_gap_above():
    summary = series_summary(analyze(_table(), ShareConfig(focus="Lider", kind="stock")), "X", "Seri")
    assert summary["rank"] == 1 and summary["next_above"] is None and summary["gap_to_next_pp"] is None


def test_movers_lists_gains_and_losses_around_the_brand(result):
    out = movers(result)
    assert [r["entity"] for r in out["losers"]] == ["Lider", "Üçüncü"] and out["gainers"] == []
    assert movers(result, display={"Lider": "Lider AŞ"})["losers"][0]["entity"] == "Lider AŞ"
    assert all(r["entity"] != "Diğer" for r in out["losers"] + out["gainers"])
    near = movers(result, around=0)
    assert near == {"gainers": [], "losers": []}


def test_competitors_rank_by_how_many_series_name_them_and_keep_their_reasons(result):
    other = analyze(_table(), ShareConfig(focus="Marka", kind="stock"))
    other["competitors"]["candidates"] = [{"entity": "Üçüncü", "share": 1, "r": None, "reasons": ["pay sıralamasında komşu"]}]
    merged = merge_competitors([("A", result), ("B", other)], display={"Üçüncü": "Üç AŞ"})
    assert merged[0]["entity"] == "Üç AŞ" and merged[0]["series"] == ["A", "B"]
    assert all(c["reasons"] for c in merged)
    assert {"series": "B", "reason": "pay sıralamasında komşu"} in merged[0]["reasons"]


def test_competitors_leave_out_siblings_residual_rows_and_inactive_players(result):
    result["competitors"]["candidates"] += [
        {"entity": "Diğer", "share": 5, "r": None, "reasons": ["x"]},
        {"entity": "Eski", "share": 0, "r": None, "reasons": ["x"]},
    ]
    assert {c["entity"] for c in merge_competitors([("A", result)], exclude={"Lider"})} == {"Üçüncü"}


def test_segment_overlap_reason_is_merged(result):
    segments = {"competitors": {"candidates": [
        {"entity": "Üçüncü", "reasons": ["segment karması en çok örtüşen oyunculardan (%80)", "pazar lideri"]},
    ]}}  # fmt: skip
    row = next(c for c in merge_competitors([("A", result)], segments) if c["entity"] == "Üçüncü")
    assert {"series": "segment", "reason": "segment karması en çok örtüşen oyunculardan (%80)"} in row["reasons"]
    assert sum(r["series"] == "segment" for r in row["reasons"]) == 1


def test_issues_are_listed_once_with_their_series():
    a = {"issues": [{"severity": "info", "check": "c", "message": "m"}]}
    assert merged_issues([("A", a), ("B", a)]) == [{"severity": "info", "check": "c", "message": "m", "series": ["A", "B"]}]


def test_headline_is_turkish_with_period_and_units():
    primary = {"basis": "ttm", "last": "2026-10-31", "series": "ODMD.TOPLAM", "share": 4.61, "players": 55, "rank": 10, "d_year_pp": 1.8}
    assert headline("Togg", "Otomotiv", primary) == (
        "Togg, Otomotiv sektöründe ODMD.TOPLAM serisinde Ekim 2026 itibarıyla son 12 ay bazında %4,6 pay ile 55 aktif "
        "oyuncu arasında 10. sırada. Payı bir yılda +1,8 puan değişti."
    )
    assert "bir yılda" not in headline("X", "Y", {**primary, "basis": "period", "d_year_pp": None})


def test_brand_metrics_use_only_the_period_itself():
    table = _table(months=30)
    full = brand_metrics(table, ShareConfig(focus="Marka", kind="stock"))
    cut = brand_metrics(table[table["date"] <= "2024-12-31"], ShareConfig(focus="Marka", kind="stock"))
    pd.testing.assert_frame_equal(full.loc[cut.index], cut)  # adding later periods changes no earlier row


def test_brand_metrics_definitions():
    metrics = brand_metrics(_table(), ShareConfig(focus="Marka", kind="stock"), exclude=("Üçüncü",))
    row = metrics.iloc[0]
    assert row["marka_pay"] == pytest.approx(30.0) and row["marka_sira"] == 2  # Üçüncü and Diğer are not rivals
    assert row["lider_farki"] == pytest.approx(20.0) and row["rakip_pay"] == pytest.approx(50.0)
    assert metrics["marka_pay_degisim"].iloc[1] == pytest.approx(metrics["marka_pay"].iloc[1] - 30.0)
    assert pd.isna(metrics["goreli_buyume"].iloc[11]) and metrics["goreli_buyume"].iloc[12] > 0


def test_rank_is_empty_before_the_brand_sells():
    table = _table()
    table.loc[(table["entity"] == "Marka") & (table["date"] < "2023-06-30"), "value"] = 0.0
    metrics = brand_metrics(table, ShareConfig(focus="Marka", kind="stock"))
    assert metrics["marka_sira"].iloc[:5].isna().all() and metrics["marka_sira"].iloc[5] > 0
    assert metrics["rakip_pay"].iloc[:5].isna().all()


def test_share_measure_has_no_growth_column():
    table = _table()
    table = table[(table["entity"] != "TOPLAM") & (table["entity"] != "Eski")]
    assert "goreli_buyume" not in brand_metrics(table, ShareConfig(focus="Marka", measure="share", kind="stock")).columns


def test_flow_series_use_the_twelve_month_basis():
    metrics = brand_metrics(_table(), ShareConfig(focus="Marka", kind="flow"))
    assert metrics.index[0] == pd.Timestamp("2023-12-31")


def test_point_in_time_moves_rows_to_the_publication_month():
    metrics = brand_metrics(_table(), ShareConfig(focus="Marka", kind="stock"))
    monthly = point_in_time(metrics, Frequency.MONTHLY, 45)
    assert monthly.index[0] == pd.Timestamp("2023-03-31")
    assert monthly["marka_pay"].iloc[0] == metrics["marka_pay"].iloc[0]


def test_point_in_time_holds_quarterly_figures_until_the_next_release():
    metrics = brand_metrics(_table(10, "QE"), ShareConfig(focus="Marka", kind="stock"))
    monthly = point_in_time(metrics, Frequency.QUARTERLY, 120)
    assert monthly.index[0] == pd.Timestamp("2023-07-31")  # March figures are published in July
    assert monthly["marka_pay"].iloc[1] == monthly["marka_pay"].iloc[0]
    assert monthly["marka_pay"].iloc[3] == metrics["marka_pay"].iloc[1]


def test_monthly_grid_starts_with_the_brand_and_needs_three_months():
    brand = pd.DataFrame({"a": [1.0, 2.0, 3.0]}, index=pd.date_range("2024-03-31", periods=3, freq="ME"))
    macro = pd.DataFrame({"b": range(12)}, index=pd.date_range("2024-01-31", periods=12, freq="ME"))
    grid = monthly_grid([brand, macro], pd.Timestamp("2024-01-01"), pd.Timestamp("2024-08-31"))
    assert grid[0] == pd.Timestamp("2024-03-31") and grid[-1] == pd.Timestamp("2024-08-31")
    with pytest.raises(InvalidInput, match="üç aylık"):
        monthly_grid([brand.iloc[:1], macro.iloc[:1]], pd.Timestamp("2024-01-01"), pd.Timestamp("2025-01-01"))


def test_panel_id_is_a_safe_slug_for_any_name():
    for name in ("Türkiye Cumhuriyeti Ziraat Bankası A.Ş.", "OMODA & JAECOO", "汉字", "7KITA"):
        value = panel_id(name, "tbb:3")
        assert slug(value, "sector") == value
    assert panel_id("X", "tbb:3") != panel_id("X", "tbb:4")
