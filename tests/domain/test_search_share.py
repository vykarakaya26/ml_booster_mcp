"""Search-interest logic on synthetic lists and series."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from mlbooster.domain.services.search_share import (
    BRAND,
    COMPETITOR,
    FOUND,
    GENERIC,
    NOT_FOUND,
    RELATED,
    interest_summary,
    list_ranking,
    monthly_features,
    normalize,
    province_summary,
    refresh_plan,
    scale_check,
    term_report,
    term_set,
    weekly_series,
)


def listing(rows) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=["table", "refresh_date", "term", "rank"])
    return frame.assign(normalized=frame["term"].map(normalize))


LISTS = listing([
    ("top", date(2026, 10, 1), "Togg", 12),
    ("top", date(2026, 10, 2), "togg t6x", 3),
    ("rising", date(2026, 10, 2), "TOGG", 7),
    ("top", date(2026, 10, 3), "benzin fiyatları", 5),
    ("top", date(2026, 10, 3), "Migros'ta kampanya", 9),
    ("top", date(2026, 10, 3), "bimeks", 10),
])  # fmt: skip


def test_normalize_folds_turkish_and_punctuation():
    assert normalize("Türk Telekom'da İŞ") == "turk telekom da is"


def test_exact_term_is_found_and_longer_terms_are_only_related():
    report = term_report(term_set("Togg", BRAND, ["Togg"]), LISTS)
    assert report["status"] == FOUND and report["terms"] == ["TOGG", "Togg"] and report["related_terms"] == ["togg t6x"]
    assert report["list_days"] == 2 and report["top_days"] == 1 and report["rising_days"] == 1 and report["best_rank"] == 7
    assert report["first_day"] == "2026-10-01" and report["last_day"] == "2026-10-02"


def test_related_only_is_not_counted():
    report = term_report(term_set("Migros", BRAND, ["Migros"]), LISTS)
    assert report["status"] == RELATED and report["list_days"] == 0 and report["best_rank"] is None
    assert report["related_list_days"] == 1 and "sayılmadı" in report["note"]


def test_missing_term_is_reported_not_filled():
    report = term_report(term_set("Renault", COMPETITOR, ["Renault"]), LISTS)
    assert report["status"] == NOT_FOUND and report["list_days"] == 0 and "aranmadığı anlamına gelmez" in report["note"]


def test_brand_needs_whole_words_and_short_spellings_need_an_exact_term():
    assert term_report(term_set("BİM", BRAND, ["BİM"]), LISTS)["status"] == NOT_FOUND  # not "bimeks"
    short = listing([("top", date(2026, 10, 1), "tt mobil fatura", 1)])
    assert term_report(term_set("TT", BRAND, ["TT"]), short)["status"] == NOT_FOUND


def test_generic_term_matches_longer_last_words():
    report = term_report(term_set("benzin fiyat", GENERIC, ["benzin fiyat"]), LISTS)
    assert report["status"] == FOUND and report["terms"] == ["benzin fiyatları"]


def test_ranking_by_list_days_then_rank_with_ties_and_unranked():
    reports = [
        {"label": "A", "mode": BRAND, "list_days": 2, "best_rank": 9},
        {"label": "B", "mode": COMPETITOR, "list_days": 3, "best_rank": 20},
        {"label": "C", "mode": COMPETITOR, "list_days": 2, "best_rank": 9},
        {"label": "D", "mode": COMPETITOR, "list_days": 0, "best_rank": None},
    ]
    assert [(r["label"], r["rank"]) for r in list_ranking(reports)] == [("B", 1), ("A", 2), ("C", 2), ("D", None)]


def test_refresh_plan_reuses_days():
    d1, d2, d3 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)
    plan = refresh_plan({"a": {d1, d2}, "b": {d2}, "c": {d2, d3}, "d": {d1}})
    assert plan == {"a": d2, "b": d2, "c": d2, "d": d1}
    assert refresh_plan({"a": {d1, d3}}) == {"a": d3}  # the newest on a tie
    assert refresh_plan({}) == {}


def rows(term, values, region=""):
    weeks = pd.date_range("2024-01-07", periods=len(values), freq="7D").date
    return pd.DataFrame({"term": term, "refresh_date": date(2026, 10, 1), "week": weeks, "region_code": region,
                         "region_name": region or None, "score": values})  # fmt: skip


def test_scale_check_sees_series_scaled_one_by_one():
    scaled = pd.concat([rows("a", [50.0, 100.0], "TR-34"), rows("b", [100.0, 10.0], "TR-34"), rows("a", [1.0], "")])
    assert scale_check(scaled) == {"series": 2, "at_100": 2, "per_series": True}
    shared = pd.concat([rows("a", [50.0, 100.0], "TR-34"), rows("b", [30.0, 10.0], "TR-34")])
    assert scale_check(shared)["per_series"] is False
    assert scale_check(rows("a", [1.0], ""))["per_series"] is None


def test_weekly_series_is_dated_at_week_end_and_averages_spellings():
    frame = pd.concat([rows("a", [10.0, 20.0]), rows("A", [30.0, 40.0]), rows("a", [90.0, 90.0], "TR-34")])
    series = weekly_series(frame, ["a", "A"])
    assert series.tolist() == [20.0, 30.0] and series.index[0] == pd.Timestamp("2024-01-13")
    assert weekly_series(frame, ["yok"]).empty


def test_interest_summary():
    series = weekly_series(rows("a", [10.0] * 52 + [20.0] * 4), ["a"])
    summary = interest_summary(series)
    assert summary["momentum"] == 2.0 and summary["weeks"] == 56 and summary["sparse"] is False
    assert interest_summary(pd.Series(dtype=float)) is None
    spike = interest_summary(weekly_series(rows("a", [0.0] * 60 + [100.0]), ["a"]))
    assert spike["sparse"] is True and spike["momentum"] is None


def test_province_summary_orders_by_published_weeks_not_levels():
    frame = pd.concat([rows("a", [100.0], "TR-06"), rows("a", [5.0, 6.0, 7.0], "TR-34"), rows("a", [1.0, 1.0, 1.0])])
    summary = province_summary(frame, ["a"])
    assert summary["provinces_with_data"] == 2
    assert [p["code"] for p in summary["most_weeks"]] == ["TR-34", "TR-06"]
    assert province_summary(frame, ["yok"]) is None


def months_of_lists(terms_by_day: dict) -> pd.DataFrame:
    return pd.DataFrame(
        [("top", d, t, r) for d, items in terms_by_day.items() for t, r in items],
        columns=["table", "refresh_date", "term", "rank"],
    )


def weekly(start, values) -> pd.Series:
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="7D"), dtype=float)


def test_monthly_features_interest_columns():
    brand = weekly("2026-01-03", [10.0] * 13 + [20.0] * 13)
    generic = {"g": weekly("2026-01-03", [5.0] * 26)}
    frame = monthly_features(brand, generic, months_of_lists({}), ["b"], [], 2, date(2026, 12, 31))
    assert {"marka_ilgi_degisim", "generic_ilgi", "marka_generic_orani"} == set(frame.columns)  # no level: it carries look-ahead
    assert not {"arama_payi", "liste_gun_sayisi"} & set(frame.columns)
    assert frame["generic_ilgi"].dropna().eq(1.0).all()  # a flat category stays at its usual level
    assert frame["marka_generic_orani"].iloc[-1] > 1  # the brand is above its own past, the category is not
    assert np.isnan(frame["generic_ilgi"].iloc[0])  # no past yet: nothing is filled


def test_ratio_features_do_not_depend_on_the_scale():
    brand = weekly("2026-01-03", np.linspace(10, 40, 26))
    a = monthly_features(brand, {}, months_of_lists({}), ["b"], [], 2, date(2026, 12, 31))
    b = monthly_features(brand * 2, {}, months_of_lists({}), ["b"], [], 2, date(2026, 12, 31))
    pd.testing.assert_series_equal(a["marka_ilgi_degisim"], b["marka_ilgi_degisim"])


def test_running_month_is_dropped():
    brand = weekly("2026-09-05", [10.0] * 9)  # up to 31 October; the last week lands in November with the lag
    frame = monthly_features(brand, {}, months_of_lists({}), ["b"], [], 2, date(2026, 11, 4))
    assert frame.index.max() == pd.Timestamp("2026-10-31")


def test_list_columns_only_for_covered_months():
    days = pd.date_range("2026-10-01", "2026-10-29").date  # with the 2-day lag all fall into October
    lists = {d: [("x", 1)] for d in days}
    lists[days[3]] = [("marka", 4), ("x", 1)]
    lists[days[4]] = [("marka", 9), ("rakip", 2)]
    lists[date(2026, 9, 20)] = [("marka", 1)]  # September has too few retained lists
    frame = monthly_features(pd.Series(dtype=float), {}, months_of_lists(lists), ["marka"], ["rakip"], 2, date(2026, 11, 4))
    assert list(frame.index) == [pd.Timestamp("2026-10-31")]
    assert frame["liste_gun_sayisi"].iloc[0] == 2 and frame["liste_en_iyi_sira"].iloc[0] == 4
    assert frame["rakip_liste_gun_sayisi"].iloc[0] == 1


def test_absent_brand_in_a_covered_month_is_a_real_zero():
    days = pd.date_range("2026-10-01", "2026-10-29").date
    frame = monthly_features(pd.Series(dtype=float), {}, months_of_lists({d: [("x", 1)] for d in days}), ["marka"], [], 2, date(2026, 11, 4))
    assert frame["liste_gun_sayisi"].iloc[0] == 0 and np.isnan(frame["liste_en_iyi_sira"].iloc[0])


@pytest.mark.parametrize("lists", [{}, {date(2026, 10, 1): [("x", 1)]}])
def test_no_list_columns_without_coverage(lists):
    frame = monthly_features(pd.Series(dtype=float), {}, months_of_lists(lists), ["marka"], [], 2, date(2026, 11, 4))
    assert frame.empty
