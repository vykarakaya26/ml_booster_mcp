import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from mlbooster.application.dto import FetchRequest
from mlbooster.application.errors import ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import HealthStatus, StoragePolicy
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.bkm import BkmProvider, _grid, label_key, parse_remote, parse_report
from mlbooster.infrastructure.providers.registry import PROVIDERS, ProviderRegistry
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.storage.raw_store import RawStore

# Pages recorded from bkm.com.tr on 2026-11-02 with scripts, styles and the 2018-2024 tables removed. BKM does not allow
# redistribution, so every number was replaced by year*10000 + month*100 + column (13 = quarter row, 14 = year row) in
# the original format; the markup is unchanged. Real values are checked by tests/test_live_bkm.py.
FIXTURES = Path(__file__).parent / "fixtures" / "bkm"
BASE = "https://bkm.com.tr"
MONTHS = ["OCAK", "ŞUBAT", "MART", "NISAN", "MAYIS", "HAZIRAN", "TEMMUZ", "AĞUSTOS", "EYLÜL", "EKİM", "KASIM", "ARALIK"]


def _html(name: str) -> httpx.Response:
    body = (FIXTURES / name).read_bytes()
    return httpx.Response(200, content=body, headers={"content-type": "text/html; charset=UTF-8"})


def _page(tables: str) -> httpx.Response:
    return httpx.Response(200, text=f"<html><body>{tables}</body></html>", headers={"content-type": "text/html"})


def _card_table(year: int, months: int, value: str = "1.000") -> str:
    """A 'Kart Sayıları' year table with the first `months` months, quarter and year rows."""
    rows = "".join(f"<tr><td>{MONTHS[m]}</td><td>{value}</td><td>2.000</td><td>3.000</td></tr>" for m in range(months))
    return (
        f'<table><tr><th colspan="4">{year} YILI</th></tr>'
        '<tr><th rowspan="2">Dönem</th><th colspan="3">Kart Sayısı</th></tr>'
        "<tr><th>Toplam<br>Kredi Kartı</th><th>Toplam<br>Banka Kartı</th><th>Toplam<br>Ön Ödemeli Kart</th></tr>"
        f'{rows}<tr><td>1. DÖNEM</td><td>1</td><td>2</td><td>3</td></tr><tr><td colspan="4"></td></tr></table>'
    )


@pytest.fixture
def bkm(catalog):
    provider = BkmProvider(catalog.source("bkm"), http=SecureHttpClient(rps=None, max_retries=1, sleep=lambda _: None))
    yield provider
    provider.close()


def test_registered_and_catalog_points_to_adapter(catalog):
    source = catalog.source("bkm")
    assert PROVIDERS[source.provider] is BkmProvider
    assert source.canaries and all(parse_remote(s.remote) for s in source.series)


def test_refuses_untrusted_host(catalog):
    hijacked = replace(catalog.source("bkm"), base_url="https://evil.example/")
    with pytest.raises(ProviderError, match="yalnızca"):
        BkmProvider(hijacked, http=SecureHttpClient())


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("İşlem Tutarı (Milyon TL)", "ISLEM_TUTARI_MILYON_TL"),
        ("İşlem Adedi(Kredi Kartı)", "ISLEM_ADEDI_KREDI_KARTI"),
        ("Toplam Ön Ödemeli Kart", "TOPLAM_ON_ODEMELI_KART"),
        ("Yerli Kartların Yurt İçi ve Yurtdışı Kullanımı", "YERLI_KARTLARIN_YURT_ICI_VE_YURTDISI_KULLANIMI"),
        ("AĞUSTOS", "AGUSTOS"),
        ("NİSAN", "NISAN"),
    ],
)
def test_label_key(label, key):
    assert label_key(label) == key


def test_parse_remote():
    assert parse_remote("kart-sayilari/KART_SAYISI.TOPLAM_KREDI_KARTI") == (
        "kart-sayilari", None, ("KART_SAYISI.TOPLAM_KREDI_KARTI",)
    )
    assert parse_remote("secilen-sektore-gore-aylik-gelisim,12/A.B+A.C") == (
        "secilen-sektore-gore-aylik-gelisim", "12", ("A.B", "A.C")
    )


@pytest.mark.parametrize(
    "remote",
    ["kart-sayilari", "kart-sayilari/lower", "../x/A", "Kart/A", "kart,1234/A", "kart/A+A", "kart/A..B", "-kart/A"],
)
def test_parse_remote_rejects_malformed(remote):
    with pytest.raises(InvalidInput, match="remote"):
        parse_remote(remote)


