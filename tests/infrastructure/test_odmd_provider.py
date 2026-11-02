import io
import json
from dataclasses import replace
from datetime import date, timedelta
from urllib.parse import parse_qs, quote, urlsplit

import httpx
import openpyxl
import pytest
import respx
from typer.testing import CliRunner

from mlbooster.application.dto import FetchRequest
from mlbooster.application.errors import ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.odmd import (
    COLUMNS,
    SEGMENTS,
    OdmdProvider,
    RetailTable,
    _validate,
    brand_key,
    brand_name,
    fold,
    parse_remote,
    parse_workbook,
)
from mlbooster.infrastructure.providers.registry import PROVIDERS
from mlbooster.infrastructure.storage.raw_store import RawStore

HOST = "https://www.odmd.org.tr"
MONTH_NAMES = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
# Brand rows of the Sep 2026 workbook (trimmed): (label, car domestic, car import, LCV domestic, LCV import).
SEP_2026 = [
    ("ALFA ROMEO", None, 76, None, None),
    ("FIAT", 637, 230, 2051, 3290),
    ("FORD", 0, 564, 2600, 1230),
    ("KG MOBILITY – SSANGYONG", None, 746, None, 664),
    ("MERCEDES-BENZ", None, 3269, None, 681),
    ("RENAULT", 10176, 373, None, 881),
    ("TESLA", None, 1725, None, None),
    ("TOGG", 6692, None, None, None),
]


def workbook(year: int, month: int, brands=SEP_2026, *, title: str | None = None, total_change=None, marka: str | None = "MARKA") -> bytes:
    """xlsx bytes laid out like ODMD's 'Perakende Satışlar (Yerli & İthal)' workbooks."""
    book = openpyxl.Workbook()
    ws = book.active
    ws.title = f"{fold(MONTH_NAMES[month - 1])}{year}"
    ws.append([title or f"PERAKENDE SATIŞLAR YERLİ / İTHAL DAĞILIMI: {MONTH_NAMES[month - 1].upper()}-{year}"])
    ws.append([marka, "OTOMOBİL", None, None, "HAFİF TİCARİ", None, None, "TOPLAM", None, None])
    ws.append([None, "YERLİ", "İTHAL", "TOPLAM", "YERLİ", "İTHAL", "TOPLAM", "YERLİ", "İTHAL", "TOPLAM"])
    sums = [0] * 9
    for label, cy, ci, ly, li in brands:
        row = [cy, ci, (cy or 0) + (ci or 0), ly, li, (ly or 0) + (li or 0), (cy or 0) + (ly or 0), (ci or 0) + (li or 0)]
        row.append(row[2] + row[5])
        ws.append([label, *row])
        sums = [a + (b or 0) for a, b in zip(sums, row, strict=True)]
    if total_change:
        total_change(sums)
    ws.append([None])
    ws.append(["TOPLAM:", *sums])
    ws.append(["* Tesla markasına ait veriler, kamuoyuna yapılan açıklamalar ışığında tahmini olarak belirlenmiştir."])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def listing(items: list[tuple[int, str]], pages: int) -> str:
    rows = "".join(
        f'<tr class="tr_r_rep">\n<td><input type="radio" name="report" onclick="sRep({i})" class="radio_reports" /></td>\n'
        f"<td class=\"td_reports_name\" onclick=\"return dRep('?primary_id={i}&type=36&target=categorial1&detail=single"
        f"&sp_table=&sp_primary=&sp_table_extra=&openfrom=sortial')\">\n{t}\n</td>\n</tr>\n"
        for i, t in items
    )
    links = "".join(
        f'&nbsp; <a href="sortial.aspx?linkpos={p}&target=categorial1&type=36&primary_id=&detail=single" class="navigatelink">{p}</a>'
        for p in range(1, pages + 1)
    )
    return f'<html><body><div id="DivPageLinks">{links}</div><table id="TableReports">{rows}</table></body></html>'


