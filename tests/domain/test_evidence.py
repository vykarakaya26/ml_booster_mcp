from dataclasses import replace
from datetime import date

import pytest

from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.services import evidence as rules
from mlbooster.domain.services.evidence import EvidenceDraft

TODAY = date(2026, 11, 4)
QUOTE = "organize perakendede pazar payımız %24,5 oldu"


def draft(**kwargs) -> EvidenceDraft:
    base = {"brand": "Migros", "value": 24.5, "basis": "revenue", "period": "2024", "published": "2025-03-01",
            "publisher": "Migros Ticaret A.Ş.", "url": "https://www.migroskurumsal.com/rapor.pdf", "quote": QUOTE}  # fmt: skip
    return EvidenceDraft(**{**base, **kwargs})


@pytest.fixture
def build(catalog):
    return lambda d: rules.build(d, catalog.evidence, catalog.brands, TODAY)


def press(**kwargs) -> EvidenceDraft:
    return draft(url="https://www.aa.com.tr/tr/ekonomi/x/123", publisher="Anadolu Ajansı", original_source="Migros", original_date="2025-03", **kwargs)


# ------------------------------------------------------------------------------------------- pages


@pytest.mark.parametrize(
    ("url", "tier"),
    [
        ("https://www.rekabet.gov.tr/karar?kararId=abc", "official"),
        ("https://rekabet.gov.tr/Dosya/x.pdf", "official"),
        ("https://data.tuik.gov.tr/Bulten/Index?p=1", "official"),
        ("https://www.resmigazete.gov.tr/eskiler/2025/01/1.pdf", "official"),
        ("https://www.migroskurumsal.com/rapor.pdf", "company"),
        ("https://www.tsb.org.tr/x", "company"),
    ],
)
def test_tier_comes_from_the_domain(build, url, tier):
    assert build(draft(url=url))[0].tier == tier


@pytest.mark.parametrize(
    ("url", "needle"),
    [
        ("http://www.rekabet.gov.tr/x", "https"),
        ("https://rekabet.gov.tr.evil.com/x", "politikasında yok"),
        ("https://notrekabet.gov.tr/x", "politikasında yok"),
        ("https://evil.com/rekabet.gov.tr", "politikasında yok"),
        ("https://user:pw@rekabet.gov.tr/x", "kullanıcı bilgisi"),
        ("https://rekabet.gov.tr:8443/x", "port"),
        ("https://rekabet.gov.tr/a b", "Geçersiz bağlantı"),
        ("https://rekabet.gov.tr/x\n", "Geçersiz bağlantı"),
        ("javascript:alert(1)", "https"),
        ("https://" + "a" * 600 + ".gov.tr", "Geçersiz bağlantı"),
        ("https://www.reddit.com/r/x", "kabul edilmiyor"),
        ("https://migros.blogspot.com/x", "kabul edilmiyor"),
        ("https://www.eksisozluk.com/migros", "kabul edilmiyor"),
        ("https://www.statista.com/statistics/1", "ücretli"),
        ("https://www.euromonitor.com/x", "ücretli"),
        ("https://rekabet.gov.tr/x?mail=ali@example.com", "kişisel veri"),
    ],
)
def test_bad_pages_are_refused(build, url, needle):
    with pytest.raises(InvalidInput, match=needle):
        build(draft(url=url))


def test_url_is_normalized_so_one_page_has_one_address(catalog):
    assert rules.clean_url("https://WWW.Rekabet.gov.tr/x.pdf?utm_source=a&id=7&gclid=b#page=3")[0] == "https://www.rekabet.gov.tr/x.pdf?id=7"


def test_a_company_site_counts_for_its_own_brand_only(build):
    with pytest.raises(InvalidInput, match="yalnızca Migros"):
        build(draft(brand="BİM"))


def test_own_site_of_an_unlisted_brand_stays_low_confidence(build):
    item, notes = build(draft(brand="Penti", url="https://ir.penti.com.tr/rapor.pdf", confidence="high"))
    assert item.tier == "company" and item.confidence == "low" and "doğrulanmalıdır" in notes[0]


