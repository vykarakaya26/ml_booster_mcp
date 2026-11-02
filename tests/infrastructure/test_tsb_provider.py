import io
import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote

import httpx
import openpyxl
import pytest
import respx

from mlbooster.application.dto import FetchRequest
from mlbooster.application.errors import ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers import tsb as tsb_module
from mlbooster.infrastructure.providers.registry import PROVIDERS
from mlbooster.infrastructure.providers.tsb import TsbProvider, label_key, parse_remote
from mlbooster.infrastructure.storage.raw_store import RawStore

# Listing responses recorded from www.tsb.org.tr on 2026-11-02, trimmed to the ranking file plus two other entries.
# The workbooks are built here with the layout of the real "Prim Üretimleri Sıralama" files (rows 0–5 as published).
FIXTURES = Path(__file__).parent / "fixtures" / "tsb"
BASE = "https://www.tsb.org.tr"
LIST_URL = f"{BASE}/Statistic/GetStatistics"
STATUS_URL = f"{BASE}/Statistic/GetStatisticReportStatus"
FILES_URL = f"{BASE}/content/Statistics/"

AUG = {"1035": ("Sompo Sigorta AŞ", 600.0), "1020": ("Türkiye Sigorta AŞ", 300.0), "1008": ("Unico Sigorta AŞ", 100.0)}
SEP = {"1035": ("Sompo Sigorta AŞ", 690.0), "1020": ("Türkiye Sigorta AŞ", 345.0), "1008": ("Unico Sigorta AŞ", 115.0)}


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _sheet(ws, title, year, month, companies, total=None, subtotal_code=None):
    ws.title = title
    ws.append(["ŞİRKETLERİN PAZAR PAY VE TOPLAM PRİM DEĞİŞİMLERİ "])
    ws.append([title])
    ws.append([f"01.01.{year}-{date(year, month, 1):%d}.{month:02d}.{year}"])
    ws.append([])
    ws.append([None, None, None, f"{year}-{month:02d}", None, None, f"{year - 1}-{month:02d}"])
    ws.append(["Sıralama", "Şirket Adı", "Şirket Kodu", "Toplam Üretim (TL)", "Pazar Payı %", None, "Toplam Üretim (TL)", "Pazar Payı %"])
    values = sum(v for _, v in companies.values() if isinstance(v, float))
    for rank, (code, (name, value)) in enumerate(companies.items(), start=1):
        share = value / values if isinstance(value, float) else None
        ws.append([rank, name, int(code), value, share, None, value * 0.8 if share is not None else None, None])
        if rank == 1:
            ws.append([None, "İLK 10 ŞİRKET", subtotal_code, value, None])  # subtotal: code empty or 0
    ws.append([None, "SEKTÖR TOPLAMI", 9003, values if total is None else total, 1, None, values * 0.8, 1])
    ws.append([])
    ws.append(["*Bu ve/veya ilgili sayfalardaki veriler geçici rakamlar olup, kesin sonuçlar için ilgili döneme ilişkin Gelir Tablolarına başvurulmalıdır."])


def workbook(year, month, sheets=None, **kwargs) -> bytes:
    """A ranking workbook: an overview sheet ('Genel', other layout) and one sheet per branch."""
    sheets = sheets or {"Genel Toplam": SEP, "Trafik": SEP}
    wb = openpyxl.Workbook()
    wb.active.title = "Genel"
    wb.active.append(["ŞİRKETLERİN PAZAR PAY VE TOPLAM PRİM DEĞİŞİMLERİ "])
    wb.active.append(["Branş Adı", "Toplam Üretim (TL)"])
    for title, companies in sheets.items():
        _sheet(wb.create_sheet(), title, year, month, companies, **kwargs)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _entry(year, month, path=None, file_id=None, name=None):
    return {
        "Id": file_id or year * 100 + month, "FileName": name or f"3 Prim Üretimleri Sıralama {year}-{month:02d}",
        "FilePath": path or f"/content/Statistics/3 Prim Üretimleri Sıralama {year} {month:02d}.xlsx",
        "PeriodYear": year, "PeriodMonth": month, "CreatedDate": "2026-10-19T11:46:01",
    }  # fmt: skip