class Site:
    """Fake www.odmd.org.tr: the report list (12 entries per page, newest first, with year-to-date entries), the
    download redirect that needs the session cookie of the list page, and the xlsx files."""

    def __init__(self, last: tuple[int, int], first: tuple[int, int] = (2025, 1), files: dict | None = None):
        self.items: list[tuple[int, str]] = []
        self.files: dict[int, tuple[str, bytes]] = {}
        y, m = last
        while (y, m) >= first:
            rid = y * 100 + m
            if m == 12:
                self.items.append((rid + 50, f"{y} Yılı (Ocak-Aralık) Perakende Satışlar (Yerli &amp; İthal)"))
            self.items.append((rid, f"{y} {MONTH_NAMES[m - 1]} Perakende Satışlar (Yerli &amp; İthal)"))
            self.files[rid] = (f"{y}%20{quote(MONTH_NAMES[m - 1])}.xlsx", (files or {}).get((y, m)) or workbook(y, m))
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        self.pages = [self.items[i : i + 12] for i in range(0, len(self.items), 12)] or [[]]
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = urlsplit(str(request.url))
        query = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        self.paths.append(url.path)
        cookie = request.headers.get("cookie", "")
        html_headers = {"content-type": "text/html; charset=utf-8"}
        if url.path == "/web_2837_1/neuralnetwork.aspx":
            headers = {**html_headers, "set-cookie": "ASP.NET_SessionId=abcdefghij0123456789wxyz; path=/; HttpOnly"}
            return httpx.Response(200, text=listing(self.pages[0], len(self.pages)), headers=headers)
        if url.path == "/web_2837_1/sortial.aspx":
            page = int(query["linkpos"])
            return httpx.Response(200, text=listing(self.pages[page - 1], len(self.pages)), headers=html_headers)
        if url.path == "/web_2837_1/wf_docudownload.aspx":
            if "ASP.NET_SessionId=abcdefghij0123456789wxyz" not in cookie:
                return httpx.Response(200, text='<span id="LabelCon"></span>', headers=html_headers)
            rid = int(query["primary_id"])
            return httpx.Response(302, headers={"location": f"/folders/2837/categorial1docs/{rid}/{self.files[rid][0]}"})
        if url.path.startswith("/folders/2837/categorial1docs/"):
            rid = int(url.path.split("/")[4])
            return httpx.Response(200, content=self.files[rid][1], headers={"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
        return httpx.Response(404)

    @property
    def downloads(self) -> list[str]:
        return [p for p in self.paths if p.startswith("/folders/")]


@pytest.fixture
def odmd(catalog):
    provider = OdmdProvider(catalog.source("odmd"), http=SecureHttpClient(rps=None, max_retries=1, sleep=lambda _: None))
    yield provider
    provider.close()


def _serve(site) -> respx.Route:
    return respx.get(url__startswith=HOST).mock(side_effect=site)


def test_registered_and_catalog_points_to_adapter(catalog):
    source = catalog.source("odmd")
    assert PROVIDERS[source.provider] is OdmdProvider
    assert source.canaries and all(parse_remote(s.remote) for s in source.series)
    assert source.license.attribution == "required" and source.verification.value == "verified"


def test_refuses_untrusted_host(catalog):
    hijacked = replace(catalog.source("odmd"), base_url="https://evil.example/web_2837_1/")
    with pytest.raises(ProviderError, match="yalnızca"):
        OdmdProvider(hijacked, http=SecureHttpClient())


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("KG MOBILITY – SSANGYONG", "KG_MOBILITY_SSANGYONG"),
        ("SSANGYONG", "KG_MOBILITY_SSANGYONG"),  # renamed brand stays one entity
        ("Mercedes-Benz", "MERCEDES_BENZ"),
        ("OMODA & JAECOO", "OMODA_JAECOO"),
        ("TESLA*", "TESLA"),
        ("  Škoda ", "SKODA"),
        ("JAECOO", "OMODA_JAECOO"),
        ("ASTON MARTİN", "ASTON_MARTIN"),
    ],
)
def test_brand_key_is_stable(label, key):
    assert brand_key(label) == key


def test_fold_handles_turkish_and_decomposed_letters():
    assert fold("Eylül İthal ışık") == "EYLUL ITHAL ISIK"