@pytest.mark.parametrize("url", ["https://pentihaber.com/x", "https://penti-kampanya.net/x", "https://pen.com.tr/x"])
def test_brand_name_rule_is_narrow(build, url):
    with pytest.raises(InvalidInput, match="politikasında yok"):
        build(draft(brand="Penti", url=url))


def test_short_brand_names_never_use_the_name_rule(build):
    with pytest.raises(InvalidInput, match="politikasında yok"):
        build(draft(brand="ABC", url="https://abcmarket.com/x"))


# ------------------------------------------------------------------------------------------- kap


def test_kap_keeps_one_disclosure_link_and_no_quote(build):
    item, _ = build(draft(url="https://www.kap.org.tr/tr/Bildirim/1234567", quote=""))
    assert item.tier == "company" and item.quote == "" and item.url.endswith("/1234567")


@pytest.mark.parametrize("url", ["https://www.kap.org.tr/tr/sirket-bilgileri/ozet/1", "https://www.kap.org.tr/tr/bildirim-sorgu"])
def test_kap_refuses_anything_but_a_single_disclosure(build, url):
    with pytest.raises(InvalidInput, match="tek bir bildirimin"):
        build(draft(url=url, quote=""))


def test_kap_refuses_stored_text(build):
    with pytest.raises(InvalidInput, match="alıntı saklanmaz"):
        build(draft(url="https://www.kap.org.tr/tr/Bildirim/1234567"))


# ------------------------------------------------------------------------------------------- claims


@pytest.mark.parametrize(
    ("changes", "needle"),
    [
        ({"value": 124.5}, "0–100"),
        ({"value": -1}, "0–100"),
        ({"value": float("nan")}, "sayı"),
        ({"value": True}, "sayı"),
        ({"basis": "pazar"}, "ölçü"),
        ({"claim": "tahmin"}, "iddia türü"),
        ({"qualifier": "belki"}, "niteleyici"),
        ({"confidence": "kesin"}, "güven"),
        ({"period": ""}, "Dönem"),
        ({"period": "geçen yıl"}, "Dönem"),
        ({"period": "2030", "published": "2026"}, "bitmemiş"),
        ({"period": "2026", "published": "2026-10-01"}, "bitmemiş"),
        ({"period": "2025", "published": "2025-06-01"}, "dönemden önce"),
        ({"published": "2027-01-01"}, "gelecekte"),
        ({"published": "dün"}, "Yayım"),
        ({"publisher": ""}, "Yayıncı"),
        ({"publisher": "x" * 81}, "Yayıncı"),
        ({"publisher": "Ali ali@example.com"}, "kişisel veri"),
        ({"quote": ""}, "Alıntı"),
        ({"quote": "pay %24,5 " + "sözcük " * 15}, "15 sözcük"),
        ({"quote": "pazar payımız çok yükseldi"}, "rakamı"),
        ({"quote": "pay %24,5 iletişim 0532 123 45 67"}, "kişisel veri"),
        ({"quote": "pay %24,5 kimlik 12345678901"}, "kişisel veri"),
        ({"qualifier": "range"}, "value_high"),
        ({"value_high": 30.0}, "value_high"),
        ({"value_high": 20.0, "qualifier": "range"}, "üst sınırı"),
        ({"segment": "x" * 81}, "Segment"),
        ({"geography": ""}, "Coğrafya"),
    ],
)
def test_bad_claims_are_refused(build, changes, needle):
    with pytest.raises(InvalidInput, match=needle):
        build(draft(**changes))


def test_range_and_rank_claims(build):
    ranged, _ = build(draft(value=20, value_high=30, qualifier="range", quote="payı %20-30 aralığındadır"))
    assert ranged.value_high == 30 and rules.claim_text(ranged) == "%20,0–30,0"
    ranked, _ = build(draft(claim="rank", value=2, quote="sektörün ikinci büyük oyuncusu"))
    assert rules.claim_text(ranked) == "2. sıra"
    with pytest.raises(InvalidInput, match="tam sayı"):
        build(draft(claim="rank", value=2.5, quote="ikinci"))