class Site:
    """respx handlers for the listing and the files; unknown months answer an empty listing."""

    def __init__(self, listings=None, files=None, status=None):
        self.listings = listings or {}
        self.files = files or {}
        self.status = status or _fixture("status_open.json")
        self.listed: list[tuple[int, int]] = []
        self.downloaded: list[str] = []

    def install(self, router):
        router.get(STATUS_URL).mock(side_effect=lambda r: httpx.Response(200, json=self.status))
        router.get(url__startswith=LIST_URL).mock(side_effect=self._listing)
        router.get(url__startswith=FILES_URL).mock(side_effect=self._file)
        return self

    def _listing(self, request):
        p = request.url.params
        assert (p["statisticCategoryId"], p["statisticSubCategoryId"], p["pageId"]) == ("1", "1", "1")
        key = (int(p["periodYear"]), int(p["periodMonth"]))
        self.listed.append(key)
        doc = self.listings.get(key, {"Result": [], "TotalCount": 0})
        return httpx.Response(200, json=doc)

    def _file(self, request):
        path = unquote(request.url.raw_path.decode("ascii"))
        self.downloaded.append(path)
        content = self.files.get(path)
        if content is None:
            return httpx.Response(404, text="<html>Not Found</html>", headers={"content-type": "text/html"})
        return httpx.Response(200, content=content, headers={"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})


def site_for(months: dict[tuple[int, int], bytes]) -> Site:
    listings, files = {}, {}
    for (y, m), content in months.items():
        entry = _entry(y, m)
        listings[(y, m)] = {"Result": [entry], "TotalCount": 1}
        files[entry["FilePath"]] = content
    return Site(listings, files)


@pytest.fixture
def tsb(catalog):
    provider = TsbProvider(catalog.source("tsb"), http=SecureHttpClient(rps=None, max_retries=1, sleep=lambda _: None))
    yield provider
    provider.close()


def _fetch(provider, codes, start, end):
    return provider.fetch(FetchRequest.create("tsb", codes, start, end))


def _values(df, code):
    part = df[df["series_id"] == code]
    return {(d.month, e): v for d, e, v in zip(part["date"], part["entity_id"], part["value"], strict=True)}


# ---------------------------------------------------------------------------------------------------- catalog


def test_registered_and_catalog_points_to_adapter(catalog):
    source = catalog.source("tsb")
    assert PROVIDERS[source.provider] is TsbProvider
    assert source.canaries and all(parse_remote(s.remote) for s in source.series)
    assert source.license.notes and source.verification.value == "verified"


def test_refuses_untrusted_host(catalog):
    hijacked = replace(catalog.source("tsb"), base_url="https://evil.example/tr/istatistik/")
    with pytest.raises(ProviderError, match="yalnızca"):
        TsbProvider(hijacked, http=SecureHttpClient())


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("Trafik", "TRAFIK"),
        ("Hastalık-Sağlık", "HASTALIK_SAGLIK"),
        ("Dev.Dest.Tarım Sigortaları", "DEV_DEST_TARIM_SIGORTALARI"),
        ("Hayatdışı", "HAYATDISI"),
        ("SEKTÖR TOPLAMI", "SEKTOR_TOPLAMI"),
        ("Toplam Üretim (TL)", "TOPLAM_URETIM_TL"),
        ("Prim Üretimleri Sıralama 2015-12", "PRIM_URETIMLERI_SIRALAMA_2015_12"),  # combining diaeresis
        ("Alt Branş Teminat Adetleri", "ALT_BRANS_TEMINAT_ADETLERI"),  # combining cedilla (older entries)
    ],
)
def test_label_key_folds_turkish_letters_and_combining_marks(label, key):
    assert label_key(label) == key


@pytest.mark.parametrize("remote", ["TRAFIK", "BASKA_DOSYA/TRAFIK", "PRIM_URETIMLERI_SIRALAMA/trafik", "PRIM_URETIMLERI_SIRALAMA/TRAFIK.ytd"])
def test_parse_remote_rejects_malformed_fields(remote):
    with pytest.raises(InvalidInput, match="remote"):
        parse_remote(remote)


def test_list_series_filters_with_turkish_folding(tsb):
    assert set(tsb.list_series("saglik")["series_id"]) == {"TSB.PRIM.SAGLIK", "TSB.PRIM.SAGLIK.YTD"}
    assert len(tsb.list_series()) == len(tsb.source.series)


# ---------------------------------------------------------------------------------------------------- fetch