@respx.mock
def test_fetch_reads_whole_history_once_per_page(bkm):
    route = respx.get(f"{BASE}/kart-sayilari/").mock(return_value=_html("kart-sayilari.html"))
    df = bkm.fetch(FetchRequest.create("bkm", ["BKM.KART.KREDI", "BKM.KART.ONODEMELI"], date(2025, 3, 1), date(2025, 4, 30)))

    assert route.call_count == 1
    values = df.set_index(["series_id", "date"])["value"]
    assert values[("BKM.KART.KREDI", date(2025, 3, 31))] == 20250301
    assert values[("BKM.KART.ONODEMELI", date(2025, 4, 30))] == 20250403
    assert len(df) == 4  # quarter and year rows are not months
    assert set(df["entity_id"]) == {""} and df["entity_name"].isna().all()
    assert set(df["dataset_id"]) == {"kart-sayilari"} and set(df["unit"]) == {"count"}
    assert set(df["freq"]) == {"M"} and set(df["metric"]) == {"level"} and set(df["publication_lag_days"]) == {40}
    assert not df["is_proxy"].any()


def test_catalog_forbids_storing_bkm_data(catalog):
    storage = catalog.source("bkm").storage
    assert not storage.raw and not storage.observations


@respx.mock
def test_nothing_is_written_to_the_raw_store(catalog, tmp_path):
    """BKM's legal notice forbids storing its data: neither the registry nor the provider keeps raw pages."""
    respx.get(f"{BASE}/kart-sayilari/").mock(return_value=_html("kart-sayilari.html"))
    registry = ProviderRegistry(
        EnvSecretProvider(), RawStore(tmp_path), http_factory=lambda _: SecureHttpClient(rps=None, max_retries=1)
    )
    direct = BkmProvider(catalog.source("bkm"), http=SecureHttpClient(rps=None), raw_store=RawStore(tmp_path))
    for provider in (registry.create(catalog.source("bkm")), direct):
        try:
            assert not provider.fetch(FetchRequest.create("bkm", ["BKM.KART.KREDI"], date(2026, 1, 1))).empty
        finally:
            provider.close()
    assert list(tmp_path.iterdir()) == []