def test_press_must_name_the_original_source_and_date(build, catalog):
    with pytest.raises(InvalidInput, match="özgün kaynağı"):
        build(draft(url="https://www.aa.com.tr/tr/x", publisher="Anadolu Ajansı"))
    assert build(press(confidence="high"))[0].confidence == "medium"


def test_paid_research_quoted_on_a_public_page_is_second_hand(build):
    item, notes = build(draft(url="https://www.aa.com.tr/tr/x", publisher="Anadolu Ajansı", original_source="NielsenIQ", original_date="2025-02", confidence="medium"))
    assert item.second_hand and item.confidence == "low" and any("ikinci el" in n for n in notes)
    with pytest.raises(InvalidInput, match="özgün kaynak"):
        build(draft(quote="Euromonitor verisine göre pay %24,5"))


def test_alias_and_case_lead_to_one_brand_and_one_record(build):
    a, _ = build(draft(brand="Migros Ticaret"))
    b, _ = build(draft(brand="MİGROS", url="https://www.migroskurumsal.com/rapor.pdf?utm_campaign=x"))
    assert a.brand == b.brand == "Migros" and a.id == b.id
    assert a.id != build(draft(value=25, quote="pay %25"))[0].id


# ------------------------------------------------------------------------------------------- reading


def test_expiry_and_order(build, catalog):
    old, _ = build(draft(period="2022", published="2023-03-01", value=20, quote="pay %20"))
    new, _ = build(press(period="2025", published="2026-03-01"))
    official, _ = build(draft(period="2025", published="2026-02-01", url="https://www.rekabet.gov.tr/x", publisher="Rekabet Kurumu", value=21, quote="pay %21"))
    out = rules.section("Migros", [old, new, official], catalog.evidence, TODAY)
    assert [e["tier"] for e in out["current"]] == ["official", "press"]
    assert [e["period"] for e in out["historical"]] == ["2022"] and out["next_step"] is None
    assert out["label"] == "derlenmiş; hesaplanmış pay değildir"
    assert rules.section("Migros", [old], catalog.evidence, TODAY, max_age_days=5000)["current"]


def test_no_finding_points_to_the_search(catalog):
    out = rules.section("Migros", [], catalog.evidence, TODAY)
    assert "share-research" in out["next_step"] and "Migros" in out["next_step"] and not out["single_source"]


def test_contradictions_are_reported_not_averaged(build, catalog):
    a, _ = build(draft())
    b, _ = build(draft(url="https://www.rekabet.gov.tr/x", publisher="Rekabet Kurumu", value=30, quote="pay %30"))
    out = rules.section("Migros", [a, b], catalog.evidence, date(2025, 6, 1))
    assert out["contradictions"][0]["difference"] == pytest.approx(5.5) and "ortalanmadı" in out["contradictions"][0]["message"]
    assert {e["value"] for e in out["current"]} == {24.5, 30.0}


def test_different_scope_or_close_figures_are_no_contradiction(build, catalog):
    a, _ = build(draft())
    other = draft(url="https://www.rekabet.gov.tr/x", publisher="Rekabet Kurumu", value=30, quote="pay %30")
    assert not rules.contradictions([a, build(replace(other, basis="units"))[0]], 1.0)
    assert not rules.contradictions([a, build(replace(other, segment="gıda"))[0]], 1.0)
    assert not rules.contradictions([a, build(replace(other, period="2023", published="2024"))[0]], 1.0)
    assert not rules.contradictions([a, build(replace(other, value=25, quote="pay %25"))[0]], 1.0)
    assert not rules.contradictions([a, build(replace(other, value=20, value_high=30, qualifier="range", quote="pay %20-30"))[0]], 1.0)
    assert not rules.contradictions([a, build(replace(other, value=20, qualifier="over", quote="pay %20"))[0]], 1.0)


def test_pages_repeating_one_source_count_once(build):
    a, _ = build(press())
    b, _ = build(draft(url="https://www.dunya.com/x", publisher="Dünya", original_source="Migros", original_date="2025-03"))
    c, _ = build(draft(url="https://www.rekabet.gov.tr/x", publisher="Rekabet Kurumu"))
    assert rules.independent_sources([a, b]) == 1 and rules.independent_sources([a, b, c]) == 2