@respx.mock
def test_monthly_premium_is_the_difference_of_cumulative_figures(tsb):
    aug, sep = _fixture("list_2026_08.json"), _fixture("list_2026_09.json")
    site = Site(
        {(2026, 8): aug, (2026, 9): sep},
        {aug["Result"][0]["FilePath"]: workbook(2026, 8, {"Genel Toplam": AUG, "Trafik": AUG}),
         sep["Result"][0]["FilePath"]: workbook(2026, 9)},
    ).install(respx)  # fmt: skip
    df = _fetch(tsb, ["TSB.PRIM.TRAFIK", "TSB.PRIM.TRAFIK.YTD"], date(2026, 9, 1), date(2026, 9, 30))
    assert _values(df, "TSB.PRIM.TRAFIK") == {(9, ""): 150.0, (9, "1008"): 15.0, (9, "1020"): 45.0, (9, "1035"): 90.0}
    assert _values(df, "TSB.PRIM.TRAFIK.YTD") == {(9, ""): 1150.0, (9, "1008"): 115.0, (9, "1020"): 345.0, (9, "1035"): 690.0}
    assert set(df["date"]) == {date(2026, 9, 30)} and set(df["metric"]) == {"level"}
    assert set(df["dataset_id"]) == {"prim_uretimleri_siralama"} and set(df["unit"]) == {"TRY"}
    assert not df["is_proxy"].any() and set(df["publication_lag_days"]) == {21}
    total = df[df["entity_id"] == ""]
    assert set(total["entity_name"]) == {"Sektör toplamı"}
    assert df.loc[df["entity_id"] == "1035", "entity_name"].iloc[0] == "Sompo Sigorta AŞ"
    assert sorted(site.listed) == [(2026, 8), (2026, 9)]  # August only for the first monthly difference
    assert site.downloaded == [aug["Result"][0]["FilePath"], sep["Result"][0]["FilePath"]]  # one file per month


@respx.mock
def test_cumulative_only_request_does_not_read_the_previous_month(tsb):
    site = site_for({(2026, 9): workbook(2026, 9)}).install(respx)
    df = _fetch(tsb, ["TSB.PRIM.TOPLAM.YTD"], date(2026, 9, 1), date(2026, 9, 30))
    assert _values(df, "TSB.PRIM.TOPLAM.YTD")[(9, "")] == 1150.0
    assert site.listed == [(2026, 9)]


@respx.mock
def test_january_is_taken_as_published_and_a_missing_month_breaks_the_difference(tsb):
    jan, feb, apr = ({"1035": ("Sompo Sigorta AŞ", v), "1008": ("Unico Sigorta AŞ", v / 2)} for v in (100.0, 210.0, 400.0))
    site = site_for({
        (2026, 1): workbook(2026, 1, {"Genel Toplam": jan}), (2026, 2): workbook(2026, 2, {"Genel Toplam": feb}),
        (2026, 4): workbook(2026, 4, {"Genel Toplam": apr}),
    }).install(respx)  # fmt: skip
    df = _fetch(tsb, ["TSB.PRIM.TOPLAM", "TSB.PRIM.TOPLAM.YTD"], date(2026, 1, 1), date(2026, 4, 30))
    monthly = _values(df, "TSB.PRIM.TOPLAM")
    assert {m for m, e in monthly if e == ""} == {1, 2}  # March is not published: no April difference either
    assert monthly[(1, "")] == 150.0 and monthly[(2, "")] == 165.0
    assert {m for m, e in _values(df, "TSB.PRIM.TOPLAM.YTD") if e == ""} == {1, 2, 4}
    assert sorted(site.listed) == [(2026, 1), (2026, 2), (2026, 3), (2026, 4)]


@respx.mock
def test_company_leaving_mid_year_is_reversed_so_companies_add_up(tsb):
    merged = {"1020": ("Türkiye Sigorta AŞ", 900.0), "1035": ("Sompo Sigorta AŞ", 690.0)}
    before = {"1020": ("Güneş Sigorta AŞ", 300.0), "1021": ("Halk Sigorta AŞ", 500.0), "1035": ("Sompo Sigorta AŞ", 600.0)}
    site_for({(2026, 8): workbook(2026, 8, {"Genel Toplam": before}), (2026, 9): workbook(2026, 9, {"Genel Toplam": merged})}).install(respx)
    df = _fetch(tsb, ["TSB.PRIM.TOPLAM"], date(2026, 8, 1), date(2026, 9, 30))
    sep = df[df["date"] == date(2026, 9, 30)].set_index("entity_id")
    assert sep["value"].to_dict() == {"": 190.0, "1020": 600.0, "1021": -500.0, "1035": 90.0}
    assert sep["value"].drop("").sum() == sep.loc["", "value"]
    names = df.drop_duplicates("entity_id").set_index("entity_id")["entity_name"]
    assert names["1020"] == "Türkiye Sigorta AŞ" and names["1021"] == "Halk Sigorta AŞ"  # latest name everywhere