@respx.mock
def test_raw_pages_are_kept_when_the_catalog_allows_it(catalog, tmp_path):
    respx.get(f"{BASE}/kart-sayilari/").mock(return_value=_html("kart-sayilari.html"))
    source = replace(catalog.source("bkm"), storage=StoragePolicy())
    provider = ProviderRegistry(EnvSecretProvider(), RawStore(tmp_path), http_factory=lambda _: SecureHttpClient()).create(source)
    provider.fetch(FetchRequest.create("bkm", ["BKM.KART.KREDI"], date(2026, 1, 1)))
    provider.close()
    manifest = (tmp_path / "bkm" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(manifest) == 1 and json.loads(manifest[0])["url"] == f"{BASE}/kart-sayilari/"


@respx.mock
def test_sector_series_sum_card_types(bkm):
    route = respx.get(f"{BASE}/secilen-sektore-gore-aylik-gelisim/", params={"filter_group": "12"}).mock(
        return_value=_html("secilen-sektore-gore-aylik-gelisim-12.html")
    )
    df = bkm.fetch(
        FetchRequest.create("bkm", ["BKM.SEKTOR.MARKET.TUTAR", "BKM.SEKTOR.MARKET.ADET"], date(2026, 8, 1), date(2026, 8, 31))
    )
    assert route.call_count == 1
    values = df.set_index("series_id")["value"]
    assert values["BKM.SEKTOR.MARKET.TUTAR"] == pytest.approx(20260803.25 + 20260804.25)  # banka + kredi kartı
    assert values["BKM.SEKTOR.MARKET.ADET"] == 20260801 + 20260802
    assert set(df["currency"].dropna()) == {"TRY"}


@respx.mock
def test_three_header_rows_and_turkish_numbers(bkm):
    respx.get(f"{BASE}/internetten-yapilan-kartli-odeme-islemleri/").mock(
        return_value=_html("internetten-yapilan-kartli-odeme-islemleri.html")
    )
    respx.get(f"{BASE}/yerli-kredi-kartlarinin-yurt-ici-kullanimi/").mock(
        return_value=_html("yerli-kredi-kartlarinin-yurt-ici-kullanimi.html")
    )
    codes = ["BKM.INTERNET.ADET", "BKM.KK.YURTICI.TUTAR", "BKM.KK.YURTICI.ALISVERIS.ADET"]
    df = bkm.fetch(FetchRequest.create("bkm", codes, date(2017, 1, 1), date(2017, 1, 31)))
    assert len(df) == 3 and set(df["date"]) == {date(2017, 1, 31)}
    aug = bkm.fetch(FetchRequest.create("bkm", codes, date(2026, 8, 1), date(2026, 12, 31))).set_index("series_id")["value"]
    assert aug["BKM.KK.YURTICI.TUTAR"] == pytest.approx(20260806.25)  # İşlem Tutarı / Toplam
    assert aug["BKM.KK.YURTICI.ALISVERIS.ADET"] == 20260801  # İşlem Adedi / Alışveriş
    assert aug["BKM.INTERNET.ADET"] == 20260806  # İşlem Adedi / Yerli ve Yabancı Kartların Yurt İçi Kullanımı / Toplam


@respx.mock
def test_period_before_the_first_report_year_makes_no_request(bkm):
    route = respx.get(f"{BASE}/kart-sayilari/").mock(return_value=_html("kart-sayilari.html"))
    assert bkm.fetch(FetchRequest.create("bkm", ["BKM.KART.KREDI"], date(2010, 1, 1), date(2016, 12, 31))).empty
    assert route.call_count == 0


@respx.mock
def test_period_without_published_months_returns_no_rows(bkm):
    respx.get(f"{BASE}/kart-sayilari/").mock(return_value=_html("kart-sayilari.html"))
    assert bkm.fetch(FetchRequest.create("bkm", ["BKM.KART.KREDI"], date(2026, 9, 1), date(2026, 12, 31))).empty


@respx.mock
def test_empty_cells_are_skipped_and_sums_need_every_part(bkm):
    table = _card_table(2026, 3).replace("<td>2.000</td>", "<td>-</td>", 1).replace("<td>1.000</td>", "<td></td>", 1)
    respx.get(f"{BASE}/kart-sayilari/").mock(return_value=_page(table))
    df = bkm.fetch(FetchRequest.create("bkm", ["BKM.KART.KREDI", "BKM.KART.BANKA"], date(2026, 1, 1), date(2026, 12, 31)))
    assert sorted(df[df["series_id"] == "BKM.KART.KREDI"]["date"]) == [date(2026, 2, 28), date(2026, 3, 31)]
    assert len(df[df["series_id"] == "BKM.KART.BANKA"]) == 2


def test_columns_summed_only_where_every_part_exists(bkm):
    # A sum of an incomplete and a complete column keeps only the months both have.
    seed = replace(
        bkm.source.seed("BKM.KART.KREDI"), remote="kart-sayilari/KART_SAYISI.TOPLAM_KREDI_KARTI+KART_SAYISI.TOPLAM_BANKA_KARTI"
    )
    bkm.source = replace(bkm.source, series=(seed,))
    table = _card_table(2026, 3).replace("<td>2.000</td>", "<td>-</td>", 1)
    with respx.mock:
        respx.get(f"{BASE}/kart-sayilari/").mock(return_value=_page(table))
        df = bkm.fetch(FetchRequest.create("bkm", ["BKM.KART.KREDI"], date(2026, 1, 1), date(2026, 12, 31)))
    assert df.set_index("date")["value"].to_dict() == {date(2026, 2, 28): 3000.0, date(2026, 3, 31): 3000.0}
    assert set(parse_report(table)["KART_SAYISI.TOPLAM_ON_ODEMELI_KART"]) == {
        date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)
    }


def test_grid_expands_spans():
    rows = [
        [("Dönem", 1, 3), ("A", 2, 1), ("B", 1, 2)],
        [("a1", 1, 1), ("a2", 1, 1)],
        [("x", 1, 1), ("y", 1, 1), ("z", 1, 1)],
    ]
    assert _grid(rows) == [["Dönem", "A", "A", "B"], ["Dönem", "a1", "a2", "B"], ["Dönem", "x", "y", "z"]]


