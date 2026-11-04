import pytest

from mlbooster.domain.errors import InvalidInput, NotFound
from mlbooster.domain.model import BrandEntry, BrandLexicon, BrandTarget
from mlbooster.domain.services.brand_resolution import (
    AMBIGUOUS,
    NOT_FOUND,
    RESOLVED,
    EntityRecord,
    EntityScan,
    fold,
    is_placeholder,
    pick,
    resolve,
    score_name,
    sector_hints,
    tokens,
    trends_code,
)
from mlbooster.domain.validation import brand_choice, brand_query

LEXICON = BrandLexicon(
    entries=(
        BrandEntry("Volkswagen", ("VW",), (BrandTarget("odmd", ("VOLKSWAGEN",)),)),
        BrandEntry("Petrol Ofisi", ("PO",), (BrandTarget("epdk", ("PETROL_OFISI",)),)),
        BrandEntry("Garanti BBVA", ("Garanti",), (BrandTarget("tbb", ("30",)),)),
        BrandEntry("Turkcell", ("Superonline",), (BrandTarget("btk", ("TURKCELL", "SUPERONLINE")),)),
        BrandEntry("Koç Grubu", ("Koç",), (BrandTarget("tbb", ("37",)), BrandTarget("epdk", ("AYGAZ",)))),
        BrandEntry("Eski Marka", (), (BrandTarget("odmd", ("YOK",)),)),
        BrandEntry("Migros", (), (), "perakende"),
        BrandEntry("Kuveyt Türk", (), (), "bankacilik", "Katılım bankası"),
    ),
    legal_tokens=frozenset({"as", "tas", "tao"}),
    generic_tokens=frozenset({"turkiye", "turk", "cumhuriyeti", "anonim", "sirketi", "bankasi", "bank", "sigorta", "hayat", "emeklilik", "ve", "petrolculuk"}),
    sector_keywords=(("perakende", ("market", "magaza")), ("sigorta", ("sigorta",))),
)
SECTORS = {"odmd": "otomotiv", "tbb": "bankacilik", "tsb": "sigorta", "epdk": "akaryakit", "btk": "telekom"}


def _record(source, entity_id, name, *older):
    return EntityRecord(source, entity_id, name, (name, *older), (), "2026-06-30")


SCAN = EntityScan((
    _record("odmd", "VOLKSWAGEN", "VOLKSWAGEN"),
    _record("odmd", "TOGG", "TOGG"),
    _record("tbb", "30", "Türkiye Garanti Bankası A.Ş."),
    _record("tbb", "37", "Yapı ve Kredi Bankası A.Ş."),
    _record("tbb", "93", "Anadolubank A.Ş."),
    _record("tbb", "121", "Aytemiz Yatırım Bankası A.Ş."),
    _record("tsb", "1005", "Anadolu Anonim Türk Sigorta Şirketi"),
    _record("tsb", "3004", "Anadolu Hayat Emeklilik AŞ"),
    _record("tsb", "3012", "Garanti BBVA Emeklilik ve Hayat AŞ"),
    _record("tsb", "1059", "Turkcell Dijital Sigorta AŞ"),
    _record("epdk", "PETROL_OFISI", "PETROL OFİSİ"),
    _record("epdk", "AYGAZ", "AYGAZ"),
    _record("epdk", "OPET_PETROLCULUK", "OPET PETROLCÜLÜK"),
    _record("epdk", "THY_OPET", "THY OPET"),
    _record("btk", "TURKCELL", "Turkcell"),
    _record("btk", "SUPERONLINE", "Superonline"),
    _record("btk", "HALK", "Yeni Ad", "Eski Ad Bankası"),
))  # fmt: skip


def _resolve(query):
    return resolve(query, SCAN, LEXICON, SECTORS)


@pytest.mark.parametrize(
    ("raw", "folded"),
    [("İŞ BANKASI", "is bankasi"), ("Işık", "isik"), ("TÜPRAŞ", "tupras"), ("Société", "societe"), ("Çağrı", "cagri"), ("şeker", "seker")],
)
def test_fold_handles_turkish_letters_whichever_way_they_are_encoded(raw, folded):
    assert fold(raw) == folded


def test_tokens_drop_company_form_words_and_dots():
    assert tokens("Akbank T.A.Ş.", LEXICON) == ("akbank",)
    assert tokens("Türkiye Vakıflar Bankası T.A.O.", LEXICON) == ("turkiye", "vakiflar", "bankasi")
    assert tokens("A.Ş.", LEXICON) == ("as",)  # a name made only of such words keeps them


@pytest.mark.parametrize(
    ("query", "expected"),
    [("VW", "odmd:VOLKSWAGEN"), ("vw", "odmd:VOLKSWAGEN"), ("PO", "epdk:PETROL_OFISI"), ("Garanti", "tbb:30"), ("Togg", "odmd:TOGG"), ("TOGG", "odmd:TOGG"),
     ("Opet", "epdk:OPET_PETROLCULUK"), ("Anadolu Sigorta", "tsb:1005"), ("anadolu hayat", "tsb:3004"), ("Turkcell", "btk:TURKCELL+SUPERONLINE"), ("Superonline", "btk:TURKCELL+SUPERONLINE")],
)  # fmt: skip
def test_aliases_and_exact_names_resolve_to_one_brand(query, expected):
    out = _resolve(query)
    assert out.status == RESOLVED and out.match.id == expected
    assert out.match.sector == SECTORS[expected.split(":")[0]]