@respx.mock
def test_legacy_xls_months_and_months_before_the_first_xlsx_are_skipped(tsb):
    site = Site({(2014, 9): _fixture("list_2014_09.json")}).install(respx)
    df = _fetch(tsb, ["TSB.PRIM.TOPLAM.YTD"], date(2014, 1, 1), date(2014, 9, 30))
    assert df.empty and site.listed == [] and site.downloaded == []  # 2014-09 is before FIRST_MONTH
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(tsb_module, "FIRST_MONTH", date(2014, 9, 1))
        assert _fetch(tsb, ["TSB.PRIM.TOPLAM.YTD"], date(2014, 9, 1), date(2014, 9, 30)).empty
    assert site.listed == [(2014, 9)] and site.downloaded == []  # listed as .xls: not downloaded


@respx.mock
def test_old_listing_entries_and_revised_uploads_are_matched(tsb):
    old = _fixture("list_2015_12.json")
    revised = _entry(2015, 12, path="/content/Statistics/3_Prim_Uretimleri_Sıralama_2015-12(2).xlsx", file_id=99999)
    old["Result"].append(revised)
    site = Site({(2015, 12): old}, {revised["FilePath"]: workbook(2015, 12)}).install(respx)
    df = _fetch(tsb, ["TSB.PRIM.TOPLAM.YTD"], date(2015, 12, 1), date(2015, 12, 31))
    assert site.downloaded == [revised["FilePath"]] and len(df) == 4  # the newest upload (highest Id) is read


@respx.mock
def test_missing_file_on_the_server_skips_the_month(tsb):
    site = Site({(2016, 4): {"Result": [_entry(2016, 4)], "TotalCount": 1}}).install(respx)
    assert _fetch(tsb, ["TSB.PRIM.TOPLAM.YTD"], date(2016, 4, 1), date(2016, 4, 30)).empty
    assert len(site.downloaded) == 1


@respx.mock
def test_unpublished_month_returns_empty(tsb):
    Site({(2026, 10): _fixture("list_2026_10.json")}).install(respx)
    assert _fetch(tsb, ["TSB.PRIM.TOPLAM"], date(2026, 10, 1), date(2026, 10, 31)).empty


@respx.mock
def test_download_form_switched_on_stops_before_any_listing(tsb):
    site = Site(status={**_fixture("status_open.json"), "Value": "true"}).install(respx)
    with pytest.raises(ProviderError, match="e-posta doğrulamasına"):
        _fetch(tsb, ["TSB.PRIM.TOPLAM"], date(2026, 9, 1), date(2026, 9, 30))
    assert site.listed == []
    Site(status={"Value": None}).install(respx)
    with pytest.raises(UnexpectedResponse, match="indirme durumu"):
        _fetch(tsb, ["TSB.PRIM.TOPLAM"], date(2026, 9, 1), date(2026, 9, 30))


@pytest.mark.parametrize(
    "path",
    ["/content/Statistics/../web.config", "/content/Other/3.xlsx", "https://evil.example/3.xlsx", "/content/Statistics/a/b.xlsx",
     "/content/Statistics/3%2e%2e.xlsx", "/content/Statistics/3.csv"],
)  # fmt: skip
@respx.mock
def test_untrusted_file_paths_are_refused(tsb, path):
    Site({(2026, 9): {"Result": [_entry(2026, 9, path=path)]}}).install(respx)
    with pytest.raises(UnexpectedResponse, match="TSB dosya"):
        _fetch(tsb, ["TSB.PRIM.TOPLAM.YTD"], date(2026, 9, 1), date(2026, 9, 30))