@respx.mock
@pytest.mark.parametrize(
    ("response", "error", "match"),
    [
        (httpx.Response(302, headers={"location": f"{BASE}/"}), UnexpectedResponse, "yönlendirme"),
        (httpx.Response(403, text="Forbidden"), ProviderError, "HTTP 403"),
        (httpx.Response(200, json={"a": 1}), UnexpectedResponse, "HTML yerine"),
        (_page("<table><tr><td>Lütfen tarih seçiniz.</td></tr></table>"), UnexpectedResponse, "tablosu bulunamadı"),
        (_page('<table><tr><th>2026 YILI</th></tr><tr><td>1. DÖNEM</td><td>1</td></tr></table>'), UnexpectedResponse, "ay satırları"),
        (_page(_card_table(2026, 2).replace("<td>1.000</td>", "<td>1,000.5</td>", 1)), UnexpectedResponse, "sayısal olmayan"),
        (_page(_card_table(2026, 2).replace("<td>3.000</td></tr>", "</tr>", 1)), UnexpectedResponse, "kolon sayısı"),
        (_page(_card_table(2026, 2).replace('<th colspan="3">Kart', '<th colspan="2">Kart')), UnexpectedResponse, "başlık yapısı"),
        (_page(_card_table(2026, 2).replace("Kredi Kartı", "Kredili Kart")), UnexpectedResponse, "TOPLAM_KREDI_KARTI' kolonu"),
    ],
)
def test_bad_responses(bkm, response, error, match):
    respx.get(f"{BASE}/kart-sayilari/").mock(return_value=response)
    with pytest.raises(error, match=match):
        bkm.fetch(FetchRequest.create("bkm", ["BKM.KART.KREDI"], date(2026, 1, 1), date(2026, 12, 31)))


def test_unknown_series_and_transforms_rejected(bkm):
    with pytest.raises(InvalidInput, match="bilinmeyen seri"):
        bkm.fetch(FetchRequest.create("bkm", ["BKM.YOK"], date(2026, 1, 1)))
    with pytest.raises(InvalidInput, match="desteklenmiyor"):
        bkm.fetch(FetchRequest.create("bkm", ["BKM.KART.KREDI"], date(2026, 1, 1), formula=1))


def test_list_series_filters_catalog(bkm):
    assert "BKM.KART.KREDI" in bkm.list_series()["series_id"].tolist()
    found = bkm.list_series("yabancı")
    assert set(found["series_id"]) == {"BKM.KK.YABANCI.TUTAR", "BKM.BK.YABANCI.TUTAR", "BKM.INTERNET.TUTAR", "BKM.INTERNET.ADET"}


def _published_until(last: date) -> httpx.Response:
    tables = "".join(_card_table(y, last.month if y == last.year else 12) for y in range(last.year, last.year - 2, -1))
    return _page(tables)


@respx.mock
def test_healthcheck_ok(bkm):
    last_month = (date.today().replace(day=1) - timedelta(days=1)).replace(day=1) - timedelta(days=1)
    respx.get(f"{BASE}/kart-sayilari/").mock(return_value=_published_until(last_month))
    result = bkm.healthcheck()
    assert result.status is HealthStatus.OK, result.detail
    assert f"BKM.KART.KREDI son={last_month}" in result.detail and result.checked == ("BKM.KART.KREDI",)


@respx.mock
def test_healthcheck_fails_without_recent_observations(bkm):
    respx.get(f"{BASE}/kart-sayilari/").mock(return_value=_page(_card_table(2017, 12)))
    result = bkm.healthcheck()
    assert result.status is HealthStatus.FAIL and "gözlem yok" in result.detail


@respx.mock
def test_healthcheck_fails_on_stale_series(bkm, monkeypatch):
    monkeypatch.setattr("mlbooster.infrastructure.providers.bkm._HEALTH_LOOKBACK_DAYS", 400)
    respx.get(f"{BASE}/kart-sayilari/").mock(return_value=_published_until(date.today() - timedelta(days=200)))
    result = bkm.healthcheck()
    assert result.status is HealthStatus.FAIL and "son gözlem" in result.detail


@respx.mock
def test_healthcheck_reports_provider_errors(bkm):
    respx.get(f"{BASE}/kart-sayilari/").mock(return_value=httpx.Response(403, text="Forbidden"))
    result = bkm.healthcheck()
    assert result.status is HealthStatus.FAIL and "HTTP 403" in result.detail


def test_healthcheck_skipped_without_canaries(catalog):
    source = replace(catalog.source("bkm"), series=())
    assert BkmProvider(source, http=SecureHttpClient()).healthcheck().status is HealthStatus.SKIPPED