SERIES = [
    {"series": "ODMD.TOPLAM", "name": "Otomobil ve hafif ticari araç satışları", "unit": "count", "basis": "ttm", "share": 3.2, "rank": 11, "last": "2026-09-30", "focus": "TOGG"},
    {"series": "ODMD.OTOMOBIL", "name": "Otomobil satışları", "unit": "count", "basis": "ttm", "share": 4.1, "rank": 9, "last": "2026-09-30", "focus": "TOGG"},
]  # fmt: skip
HISTORY = {"dates": ["2024-12-31", "2025-12-31"], "series": {"TOGG": [2.0, None]}}


def togg(**kwargs) -> EvidenceDraft:
    base = {"brand": "Togg", "value": 2.4, "basis": "units", "period": "2024", "published": "2025-02-01", "publisher": "Togg",
            "url": "https://www.togg.com.tr/x", "quote": "payımız %2,4 oldu"}  # fmt: skip
    return EvidenceDraft(**{**base, **kwargs})


def test_comparison_uses_the_computed_share_of_the_same_period(build):
    item, _ = build(togg())
    row = rules.compare([item], SERIES, HISTORY)[0]
    assert row["computed"] == 2.0 and row["computed_period"] == "Aralık 2024" and row["difference"] == pytest.approx(0.4)
    assert row["reasons"] == ["Fark yuvarlama ve revizyon düzeyinde."]


def test_comparison_explains_the_likely_reason(build):
    item, _ = build(togg(value=12, quote="payımız %12", basis="revenue", segment="elektrikli otomobil", period="2023-06", published="2024", geography="Avrupa"))
    row = rules.compare([item], SERIES, HISTORY)[0]
    assert row["series"] == "ODMD.OTOMOBIL" and row["computed"] == 4.1 and row["difference"] == pytest.approx(7.9)
    text = " ".join(row["reasons"])
    assert all(word in text for word in ("Ölçü farklı", "Dönem farklı", "Kapsam farklı", "Coğrafya farklı"))


def test_comparison_of_a_range_and_a_rank(build):
    inside, _ = build(togg(value=3, value_high=4, qualifier="range", quote="payı %3-4", period="2026-09", published="2026-10"))
    assert rules.compare([inside], SERIES)[0]["difference"] == 0
    ranked, _ = build(togg(claim="rank", value=8, quote="sekizinci sırada", period="2026-09", published="2026-10"))
    row = rules.compare([ranked], SERIES)[0]
    assert row["difference"] == -3 and row["computed_text"] == "11. sıra"
    assert rules.compare([ranked], []) == []


def test_comparison_names_a_monthly_figure_against_a_twelve_month_share(build):
    item, _ = build(togg(period="2026-09", published="2026-10", value=9, quote="eylülde payımız %9"))
    assert any("Taban farklı" in r for r in rules.compare([item], SERIES)[0]["reasons"])


def test_queries_cover_both_languages_and_skip_a_missing_sector_term(catalog):
    known = rules.search_queries(catalog.evidence, "Migros", "perakende", 2025)
    assert {q["language"] for q in known} == {"tr", "en"} and any("organize perakende" in q["query"] for q in known)
    assert all("{" not in q["query"] for q in known)
    unknown = rules.search_queries(catalog.evidence, "Abdi İbrahim", None, 2025)
    assert len(unknown) < len(known) and all("{" not in q["query"] for q in unknown)


def test_allowed_sources_add_the_brands_own_site(catalog):
    tiers = rules.allowed_sources(catalog.evidence, "Migros", catalog.brands)
    assert [t["tier"] for t in tiers] == ["official", "company", "press"]
    assert "migroskurumsal.com" in tiers[1]["domains"] and all(t["reason"] for t in tiers)
    assert "migroskurumsal.com" not in rules.allowed_sources(catalog.evidence, "BİM", catalog.brands)[1]["domains"]