@pytest.mark.parametrize(
    ("response", "error", "match"),
    [
        (httpx.Response(200, text="<html>Erişim engellendi</html>", headers={"content-type": "text/html"}), UnexpectedResponse, "JSON yerine"),
        (httpx.Response(200, content=b"{not json", headers={"content-type": "application/json"}), UnexpectedResponse, "çözümlenemedi"),
        (httpx.Response(200, json=[1, 2]), UnexpectedResponse, "nesne biçiminde"),
        (httpx.Response(200, json={"Result": "x"}), UnexpectedResponse, "liste"),
        (httpx.Response(302, headers={"location": "https://www.tsb.org.tr/tr"}), UnexpectedResponse, "yönlendirme"),
        (httpx.Response(403, text="forbidden"), ProviderError, "HTTP 403"),
    ],
)
@respx.mock
def test_listing_errors_are_translated(tsb, response, error, match):
    respx.get(STATUS_URL).mock(return_value=httpx.Response(200, json=_fixture("status_open.json")))
    respx.get(url__startswith=LIST_URL).mock(return_value=response)
    with pytest.raises(error, match=match):
        _fetch(tsb, ["TSB.PRIM.TOPLAM.YTD"], date(2026, 9, 1), date(2026, 9, 30))


@respx.mock
def test_missing_result_key_means_no_file(tsb):
    respx.get(STATUS_URL).mock(return_value=httpx.Response(200, json=_fixture("status_open.json")))
    respx.get(url__startswith=LIST_URL).mock(return_value=httpx.Response(200, json={"HasError": True}))
    assert _fetch(tsb, ["TSB.PRIM.TOPLAM.YTD"], date(2026, 9, 1), date(2026, 9, 30)).empty


@respx.mock
def test_html_instead_of_excel_is_rejected(tsb):
    site = site_for({})
    site.listings[(2026, 9)] = {"Result": [_entry(2026, 9)]}
    site.files[_entry(2026, 9)["FilePath"]] = b"<html>portal</html>"
    site.install(respx)
    with pytest.raises(UnexpectedResponse, match="Excel yerine"):
        _fetch(tsb, ["TSB.PRIM.TOPLAM.YTD"], date(2026, 9, 1), date(2026, 9, 30))


def _workbook_error(tsb, content, match, code="TSB.PRIM.TOPLAM.YTD"):
    with respx.mock:
        site_for({(2026, 9): content}).install(respx)
        with pytest.raises(UnexpectedResponse, match=match):
            _fetch(tsb, [code], date(2026, 9, 1), date(2026, 9, 30))


def test_workbook_validation(tsb):
    _workbook_error(tsb, b"PK\x03\x04broken", "okunamadı")
    _workbook_error(tsb, workbook(2026, 9, {"Trafik": SEP}), "'GENEL_TOPLAM' sayfası yok")
    _workbook_error(tsb, workbook(2026, 8), "dönemi istenen ayla uyuşmuyor")
    _workbook_error(tsb, workbook(2026, 9, total=1300.0), "sektör toplamını tutmuyor")
    _workbook_error(tsb, workbook(2026, 9, {"Genel Toplam": {"1035": ("Sompo Sigorta AŞ", "çok")}}, total=1.0), "sayısal olmayan")


def test_subtotal_rows_with_code_zero_are_not_companies(tsb):
    with respx.mock:
        site_for({(2026, 9): workbook(2026, 9, subtotal_code=0)}).install(respx)
        df = _fetch(tsb, ["TSB.PRIM.TOPLAM.YTD"], date(2026, 9, 1), date(2026, 9, 30))
    assert set(df["entity_id"]) == {"", "1008", "1020", "1035"}