def test_brand_names_are_stable_across_label_variants():
    assert brand_name("ASTON_MARTIN", "ASTON MARTİN") == brand_name("ASTON_MARTIN", "Aston  Martin*") == "ASTON MARTIN"
    assert brand_name("KG_MOBILITY_SSANGYONG", "SSANGYONG") == "KG MOBILITY – SSANGYONG"


def test_parse_remote():
    assert parse_remote("perakende/TOPLAM.TOPLAM") == ("TOPLAM", "TOPLAM")
    assert parse_remote("perakende/HAFIF_TICARI.YERLI") == ("HAFIF_TICARI", "YERLI")


@pytest.mark.parametrize("remote", ["perakende/TOPLAM", "TOPLAM.TOPLAM", "perakende/KAMYON.TOPLAM", "x/OTOMOBIL.ITHAL"])
def test_parse_remote_rejects_malformed(remote):
    with pytest.raises(InvalidInput, match="remote"):
        parse_remote(remote)


@respx.mock
def test_fetch_walks_the_list_downloads_with_session_and_maps_brands(odmd, tmp_path):
    odmd._raw_store = RawStore(tmp_path)
    site = Site(last=(2026, 9))
    route = _serve(site)
    df = odmd.fetch(FetchRequest.create("odmd", ["ODMD.TOPLAM", "ODMD.OTOMOBIL", "ODMD.HAFIF_TICARI"], date(2026, 8, 1), date(2026, 9, 30)))

    assert len(site.downloads) == 2  # one file per month for all series
    assert site.paths.count("/web_2837_1/sortial.aspx") == 0  # page 1 already reaches August 2026
    downloads = [c for c in route.calls if "wf_docudownload" in str(c.request.url)]
    assert all("ASP.NET_SessionId=" in c.request.headers["cookie"] for c in downloads)

    sep = df[df["date"] == date(2026, 9, 30)].set_index(["series_id", "entity_id"])["value"]
    assert sep[("ODMD.TOPLAM", "")] == 35885 and sep[("ODMD.OTOMOBIL", "")] == 24488 and sep[("ODMD.HAFIF_TICARI", "")] == 11397
    assert sep[("ODMD.OTOMOBIL", "RENAULT")] == 10549 and sep[("ODMD.HAFIF_TICARI", "RENAULT")] == 881
    assert sep[("ODMD.TOPLAM", "FIAT")] == 6208 and sep[("ODMD.HAFIF_TICARI", "TOGG")] == 0
    for code in ("ODMD.TOPLAM", "ODMD.OTOMOBIL", "ODMD.HAFIF_TICARI"):
        assert sep.loc[code].drop("").sum() == sep[(code, "")]  # brands add up to the market
    names = dict(zip(df["entity_id"], df["entity_name"], strict=True))
    assert names[""] == "Toplam pazar" and names["KG_MOBILITY_SSANGYONG"] == "KG MOBILITY – SSANGYONG"
    assert names["MERCEDES_BENZ"] == "MERCEDES-BENZ"
    assert set(df["dataset_id"]) == {"perakende_satislar"} and set(df["unit"]) == {"count"}
    assert set(df["freq"]) == {"M"} and set(df["metric"]) == {"level"} and df["currency"].isna().all()
    assert not df["is_proxy"].any() and set(df["publication_lag_days"]) == {5}
    assert len(df) == 2 * 3 * 9
    manifest = (tmp_path / "odmd" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(manifest) == 2 and json.loads(manifest[-1])["url"].endswith("/202609/2026%20Eyl%C3%BCl.xlsx")
    assert next((tmp_path / "odmd").glob("*.xlsx")).stat().st_size > 0


@respx.mock
def test_domestic_and_import_columns(odmd):
    _serve(Site(last=(2026, 9)))
    df = odmd.fetch(FetchRequest.create("odmd", ["ODMD.OTOMOBIL.YERLI", "ODMD.OTOMOBIL.ITHAL", "ODMD.TOPLAM.YERLI"], date(2026, 9, 1), date(2026, 9, 30)))
    v = df.set_index(["series_id", "entity_id"])["value"]
    assert v[("ODMD.OTOMOBIL.YERLI", "TOGG")] == 6692 and v[("ODMD.OTOMOBIL.ITHAL", "TOGG")] == 0
    assert v[("ODMD.OTOMOBIL.YERLI", "")] == 17505 and v[("ODMD.OTOMOBIL.ITHAL", "")] == 6983
    assert v[("ODMD.TOPLAM.YERLI", "FORD")] == 2600


@respx.mock
def test_list_is_read_back_to_the_first_requested_month_and_ytd_entries_are_ignored(odmd):
    site = Site(last=(2026, 9), first=(2024, 11))
    _serve(site)
    df = odmd.fetch(FetchRequest.create("odmd", ["ODMD.TOPLAM"], date(2024, 11, 1), date(2024, 12, 31)))
    assert sorted(set(df["date"])) == [date(2024, 11, 30), date(2024, 12, 31)]
    assert site.paths.count("/web_2837_1/sortial.aspx") == 2  # Nov 2024 is the first entry of page 3
    assert len(site.downloads) == 2  # the "2025 Yılı (Ocak-Aralık)" entry is not a month


@respx.mock
def test_unpublished_months_and_months_before_2015_are_not_requested(odmd):
    site = Site(last=(2026, 9), first=(2026, 7))
    _serve(site)
    df = odmd.fetch(FetchRequest.create("odmd", ["ODMD.TOPLAM"], date(2026, 9, 1), date(2027, 3, 31)))
    assert sorted(set(df["date"])) == [date(2026, 9, 30)] and len(site.downloads) == 1
    site.paths.clear()
    assert odmd.fetch(FetchRequest.create("odmd", ["ODMD.TOPLAM"], date(2010, 1, 1), date(2014, 12, 31))).empty
    assert site.paths == []


@respx.mock
def test_missing_month_in_the_middle_of_the_list_is_skipped(odmd):
    site = Site(last=(2026, 9), first=(2026, 7))
    site.pages[0] = [item for item in site.pages[0] if item[0] != 202608]
    _serve(site)
    df = odmd.fetch(FetchRequest.create("odmd", ["ODMD.TOPLAM"], date(2026, 7, 1), date(2026, 9, 30)))
    assert sorted(set(df["date"])) == [date(2026, 7, 31), date(2026, 9, 30)]


@respx.mock
def test_old_header_without_marka_and_text_numbers(odmd):
    brands = [("A", 1200, 300, None, 10), ("B", None, 500, 20, None)]
    content = _set_cell(workbook(2021, 7, brands, marka=None), "1.200")
    _serve(Site(last=(2021, 7), first=(2021, 7), files={(2021, 7): content}))
    df = odmd.fetch(FetchRequest.create("odmd", ["ODMD.TOPLAM"], date(2021, 7, 1), date(2021, 7, 31)))
    assert df.set_index("entity_id")["value"].to_dict() == {"": 2030.0, "A": 1510.0, "B": 520.0}


def _set_cell(content: bytes, value) -> bytes:
    """The workbook with the first brand's domestic passenger car cell replaced."""
    book = openpyxl.load_workbook(io.BytesIO(content))
    book.active.cell(4, 2).value = value
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _fetch_one(odmd, content: bytes, year: int = 2026, month: int = 9):
    _serve(Site(last=(year, month), first=(year, month), files={(year, month): content}))
    return odmd.fetch(FetchRequest.create("odmd", ["ODMD.TOPLAM"], date(year, month, 1), date(year, month, 28)))


def _bump(i: int, by: int):
    def change(sums):
        sums[i] += by

    return change


@respx.mock
@pytest.mark.parametrize(
    ("content", "match"),
    [
        (workbook(2026, 9, total_change=_bump(8, 500)), r"otomobil ve hafif ticari \(TOPLAM\) markalarının toplamı"),
        (workbook(2026, 9, total_change=_bump(2, 500)), r"otomobil \(TOPLAM\) markalarının toplamı \(24\.488\)"),
        (workbook(2026, 9, total_change=_bump(4, -500)), r"hafif ticari \(ITHAL\)"),
        (workbook(2026, 8), "2026-08 dönemini içeriyor"),
        (workbook(2026, 9, title="PERAKENDE SATIŞLAR"), "dönem bulunamadı"),
        (_set_cell(workbook(2026, 9), "x"), "sayısal olmayan"),
        (_set_cell(workbook(2026, 9), True), "sayısal olmayan"),
        (workbook(2026, 9, [("A", 1, 1, None, None), ("a", 1, 1, None, None)]), "tekrarlanan marka"),
        (workbook(2026, 9, []), "marka satırı"),
        (b"PK\x03\x04 not really a zip", "okunamadı"),
    ],
)
def test_invalid_workbooks_are_rejected(odmd, content, match):
    with pytest.raises(UnexpectedResponse, match=match):
        _fetch_one(odmd, content)


def _without_rows(content: bytes, predicate) -> bytes:
    book = openpyxl.load_workbook(io.BytesIO(content))
    ws = book.active
    for i in reversed(range(1, ws.max_row + 1)):
        if predicate(ws.cell(i, 1).value, ws.cell(i, 2).value):
            ws.delete_rows(i)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _table(**totals) -> RetailTable:
    """A one-brand table whose brand row equals the totals; `totals` overrides (segment, column) totals."""
    base = {(s, c): v for s, (y, i) in {"OTOMOBIL": (60, 40), "HAFIF_TICARI": (20, 10), "TOPLAM": (80, 50)}.items()
            for c, v in (("YERLI", y), ("ITHAL", i), ("TOPLAM", y + i))}  # fmt: skip
    base.update({tuple(k.split("__")): v for k, v in totals.items()})
    table = RetailTable(2026, 9, labels={"A": "A"})
    table.totals = {s: {c: base[(s, c)] for c in COLUMNS} for s in SEGMENTS}
    table.values = {s: {c: {"A": base[(s, c)]} for c in COLUMNS} for s in SEGMENTS}
    return table


def test_validation_of_column_and_segment_totals():
    _validate(_table())
    with pytest.raises(UnexpectedResponse, match="hafif ticari yerli ve ithal toplamı uyuşmuyor"):
        _validate(_table(HAFIF_TICARI__TOPLAM=40))
    with pytest.raises(UnexpectedResponse, match="otomobil ve hafif ticari toplamı pazar toplamıyla"):
        _validate(_table(TOPLAM__YERLI=90, TOPLAM__TOPLAM=140))
    with pytest.raises(UnexpectedResponse, match="pazar toplamı yok"):
        _validate(_table(TOPLAM__YERLI=0, TOPLAM__ITHAL=0, TOPLAM__TOPLAM=0))


def test_layout_changes_are_reported():
    base = workbook(2026, 9)
    with pytest.raises(UnexpectedResponse, match="TOPLAM satırı yok"):
        parse_workbook(_without_rows(base, lambda a, b: a == "TOPLAM:"))
    with pytest.raises(UnexpectedResponse, match="başlığı bulunamadı"):
        parse_workbook(_without_rows(base, lambda a, b: b == "OTOMOBİL"))
    with pytest.raises(UnexpectedResponse, match="başlık yapısı"):
        parse_workbook(_without_rows(base, lambda a, b: b == "YERLİ"))
    with pytest.raises(UnexpectedResponse, match="tablo yok"):
        parse_workbook(_without_rows(base, lambda a, b: True))


def test_parse_workbook_reads_september_2026():
    table = parse_workbook(workbook(2026, 9))
    assert (table.year, table.month) == (2026, 9) and table.market_total == 35885
    assert table.totals["OTOMOBIL"] == {"YERLI": 17505, "ITHAL": 6983, "TOPLAM": 24488}
    assert table.values["HAFIF_TICARI"]["TOPLAM"]["KG_MOBILITY_SSANGYONG"] == 664


@respx.mock
def test_market_total_jump_against_previous_month_is_rejected(odmd):
    tiny = workbook(2026, 9, [("A", 1, 1, None, None)])
    _serve(Site(last=(2026, 9), first=(2026, 8), files={(2026, 9): tiny}))
    with pytest.raises(UnexpectedResponse, match="önceki aya göre tutarsız"):
        odmd.fetch(FetchRequest.create("odmd", ["ODMD.TOPLAM"], date(2026, 8, 1), date(2026, 9, 30)))


class Broken(Site):
    def __init__(self, path: str, response: httpx.Response, **kw):
        super().__init__(last=(2026, 9), first=(2026, 9), **kw)
        self.path, self.response = path, response

    def __call__(self, request):
        if urlsplit(str(request.url)).path == self.path:
            self.paths.append(self.path)
            return self.response
        return super().__call__(request)


@respx.mock
@pytest.mark.parametrize(
    ("path", "response", "error", "match"),
    [
        ("/web_2837_1/neuralnetwork.aspx", httpx.Response(403, text="Forbidden"), ProviderError, "HTTP 403"),
        ("/web_2837_1/neuralnetwork.aspx", httpx.Response(302, headers={"location": "/"}), UnexpectedResponse, "yönlendirme"),
        ("/web_2837_1/neuralnetwork.aspx", httpx.Response(200, json={}), UnexpectedResponse, "HTML değil"),
        ("/web_2837_1/neuralnetwork.aspx", httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"}), UnexpectedResponse, "kayıt bulunamadı"),
        ("/web_2837_1/wf_docudownload.aspx", httpx.Response(404), ProviderError, "HTTP 404"),
        ("/web_2837_1/wf_docudownload.aspx", httpx.Response(302, headers={"location": "https://evil.example/x.xlsx"}), UnexpectedResponse, "beklenmeyen"),
        ("/web_2837_1/wf_docudownload.aspx", httpx.Response(302, headers={"location": "/folders/2837/categorial1docs/1/x.xlsx"}), UnexpectedResponse, "beklenmeyen"),
        ("/web_2837_1/wf_docudownload.aspx", httpx.Response(302, headers={"location": "/folders/2837/categorial1docs/202609/../x.xlsx"}), UnexpectedResponse, "beklenmeyen"),
    ],
)
def test_bad_responses(odmd, path, response, error, match):
    _serve(Broken(path, response))
    with pytest.raises(error, match=match):
        odmd.fetch(FetchRequest.create("odmd", ["ODMD.TOPLAM"], date(2026, 9, 1), date(2026, 9, 30)))


@respx.mock
def test_download_without_session_cookie_is_reported(odmd):
    site = Site(last=(2026, 9), first=(2026, 9))
    respx.get(url__startswith=f"{HOST}/web_2837_1/neuralnetwork.aspx").mock(return_value=httpx.Response(200, text=listing(site.pages[0], 1), headers={"content-type": "text/html"}))
    respx.get(url__startswith=HOST).mock(side_effect=site)
    with pytest.raises(UnexpectedResponse, match="oturum"):
        odmd.fetch(FetchRequest.create("odmd", ["ODMD.TOPLAM"], date(2026, 9, 1), date(2026, 9, 30)))


@respx.mock
@pytest.mark.parametrize(
    ("response", "error", "match"),
    [
        (httpx.Response(404), ProviderError, "HTTP 404"),
        (httpx.Response(200, text="<!doctype html>", headers={"content-type": "text/html"}), UnexpectedResponse, "xlsx değil"),
    ],
)
def test_bad_file_responses(odmd, response, error, match):
    _serve(Broken("/folders/2837/categorial1docs/202609/2026%20Eyl%C3%BCl.xlsx", response))
    with pytest.raises(error, match=match):
        odmd.fetch(FetchRequest.create("odmd", ["ODMD.TOPLAM"], date(2026, 9, 1), date(2026, 9, 30)))


@respx.mock
@pytest.mark.parametrize("legacy", [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 xls", b"%PDF-1.3 ..."])
def test_months_published_as_xls_or_pdf_are_skipped(odmd, legacy):
    site = Site(last=(2017, 3), first=(2017, 1), files={(2017, 2): legacy})
    _serve(site)
    df = odmd.fetch(FetchRequest.create("odmd", ["ODMD.TOPLAM"], date(2017, 1, 1), date(2017, 3, 31)))
    assert sorted(set(df["date"])) == [date(2017, 1, 31), date(2017, 3, 31)] and len(site.downloads) == 3


def test_partial_total_row_before_the_real_one_is_ignored():
    book = openpyxl.load_workbook(io.BytesIO(workbook(2020, 12)))
    ws = book.active
    total = next(i for i in range(1, ws.max_row + 1) if ws.cell(i, 1).value == "TOPLAM:")
    ws.insert_rows(total)
    ws.cell(total, 1).value, ws.cell(total, 2).value, ws.cell(total, 3).value = "TOPLAM/TOTAL", 17505, 6983
    out = io.BytesIO()
    book.save(out)
    table = parse_workbook(out.getvalue())
    assert table.market_total == 35885 and table.totals["OTOMOBIL"]["TOPLAM"] == 24488
    assert "TOPLAM_TOTAL" not in table.labels


def test_unknown_series_and_transforms_rejected(odmd):
    with pytest.raises(InvalidInput, match="bilinmeyen seri"):
        odmd.fetch(FetchRequest.create("odmd", ["ODMD.YOK"], date(2026, 1, 1)))
    with pytest.raises(InvalidInput, match="desteklenmiyor"):
        odmd.fetch(FetchRequest.create("odmd", ["ODMD.TOPLAM"], date(2026, 1, 1), formula=1))


def test_list_series_filters_catalog(odmd):
    assert "ODMD.OTOMOBIL" in odmd.list_series()["series_id"].tolist()
    assert set(odmd.list_series("İTHAL")["series_id"]) == {"ODMD.OTOMOBIL.ITHAL"}


@respx.mock
def test_healthcheck_ok(odmd):
    last_month = date.today().replace(day=1) - timedelta(days=1)
    _serve(Site(last=(last_month.year, last_month.month), first=(last_month.year - 1, 1)))
    result = odmd.healthcheck()
    assert result.status is HealthStatus.OK, result.detail
    assert f"ODMD.TOPLAM son={last_month}" in result.detail and result.checked == ("ODMD.TOPLAM",)


@respx.mock
def test_healthcheck_fails_without_recent_observations(odmd):
    _serve(Site(last=(2020, 3), first=(2020, 1)))
    result = odmd.healthcheck()
    assert result.status is HealthStatus.FAIL and "gözlem yok" in result.detail


@respx.mock
def test_healthcheck_fails_on_stale_series(odmd, monkeypatch):
    monkeypatch.setattr("mlbooster.infrastructure.providers.odmd._HEALTH_LOOKBACK_DAYS", 400)
    old = date.today() - timedelta(days=400)
    _serve(Site(last=(old.year, old.month), first=(old.year - 1, 1)))
    result = odmd.healthcheck()
    assert result.status is HealthStatus.FAIL and "son gözlem" in result.detail


@respx.mock
def test_healthcheck_reports_provider_errors(odmd):
    respx.get(url__startswith=HOST).mock(return_value=httpx.Response(403, text="Forbidden"))
    result = odmd.healthcheck()
    assert result.status is HealthStatus.FAIL and "HTTP 403" in result.detail


def test_healthcheck_skipped_without_canaries(catalog):
    source = replace(catalog.source("odmd"), series=())
    assert OdmdProvider(source, http=SecureHttpClient()).healthcheck().status is HealthStatus.SKIPPED


def test_cli_market_share_from_odmd_source(monkeypatch, tmp_path):
    from mlbooster.interfaces import cli

    monkeypatch.setenv("MLBOOSTER_DATA_DIR", str(tmp_path))
    cli.container.cache_clear()
    site = Site(last=(2026, 9), first=(2026, 1))
    with respx.mock:
        _serve(site)
        args = ["market-share", "--source", "odmd", "--series", "ODMD.OTOMOBIL", "--start", "2026-01", "--end", "2026-12"]
        out = CliRunner().invoke(cli.app, [*args, "--json", str(tmp_path / "o.json")])
        assert out.exit_code == 0, out.output
        assert len(site.downloads) == 9
        again = CliRunner().invoke(cli.app, args)
        assert again.exit_code == 0 and len(site.downloads) == 9  # served from the local store
    cli.container.cache_clear()
    assert "Otomotiv Distribütörleri ve Mobilite Derneği" in out.output and "RENAULT" in out.output
    meta = json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))["meta"]
    assert meta["kind"] == "flow" and meta["source"]["series"] == "ODMD.OTOMOBIL" and meta["has_total"]