def test_weaker_names_stay_visible_next_to_the_resolved_brand():
    assert [m.name for m in _resolve("Garanti").others] == ["Garanti BBVA Emeklilik ve Hayat AŞ"]
    assert [m.name for m in _resolve("Turkcell").others] == ["Turkcell Dijital Sigorta AŞ"]
    assert [m.name for m in _resolve("Opet").others] == ["THY OPET"]


def test_an_old_name_of_a_player_still_finds_it():
    assert _resolve("Eski Ad Bankası").match.id == "btk:HALK"


def test_same_name_in_two_sectors_is_ambiguous_not_a_guess():
    scan = EntityScan((*SCAN.records, _record("epdk", "AYTEMIZ_AKARYAKIT", "AYTEMİZ AKARYAKIT")))
    lexicon = BrandLexicon(generic_tokens=LEXICON.generic_tokens | {"akaryakit", "yatirim"}, legal_tokens=LEXICON.legal_tokens)
    out = resolve("Aytemiz", scan, lexicon, SECTORS)
    assert out.status == AMBIGUOUS and {m.sector for m in out.matches} == {"akaryakit", "bankacilik"}


def test_group_name_offers_every_member_with_its_sector():
    out = _resolve("Koç")
    assert out.status == AMBIGUOUS and [m.id for m in out.matches] == ["tbb:37", "epdk:AYGAZ"]
    assert {m.sector for m in out.matches} == {"bankacilik", "akaryakit"}


@pytest.mark.parametrize("query", ["Anadolu", "Volkswagon", "Turkcel", "Dijital"])
def test_partial_and_fuzzy_matches_are_only_candidates(query):
    out = _resolve(query)
    assert out.status == AMBIGUOUS and out.matches
    assert all(m.score < 1 for m in out.matches)


def test_alias_target_that_the_scan_has_not_seen_is_not_offered():
    assert _resolve("Eski Marka").status == NOT_FOUND


def test_unknown_names_are_not_found():
    for query in ("Şirket Yok", "Xyz", "汉字"):
        assert _resolve(query).status == NOT_FOUND


def test_known_brand_without_a_source_keeps_its_catalog_entry():
    out = _resolve("Migros")
    assert out.status == NOT_FOUND and out.known.sector == "perakende"
    assert _resolve("Kuveyt").known.name == "Kuveyt Türk"  # part of the name is enough


def test_a_source_outside_the_sector_list_is_ignored():
    assert resolve("Togg", SCAN, LEXICON, {"tbb": "bankacilik"}).status == NOT_FOUND


def test_short_text_is_not_matched_fuzzily():
    assert score_name("Bim", "BMW", LEXICON) is None
    assert score_name("Sok", "Sompo", LEXICON) is None


def test_pick_returns_the_chosen_candidate():
    out = pick("Koç", "tbb:37", SCAN, LEXICON, SECTORS)
    assert out.status == RESOLVED and out.match.name == "Yapı ve Kredi Bankası A.Ş." and out.match.kind == "choice"
    assert pick("x", "btk:TURKCELL+SUPERONLINE", SCAN, LEXICON, SECTORS).match.name == "Turkcell"


@pytest.mark.parametrize("choice", ["tbb:999", "yok:30", "tbb:30+999"])
def test_pick_refuses_a_player_the_scan_has_not_seen(choice):
    with pytest.raises(NotFound, match="bulunamadı"):
        pick("x", choice, SCAN, LEXICON, SECTORS)


def test_sector_hints_use_catalog_or_keywords():
    assert sector_hints("Migros", _resolve("Migros").known, LEXICON)[0][0] == "perakende"
    assert [s for s, _ in sector_hints("Yılmaz Market", None, LEXICON)] == ["perakende"]
    assert sector_hints("Xyz", None, LEXICON) == []


def test_trends_code_is_a_valid_series_code():
    assert trends_code("Türk Hava Yolları A.Ş.", LEXICON) == "BQT.ILGI.TURK_HAVA_YOLLARI"
    assert trends_code("汉字", LEXICON) is None


def test_placeholder_rows_are_not_players():
    assert is_placeholder("Diğer") and is_placeholder("DİĞER") and not is_placeholder("Digiturk")


def test_brand_query_cleans_and_limits_the_text():
    assert brand_query("  Türk \n Telekom ") == "Türk Telekom"
    for bad, message in ((123, "metin"), ("a" * 81, "en fazla"), ("-", "en az iki"), ("a\x00b", "kontrol")):
        with pytest.raises(InvalidInput, match=message):
            brand_query(bad)


def test_brand_choice_is_allow_listed():
    assert brand_choice("btk:TURKCELL+SUPERONLINE") == "btk:TURKCELL+SUPERONLINE"
    for bad in ("tbb", "tbb:", "tbb:3/../x", "TBB:3", "tbb:3 4", None):
        with pytest.raises(InvalidInput, match="aday"):
            brand_choice(bad)