def _edited(content: bytes, edit) -> bytes:
    wb = openpyxl.load_workbook(io.BytesIO(content))
    edit(wb["Genel Toplam"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_layout_changes_are_detected(tsb):
    def drop_header(ws):
        ws.delete_rows(6)

    def rename_value_column(ws):
        ws["D6"] = "Brüt Prim"  # the previous-year column keeps its header and must not be read instead

    def move_period_label(ws):
        ws["D5"], ws["B5"] = None, "2026-09"

    def drop_total(ws):
        ws.delete_rows(ws.max_row - 2)

    def duplicate_company(ws):
        ws["C9"] = 1035

    _workbook_error(tsb, _edited(workbook(2026, 9), drop_header), "başlık satırı")
    _workbook_error(tsb, _edited(workbook(2026, 9), rename_value_column), "kolon yapısı")
    _workbook_error(tsb, _edited(workbook(2026, 9), move_period_label), "kolon yapısı")
    _workbook_error(tsb, _edited(workbook(2026, 9), drop_total), "sektör toplamı satırı yok")
    _workbook_error(tsb, _edited(workbook(2026, 9), duplicate_company), "tekrarlanıyor")


def test_oversized_archive_is_refused(tsb, monkeypatch):
    monkeypatch.setattr(tsb_module, "MAX_UNZIPPED_BYTES", 1000)
    _workbook_error(tsb, workbook(2026, 9), "çok büyük")


def test_shrinking_cumulative_total_is_inconsistent(tsb):
    shrunk = {code: (name, value / 2) for code, (name, value) in AUG.items()}
    with respx.mock:
        site_for({(2026, 8): workbook(2026, 8, {"Genel Toplam": AUG}), (2026, 9): workbook(2026, 9, {"Genel Toplam": shrunk})}).install(respx)
        with pytest.raises(UnexpectedResponse, match="tutarsız"):
            _fetch(tsb, ["TSB.PRIM.TOPLAM"], date(2026, 9, 1), date(2026, 9, 30))


def test_rejects_unknown_series_and_transforms(tsb):
    with pytest.raises(InvalidInput, match="bilinmeyen seri"):
        _fetch(tsb, ["TSB.YOK"], date(2026, 1, 1), date(2026, 2, 1))
    with pytest.raises(InvalidInput, match="desteklenmiyor"):
        tsb.fetch(FetchRequest.create("tsb", ["TSB.PRIM.TOPLAM"], date(2026, 1, 1), date(2026, 2, 1), formula=1))


@respx.mock
def test_raw_files_go_to_the_bronze_store(catalog, tmp_path):
    site_for({(2026, 9): workbook(2026, 9)}).install(respx)
    provider = TsbProvider(catalog.source("tsb"), http=SecureHttpClient(rps=None), raw_store=RawStore(tmp_path))
    _fetch(provider, ["TSB.PRIM.TOPLAM.YTD"], date(2026, 9, 1), date(2026, 9, 30))
    manifest = [json.loads(line) for line in (tmp_path / "tsb" / "manifest.jsonl").read_text().splitlines()]
    assert len(manifest) == 1 and manifest[0]["file"].endswith(".xlsx") and len(manifest[0]["sha256"]) == 64
    assert manifest[0]["url"].startswith(FILES_URL) and " " not in manifest[0]["url"]


# ---------------------------------------------------------------------------------------------------- healthcheck


def _recent_site(last: date) -> Site:
    months, d = {}, date(last.year, 1, 1)
    while d <= last:
        companies = {c: (n, v * d.month / 9) for c, (n, v) in SEP.items()}
        months[(d.year, d.month)] = workbook(d.year, d.month, {"Genel Toplam": companies})
        d = date(d.year + d.month // 12, d.month % 12 + 1, 1)
    return site_for(months)


@respx.mock
def test_healthcheck_ok_and_stale(tsb):
    today = date.today()
    last = date(today.year, today.month, 1) - timedelta(days=20)
    _recent_site(date(last.year, last.month, 1)).install(respx)
    result = tsb.healthcheck()
    assert result.status is HealthStatus.OK and "TSB.PRIM.TOPLAM" in result.detail


@respx.mock
def test_healthcheck_fails_on_stale_data_and_errors(tsb, monkeypatch):
    stale = date.today() - timedelta(days=100)
    _recent_site(date(stale.year, stale.month, 1)).install(respx)
    monkeypatch.setattr(tsb_module, "_HEALTH_LOOKBACK_DAYS", 400)
    assert tsb.healthcheck().status is HealthStatus.FAIL
    monkeypatch.setattr(tsb_module, "_HEALTH_LOOKBACK_DAYS", 120)
    respx.get(STATUS_URL).mock(return_value=httpx.Response(200, json={"Value": "true"}))
    result = tsb.healthcheck()
    assert result.status is HealthStatus.FAIL and "ProviderError" in result.detail


@respx.mock
def test_healthcheck_fails_without_recent_observations(tsb):
    Site().install(respx)
    assert tsb.healthcheck().status is HealthStatus.FAIL


def test_healthcheck_skipped_without_canaries(catalog):
    source = catalog.source("tsb")
    provider = TsbProvider(replace(source, series=tuple(replace(s, canary=False) for s in source.series)), http=SecureHttpClient())
    assert provider.healthcheck().status is HealthStatus.SKIPPED
