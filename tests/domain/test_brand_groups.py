from datetime import date

import pandas as pd
import pytest

from mlbooster.domain.errors import NotFound
from mlbooster.domain.model import BrandGroup, BrandLexicon, GroupMember
from mlbooster.domain.services import brand_groups
from mlbooster.domain.services.brand_resolution import GROUP, RESOLVED, EntityRecord, EntityScan, pick, resolve

URL = "https://example.com/belge"
SEEN = date(2026, 11, 4)
GROUP_X = BrandGroup("grup_x", "Grup X", "odmd", (
    GroupMember("A", "Marka A", URL, SEEN),
    GroupMember("B", "Marka B", URL, SEEN, start=date(2025, 5, 1)),
    GroupMember("C", "Marka C", URL, SEEN, end=date(2025, 1, 31)),
), aliases=("X Otomotiv",))  # fmt: skip
LABELS = {"A": "MARKA A", "B": "MARKA B", "C": "MARKA C"}
LEXICON = BrandLexicon(groups=(GROUP_X,))
SECTORS = {"odmd": "otomotiv"}
SCAN = EntityScan(tuple(EntityRecord("odmd", e, label, (label,)) for e, label in LABELS.items()))


def table() -> pd.DataFrame:
    rows = [(e, d, v) for d in ("2024-12-31", "2025-04-30", "2025-05-31") for e, v in (("MARKA A", 10), ("MARKA B", 5), ("MARKA C", 2), ("RAKIP", 20), ("TOPLAM", 37))]
    return pd.DataFrame(rows, columns=["entity", "date", "value"])


def values(out: pd.DataFrame, day: str) -> dict:
    return dict(out[out["date"] == day][["entity", "value"]].to_numpy())


def test_a_member_counts_only_in_its_dates():
    out = brand_groups.merged_table(table(), GROUP_X, LABELS)
    assert values(out, "2024-12-31") == {"Grup X": 12, "MARKA B": 5, "RAKIP": 20, "TOPLAM": 37}  # B not yet, C still in
    assert values(out, "2025-04-30") == {"Grup X": 10, "MARKA B": 5, "MARKA C": 2, "RAKIP": 20, "TOPLAM": 37}  # C has left
    assert values(out, "2025-05-31") == {"Grup X": 15, "MARKA C": 2, "RAKIP": 20, "TOPLAM": 37}  # B has joined


def test_a_member_the_source_does_not_list_is_skipped():
    out = brand_groups.merged_table(table(), GROUP_X, {"A": "MARKA A"})
    assert values(out, "2024-12-31")["Grup X"] == 10


def test_summary_shows_each_member_and_says_what_is_unknown():
    players = [{"entity": "MARKA A", "share": 27.0, "rank": 2, "d_year_pp": 1.0}, {"entity": "MARKA B", "share": 13.5, "rank": 3, "d_year_pp": None}]
    out = brand_groups.summary(GROUP_X, players, LABELS, 40.5, date(2025, 5, 31))
    assert [m["name"] for m in out["members"]] == ["Marka A", "Marka B", "Marka C"] and out["label"] == "üye markaların toplamı"
    assert out["members"][0]["url"] == URL and out["members"][2]["included"] is False and out["members"][2]["share"] is None
    text = " ".join(out["notes"])
    assert "Başlangıç tarihi doğrulanamadı" in text and "Marka A" in text and "2025-05-01 tarihinden itibaren" in text
    assert out["members_sum"] == pytest.approx(40.5)


def test_summary_explains_a_sum_that_differs_from_the_twelve_month_share():
    players = [{"entity": "MARKA A", "share": 30.0}, {"entity": "MARKA B", "share": 20.0}]
    assert "son 12 ay" in " ".join(brand_groups.summary(GROUP_X, players, LABELS, 41.0, date(2025, 5, 31))["notes"])


def test_a_group_name_resolves_to_the_group():
    out = resolve("x otomotiv", SCAN, LEXICON, SECTORS)
    assert out.status == RESOLVED and out.match.kind == GROUP and out.match.name == "Grup X" and out.match.entity_ids == ("A", "B", "C")
    assert out.match.id == "odmd:GROUP.grup_x"


def test_a_group_can_be_chosen_by_id_and_needs_a_member_in_the_scan():
    assert pick("x", "odmd:GROUP.grup_x", SCAN, LEXICON, SECTORS).match.group is GROUP_X
    with pytest.raises(NotFound):
        pick("x", "odmd:GROUP.yok", SCAN, LEXICON, SECTORS)
    assert resolve("Grup X", EntityScan(), LEXICON, SECTORS).status != RESOLVED


def test_catalog_groups_have_a_dated_document_for_every_member(catalog):
    assert {g.id for g in catalog.brands.groups} >= {"dogus_otomotiv", "tofas", "oyak_renault", "toyota_turkiye"}
    for group in catalog.brands.groups:
        for member in group.members:
            assert member.url.startswith("https://") and member.checked
    tofas = next(g for g in catalog.brands.groups if g.id == "tofas")
    assert {m.entity for m in tofas.members if m.start == date(2025, 5, 1)} == {"PEUGEOT", "CITROEN", "OPEL", "DS"}
