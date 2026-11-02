import io
import zipfile
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
import openpyxl
import pytest
import respx

from mlbooster.application.dto import FetchRequest
from mlbooster.application.errors import ProviderError, ProviderUnavailable, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers import epdk_reports as reports
from mlbooster.infrastructure.providers.epdk import EpdkProvider, _check_continuity, parse_remote, validate
from mlbooster.infrastructure.providers.epdk_reports import Grid, MonthTable, number
from mlbooster.infrastructure.providers.registry import PROVIDERS
from mlbooster.infrastructure.storage.raw_store import RawStore

# The report list is trimmed from www.epdk.gov.tr (2026-11-02). Report files are built here with the real headers,
# captions and holder names of the 2025-12 Word report and the 2026 Excel annexes, with fewer rows.
FIXTURES = Path(__file__).parent / "fixtures" / "epdk"
BASE = "https://www.epdk.gov.tr"
PETROL_LIST = f"{BASE}/Detay/Icerik/3-0-104/petrolaylik-sektor-raporu"
LPG_LIST = f"{BASE}/Detay/Icerik/3-0-105/lpgaylik-sektor-raporlari"
DOWNLOAD = f"{BASE}/Detay/DownloadDocument"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
PETROL_HEADER = ["Lisans Sahibinin Ünvanı", "Lisans Türü", "Benzin Türleri", "Motorin Türleri", "Fuel Oil Türleri",
                 "Havacılık Yakıtları", "Denizcilik Yakıtları", "Gazyağı", "Diğer Ürünler", "Toplam", "Pay (%)"]  # fmt: skip
PETROL_ROWS = [
    ["PETROL OFİSİ A.Ş.", "Dağıtıcı", "122.942,75", "617.376,52", "2.721,90", "21.056,65", "3.072,41", "51,18", "0,00", "767.221,41", "50,00"],
    ["OPET PETROLCÜLÜK A.Ş.", "Dağıtıcı", "134.846,88", "434.694,53", "8.691,02", "0,00", "2.002,33", "26,90", "0,00", "580.261,66", "37,81"],
    ["THY OPET HAVACILIK YAKITLARI A.Ş.", "İhrakiye Teslimi", "0,00", "0,00", "0,00", "187.000,00", "0,00", "0,00", "0,00", "187.000,00", "12,19"],
    ["Toplam", "", "257.789,63", "1.052.071,05", "11.412,92", "208.056,65", "5.074,74", "78,08", "0,00", "1.534.483,07", "100,00"],
]  # fmt: skip


def _cell(value) -> str:
    text, span = value if isinstance(value, tuple) else (value, 1)
    props = f'<w:tcPr><w:gridSpan w:val="{span}"/></w:tcPr>' if span > 1 else ""
    return f"<w:tc>{props}<w:p><w:r><w:t>{escape(str(text))}</w:t></w:r></w:p></w:tc>"


def docx(*blocks, sdt: bool = False) -> bytes:
    """Minimal .docx: ("p", text) paragraphs and ("tbl", rows) tables; cells may be (text, grid span)."""
    body = ""
    for kind, value in blocks:
        if kind == "p":
            body += f"<w:p><w:r><w:t>{escape(value)}</w:t></w:r></w:p>"
        else:
            body += "<w:tbl>" + "".join("<w:tr>" + "".join(map(_cell, row)) + "</w:tr>" for row in value) + "</w:tbl>"
    if sdt:
        body = f"<w:sdt><w:sdtContent>{body}</w:sdtContent></w:sdt>"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
    return out.getvalue()


def xlsx(sheets: dict[str, list[list]]) -> bytes:
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def petrol_docx(month: str = "Aralık 2025", rows=PETROL_ROWS) -> bytes:
    return docx(
        ("p", "Tablo 3.1. Aralık 2025 Dönemi İllere Göre Yurtiçi Satış Miktarları"),
        ("tbl", [["İl", "Benzin Türleri", "Toplam"], ["İSTANBUL", "119.961,47", "465.653,76"]]),
        # the year-to-date table comes first here to prove the caption filter (in the reports it follows 3.2)
        ("p", f"Tablo 3.3. Ocak-{month} Dönemi Lisans Sahibi Şirketlere Göre Yurtiçi Satış Miktarları"),
        ("tbl", [PETROL_HEADER, ["PETROL OFİSİ A.Ş.", "Dağıtıcı", *["1,00"] * 9], ["Toplam", "", *["1,00"] * 9]]),
        ("p", f"Tablo 3.2. {month} Dönemi Lisans Sahibi Şirketlere Göre Yurtiçi Satış Miktarları"),
        ("tbl", [PETROL_HEADER, *rows]),
    )


PETROL_XLSX_HEADER = ["Lisans Sahibinin Ünvanı", "Lisans Türü", "Benzin Türleri", "Motorin Türleri", "Fuel Oil Türleri",
                      "Havacılık Yakıtları", "Denizcilik Yakıtları", "Gazyağı", "Diğer Ürünler", "Genel Toplam", "Pay (%)"]  # fmt: skip


def petrol_xlsx() -> bytes:
    return xlsx({
        "İçindekiler": [[None, "Tablo 12: Ocak 2026 Dönemi Lisans Sahibi Şirketlere Göre Yurtiçi Satışlar"]],
        "Tablo 12": [
            [None], ["Tablo 12: Ocak 2026 Dönemi Lisans Sahibi Şirketlere Göre Yurtiçi Satışlar"], [None],
            PETROL_XLSX_HEADER,
            ["PETROL OFİSİ ANONİM ŞİRKETİ", "Dağıtıcı", 104871.3, 494259.6, 3586.9, 16419.7, 1891.4, 48.1, 0, 621077.0, 50.0],
            [None, "İhrakiye Teslimi", 0, 0, 0, 13723.4, 0, 0, 0, 13723.4, 1.1],  # second licence, merged name cell
            ["OPET PETROLCÜLÜK ANONİM ŞİRKETİ", "Dağıtıcı", 115783.5, 349216.4, 8709.2, 0, 206.1, 50.9, 0, 473966.1, 38.2],
            ["SHELL & TURCAS PETROL ANONİM ŞİRKETİ", "Dağıtıcı", 129807.6, 0, 0, 0, 0, 0, 0, 129807.6, 10.7],
            ["Toplam", None, 350462.4, 843476.0, 12296.1, 30143.1, 2097.5, 99.0, 0, 1238574.1, "100"],
        ],
        "Tablo 13": [
            ["Tablo 13: Lisans Sahibi Şirketlere Göre Kümülatif Yurtiçi Satışlar (Ocak-Ocak 2026)"],
            PETROL_XLSX_HEADER, ["Toplam", None, *[1] * 9],
        ],
    })  # fmt: skip


LPG_HEAD = ["Lisans Sahibinin Unvanı", ("Tüplü", 2), ("Dökme", 2), ("Otogaz", 2), ("Toplam", 2)]
LPG_SUB = ["", *["Satış (ton)", "Pay (%)"] * 4]


def lpg_docx() -> bytes:
    return docx(
        ("p", "Tablo 3.1: Aralık 2025 Dönemi Dağıtıcılar Arası Toptan LPG Satışları"),
        ("tbl", [["Satış Yapan", ("Tüplü", 2), ("Dökme", 2), ("Otogaz", 2), ("Toplam", 2)], ["Lisans Sahibinin Unvanı", *LPG_SUB[1:]]]),
        ("p", "Tablo 3.5: Aralık 2025 Döneminde LPG Satışlarının Dağıtıcılara ve Ürün Türüne Göre Dağılımı"),
        ("tbl", [
            LPG_HEAD, LPG_SUB,
            ["AYGAZ A.Ş.", "19.409,19", "100,00", "3.348,19", "100,00", "63.509,54", "51,42", "86.266,91", "58,98"],
            ["PETROL OFİSİ A.Ş.", "0,00", "0,00", "0,00", "0,00", "60.000,00", "48,58", "60.000,00", "41,02"],
            ["Toplam", "19.409,19", "100", "3.348,19", "100", "123.509,54", "100", "146.266,92", "100"],
        ]),
        sdt=True,
    )  # fmt: skip


def lpg_xlsx() -> bytes:
    rows = [
        [None], ["Tablo 12: Lisans Sahiplerine Göre LPG Satışlarının Karşılaştırması Ocak 2025 - Ocak 2026"],
        ["Lisans Sahibi Unvanı", "", "Ocak 2025", None, "Ocak 2026", None, ""],
        [None, "Ürün Türü", "Miktar(Ton)", "Pay(%)", "Miktar(Ton)", "Pay(%)", "Değişim (%)"],
        ["AYGAZ ANONİM ŞİRKETİ", "Dökme", 3300.2, 3.4, 3910.5, 3.9, 18.5],
        [None, "Otogaz", 73141.6, 74.8, 75146.5, 75.8, 2.7],
        [None, "Tüplü", 21357.3, 21.8, 20088.8, 20.3, -5.9],
        [None, "Firma Toplamı", 97799.1, "100", 99145.8, "100", 1.4],
        ["PETROL OFİSİ ANONİM ŞİRKETİ", "Dökme", 0, 0, 0, 0, "-"],
        [None, "Otogaz", 79842.9, 100, 78995.9, 100, -1.1],
        [None, "Tüplü", 0, 0, 0, 0, "-"],
        [None, "Firma Toplamı", 79842.9, "100", 78995.9, "100", -1.1],
        ["TOPLAM", "", 177642.0, "100", 178141.7, "100", 0.3],
    ]  # fmt: skip
    cumulative = [["Tablo 11: Dağıtıcı ve Ürün Türüne Göre LPG Satışı Ocak 2026 (ton)"], ["", "Tüplügaz", None, "Dökmegaz", None, "Otogaz"],
                  ["Lisans Sahibinin Unvanı", "Satış(ton)", "Pay(%)", "Satış(ton)", "Pay(%)", "Satış(ton)", "Pay(%)"]]  # fmt: skip
    return xlsx({"Tablo 11": cumulative, "Tablo 12": rows})


def _listing(entries: dict[str, list[tuple[str, str]]]) -> str:
    """Report list markup as on the site: an entry title, then its files (icon kind, document id)."""
    html = "<ul>"
    for title, docs in entries.items():
        html += f'<li class="accordion-pop"><a data-toggle="collapse" data-target="#x">{title}</a><br/>'
        html += "".join(f'<a href="/Detay/DownloadDocument?id={i}" title="t"><img src =\'/Content/img/{k}.png\'/></a>' for k, i in docs)
        html += "</li>"
    return html + "</ul>"


def _html(text: str) -> httpx.Response:
    return httpx.Response(200, text=text, headers={"content-type": "text/html; charset=utf-8"})


def _file(content: bytes) -> httpx.Response:
    return httpx.Response(200, content=content, headers={"content-type": "application/octet-stream"})


def _downloads(files: dict[str, bytes]):
    def handler(request: httpx.Request) -> httpx.Response:
        return _file(files[request.url.params["id"]])

    return handler


@pytest.fixture
def epdk(catalog):
    provider = EpdkProvider(catalog.source("epdk"), http=SecureHttpClient(rps=None, max_retries=1, sleep=lambda _: None))
    yield provider
    provider.close()


def test_registered_and_catalog_points_to_adapter(catalog):
    source = catalog.source("epdk")
    assert PROVIDERS[source.provider] is EpdkProvider
    assert source.canaries and all(parse_remote(s.remote) for s in source.series)


def test_refuses_untrusted_host(catalog):
    hijacked = replace(catalog.source("epdk"), base_url="https://evil.example/")
    with pytest.raises(ProviderError, match="yalnızca"):
        EpdkProvider(hijacked, http=SecureHttpClient())


def test_parse_remote():
    assert parse_remote("petrol/MOTORIN") == ("petrol", "MOTORIN")
    assert parse_remote("lpg/OTOGAZ") == ("lpg", "OTOGAZ")


@pytest.mark.parametrize("remote", ["petrol/OTOGAZ", "lpg/BENZIN", "dogalgaz/TOPLAM", "petrol/motorin", "petrol", "petrol/PAY"])
def test_parse_remote_rejects_malformed(remote):
    with pytest.raises(InvalidInput, match="remote"):
        parse_remote(remote)


@pytest.mark.parametrize(
    ("raw", "key", "name"),
    [
        ("PETROL OFİSİ A.Ş.", "PETROL_OFISI", "PETROL OFİSİ"),
        ("PETROL OFİSİ ANONİM ŞİRKETİ", "PETROL_OFISI", "PETROL OFİSİ"),
        ("SHELL & TURCAS PETROL A.Ş.", "SHELL_TURCAS", "SHELL & TURCAS"),
        ("SHELL&TURCAS PETROL A.Ş.", "SHELL_TURCAS", "SHELL & TURCAS"),
        ("AYTEMİZ AKARYAKIT DAĞ. A.Ş.", "AYTEMIZ_AKARYAKIT", "AYTEMİZ AKARYAKIT"),
        ("AYTEMİZ AKARYAKIT DAĞITIM ANONİM ŞİRKETİ", "AYTEMIZ_AKARYAKIT", "AYTEMİZ AKARYAKIT"),
        ("TECO PETROL. SANAYİ VE TİCARET A.Ş.", "TECO_PETROLCULUK", "TECO PETROLCÜLÜK"),
        ("SİYAM PETROLCÜLÜK SANAYİ VE TİCARET ANONİM ŞİRKETİ (S OIL)", "SIYAM_PETROLCULUK", "SİYAM PETROLCÜLÜK"),
        ("AYGAZ A.Ş.", "AYGAZ", "AYGAZ"),
        ("AYTEMİZ AKAR. A.Ş.", "AYTEMIZ_AKARYAKIT", "AYTEMİZ AKARYAKIT"),
        ("LİKİTGAZ DAĞ", "LIKITGAZ_DAGITIM", "LİKİTGAZ DAĞITIM"),  # cut title, dot lost
        ("HÜRGAZ TİC. VE SAN. A.Ş.", "HURGAZ_TICARET", "HÜRGAZ TİCARET"),
        ("PET GAZ A.Ş.", "PET_GAZ", "PET GAZ"),
        ("İPRAGAZ ANONİM ŞİRKETİ", "IPRAGAZ", "İPRAGAZ"),
        ("BALPET PETROL ÜRÜNLERİ TAŞIMACILIK SANAYİ VE TİC.A.Ş", "BALPET_PETROL", "BALPET PETROL"),
        ("ERDEM PETROL ÜRÜNLERİ SANAYİ PAZARLAMA LTD. ŞTİ.", "ERDEM_PETROL", "ERDEM PETROL"),
        ("  Euroil   enerji dağıtım a.ş. ", "EUROIL_ENERJI", "EUROİL ENERJİ"),
        (" - ", "", ""),
    ],
)
def test_holder_keys_are_stable_across_report_formats(raw, key, name):
    assert reports.holder(raw) == (key, name)


@pytest.mark.parametrize(
    ("cell", "value"),
    [("1.234.567,89", 1234567.89), ("0,000", 0.0), ("-38.067,81", -38067.81), ("100", 100.0), ("12.5", 12.5),
     ("41,08", 41.08), (" 1.234 ", 1234.0), (617376.52, 617376.52), (3, 3.0), ("", None), ("-", None), (None, None)],
)  # fmt: skip
def test_number_reads_turkish_and_excel_cells(cell, value):
    assert number(cell) == value


@pytest.mark.parametrize("cell", ["abc", "1,2,3", True])
def test_number_rejects_text(cell):
    with pytest.raises(UnexpectedResponse):
        number(cell)


@pytest.mark.parametrize(
    ("caption", "cumulative"),
    [("Tablo 3.2. Aralık 2025 Dönemi Lisans Sahibi ...", False), ("Tablo 3.3. Ocak-Aralık 2025 Dönemi ...", True),
     ("Tablo 13: ... Kümülatif Yurtiçi Satışlar", True), ("Tablo 15: Lisans ... Kümütalif", True),
     ("Tablo 12: ... Ocak 2025 - Ocak 2026", False), ("Ocak - Haziran 2019 Dönemi", True),
     ("Tablo 3.6: 2016 yılının ilk 7 ayındaki toplam satışın dağıtıcılara ve ürünlere göre dağılımı", True)],
)  # fmt: skip
def test_cumulative_captions(caption, cumulative):
    assert reports.is_cumulative(caption) is cumulative


def test_parse_listing_of_the_real_page():
    listing = reports.parse_listing((FIXTURES / "petrol_listing.html").read_text(encoding="utf-8"))
    assert sorted(listing) == [(2015, 12), (2025, 12), (2026, 1), (2026, 2)]  # year headers and "İlk Dokuz Ay" skipped
    assert [(d.kind, d.doc_id) for d in listing[(2026, 1)]] == [("pdf", "O3+H6EouTaI="), ("excel", "cTHWx7K9DI8=")]
    assert [d.kind for d in listing[(2025, 12)]] == ["word", "excel", "excel"]
    assert listing[(2025, 12)][1].title == "Ek1_İl Bazında Petrol Ürünü Teslimleri (ton)"


def test_parse_listing_ignores_bad_ids_and_untyped_links():
    html = '<a data-toggle="collapse">Petrol Piyasası Sektör Raporu Mart 2020</a><a href="/Detay/DownloadDocument?id=bad id!"><img src="/Content/img/pdf.png"/></a><a href="/Detay/DownloadDocument?id=abcd"></a>'
    assert reports.parse_listing(html) == {}


def test_petrol_table_from_word_report():
    table = reports.petrol_sales(reports.read_docx(petrol_docx()))
    assert table.totals["MOTORIN"] == pytest.approx(1052071.05)
    assert table.entities["PETROL_OFISI"] == ("PETROL OFİSİ", pytest.approx(
        {"BENZIN": 122942.75, "MOTORIN": 617376.52, "FUEL_OIL": 2721.9, "HAVACILIK": 21056.65, "DENIZCILIK": 3072.41,
         "GAZYAGI": 51.18, "DIGER": 0.0, "TOPLAM": 767221.41}))  # fmt: skip
    assert [s for _, s in table.shares["TOPLAM"]] == [50.0, 37.81, 12.19]
    validate(table, {"BENZIN", "MOTORIN", "TOPLAM"}, "test")


def test_petrol_table_from_2016_word_report_headers():
    header = ["ŞİRKET ÜNVANI", "LİSANS TİPİ", "BENZİN TÜRLERİ", "MOTORİN TÜRLERİ", "FUEL OİL TÜRLERİ", "HAVACILIK YAKITLARI",
              "DENİZCİLİK YAKITLARI", "GAZYAĞI", "DİĞER ÜRÜNLER", "GENEL TOPLAM", "PAYI (%)"]  # fmt: skip
    content = docx(
        ("p", "Tablo 3.2. 2016 Yılı Ocak Ayında Lisans Sahibi Şirketlere Göre Yurtiçi Satış Miktarları (ton)"),
        ("tbl", [header, ["OMV PETROL OFİSİ ANONİM ŞİRKETİ", "Dağıtıcı Lisansı", *["2,000"] * 8, "100,00"], header, ["TOPLAM", "", *["2,000"] * 8, "100"]]),
    )  # fmt: skip
    table = reports.petrol_sales(reports.read_docx(content))
    assert list(table.entities) == ["OMV_PETROL"] and table.totals["TOPLAM"] == 2.0


def test_petrol_table_from_excel_annex_joins_second_licence_rows():
    table = reports.petrol_sales(reports.read_xlsx(petrol_xlsx()))
    assert set(table.entities) == {"PETROL_OFISI", "OPET_PETROLCULUK", "SHELL_TURCAS"}
    assert table.entities["PETROL_OFISI"][1]["HAVACILIK"] == pytest.approx(16419.7 + 13723.4)
    assert table.totals["TOPLAM"] == pytest.approx(1238574.1)
    validate(table, {"TOPLAM", "BENZIN", "MOTORIN"}, "test")


def test_petrol_table_errors():
    nameless = docx(("p", "Ocak 2026 Dönemi Lisans Sahibi Şirketlere Göre Yurtiçi Satışlar"),
                    ("tbl", [PETROL_HEADER, ["", "Dağıtıcı", *["1,00"] * 9]]))  # fmt: skip
    with pytest.raises(UnexpectedResponse, match="adı olmayan"):
        reports.petrol_sales(reports.read_docx(nameless))
    no_total = docx(("p", "Ocak 2026 Dönemi Lisans Sahibi Şirketlere Göre Yurtiçi Satışlar"), ("tbl", [PETROL_HEADER, PETROL_ROWS[0]]))
    with pytest.raises(UnexpectedResponse, match="toplam satırı"):
        reports.petrol_sales(reports.read_docx(no_total))
    renamed = docx(("p", "Ocak 2026 Dönemi Lisans Sahibi Şirketlere Göre Yurtiçi Satışlar"),
                   ("tbl", [["Lisans Sahibinin Ünvanı", "Lisans Türü", "Benzin", "Motorin", "Hepsi"]]))  # fmt: skip
    with pytest.raises(UnexpectedResponse, match="kolonları değişmiş"):
        reports.petrol_sales(reports.read_docx(renamed))
    assert reports.petrol_sales(reports.read_docx(docx(("p", "başka bir rapor")))) is None


def test_lpg_table_from_word_report_with_spanned_header():
    table = reports.lpg_sales(reports.read_docx(lpg_docx()), 2025, 12)
    assert table.totals == pytest.approx({"TUPLU": 19409.19, "DOKME": 3348.19, "OTOGAZ": 123509.54, "TOPLAM": 146266.92})
    assert table.entities["AYGAZ"] == ("AYGAZ", pytest.approx({"TUPLU": 19409.19, "DOKME": 3348.19, "OTOGAZ": 63509.54, "TOPLAM": 86266.91}))
    assert table.shares["OTOGAZ"] == [(63509.54, 51.42), (60000.0, 48.58)]
    validate(table, {"TUPLU", "DOKME", "OTOGAZ", "TOPLAM"}, "test")


def test_lpg_table_from_older_word_reports():
    """2016: first column "DAĞITICI", caption "... ve ürünlere göre"; 2017-2018: "Tüplügaz", total row "Genel Toplam"."""
    head = ["DAĞITICI", ("Tüplügaz", 2), ("Dökmegaz", 2), ("Otogaz", 2), ("Toplam", 2)]
    rows = [head, LPG_SUB, ["AYGAZ", "25.953", "100", "2.837", "100", "55.802", "100", "84.592", "100"],
            ["Genel Toplam", "25.953", "100", "2.837", "100", "55.802", "100", "84.592", "100"]]  # fmt: skip
    content = docx(
        ("p", "Tablo 3.5: 2016 yılının ilk 7 ayındaki toplam satışın dağıtıcılara ve ürünlere göre dağılımı"), ("tbl", rows[:2] + [["TOPLAM", *["1"] * 8]]),
        ("p", "Tablo 3.4: Ocak 2016 döneminde satışların dağıtıcılara ve ürünlere göre dağılımı"), ("tbl", rows),
    )  # fmt: skip
    table = reports.lpg_sales(reports.read_docx(content), 2016, 1)
    assert table.totals == {"TUPLU": 25953.0, "DOKME": 2837.0, "OTOGAZ": 55802.0, "TOPLAM": 84592.0}
    assert list(table.entities) == ["AYGAZ"]


def test_lpg_table_from_excel_annex_long_format():
    table = reports.lpg_sales(reports.read_xlsx(lpg_xlsx()), 2026, 1)
    assert table.entities["AYGAZ"][1] == pytest.approx({"DOKME": 3910.5, "OTOGAZ": 75146.5, "TUPLU": 20088.8, "TOPLAM": 99145.8})
    assert table.totals == pytest.approx({"TUPLU": 20088.8, "DOKME": 3910.5, "OTOGAZ": 154142.4, "TOPLAM": 178141.7})
    validate(table, {"OTOGAZ", "TOPLAM"}, "test")
    assert reports.lpg_sales(reports.read_xlsx(lpg_xlsx()), 2026, 2) is None  # another month's annex


def test_lpg_long_table_errors():
    rows = reports.read_xlsx(lpg_xlsx())[1].rows
    broken = [r[:] for r in rows]
    broken[7][4] = 1.0  # Firma Toplamı no longer the sum of the products
    with pytest.raises(UnexpectedResponse, match="firma toplamını"):
        reports.lpg_sales([Grid("", broken)], 2026, 1)
    with pytest.raises(UnexpectedResponse, match="firma toplamı eksik"):
        reports.lpg_sales([Grid("", rows[:6] + rows[-1:])], 2026, 1)
    with pytest.raises(UnexpectedResponse, match="toplam satırı"):
        reports.lpg_sales([Grid("", rows[:-1])], 2026, 1)
    with pytest.raises(UnexpectedResponse, match="kolonları"):
        reports.lpg_sales([Grid("", rows[:3] + [["", "Tür"]])], 2026, 1)


def test_lpg_wide_table_errors():
    grid = reports.read_docx(lpg_docx())[1]
    with pytest.raises(UnexpectedResponse, match="toplam satırı"):
        reports.lpg_sales([Grid(grid.caption, grid.rows[:-1])], 2025, 12)
    with pytest.raises(UnexpectedResponse, match="kolonları"):
        reports.lpg_sales([Grid(grid.caption, [grid.rows[0], [""] * 9])], 2025, 12)


def test_validate_shares():
    table = MonthTable({"A": ("A", {"TOPLAM": 60.0}), "B": ("B", {"TOPLAM": 39.0})}, {"TOPLAM": 100.0})
    with pytest.raises(UnexpectedResponse, match=r"%99.00"):
        validate(table, {"TOPLAM"}, "x")
    table.entities["B"] = ("B", {"TOPLAM": 39.6})
    validate(table, {"TOPLAM"}, "x")
    table.shares = {"TOPLAM": [(60.0, 60.0), (39.6, 38.0)]}
    with pytest.raises(UnexpectedResponse, match="yayımlanan payların"):
        validate(table, {"TOPLAM"}, "x")
    with pytest.raises(UnexpectedResponse, match="toplam yok"):
        validate(table, {"BENZIN"}, "x")
    zero = MonthTable({"A": ("A", {"GAZYAGI": 1.0})}, {"GAZYAGI": 0.0})
    with pytest.raises(UnexpectedResponse, match="sıfır"):
        validate(zero, {"GAZYAGI"}, "x")
    validate(MonthTable({}, {"GAZYAGI": 0.0}), {"GAZYAGI"}, "x")


def test_a_total_row_contradicting_its_table_and_shares_is_replaced(caplog):
    """LPG 2023-10: the otogaz total row was ~6 % too high while every published share fits the holders' sum."""
    shares = {"OTOGAZ": [(64828.92, 70.0), (27781.08, 30.0)]}
    table = MonthTable({"A": ("A", {"OTOGAZ": 64828.92}), "B": ("B", {"OTOGAZ": 27781.08})}, {"OTOGAZ": 98000.0}, shares)
    validate(table, {"OTOGAZ"}, "lpg 2023/10")
    assert table.totals["OTOGAZ"] == pytest.approx(92610.0) and "disagrees" in caplog.text
    table = MonthTable(table.entities, {"OTOGAZ": 98000.0}, {"OTOGAZ": [(64828.92, 66.15), (27781.08, 33.85)]})
    with pytest.raises(UnexpectedResponse, match="100±0,5"):  # shares computed on the printed total: rows are missing
        validate(table, {"OTOGAZ"}, "x")


def test_continuity_check():
    import pandas as pd

    frame = pd.DataFrame({"series_id": ["S"] * 3, "entity_id": [""] * 3, "date": [date(2026, 1, 31), date(2026, 2, 28), date(2026, 4, 30)],
                          "value": [100.0, 120.0, 1000.0]})  # fmt: skip
    _check_continuity(frame)  # March is missing: no comparison across the gap
    frame.loc[1, "value"] = 20.0
    with pytest.raises(UnexpectedResponse, match="kat değişti"):
        _check_continuity(frame)


@pytest.mark.parametrize(
    ("content", "match"),
    [
        (b"PK\x03\x04 not a zip", "bozuk"),
        (docx(("p", "x")).replace(b"word/document.xml", b"word/documenx.xml"), None),  # zip without a Word/Excel part
    ],
)
def test_office_kind_of_odd_archives(content, match):
    if match:
        with pytest.raises(UnexpectedResponse, match=match):
            reports.office_kind(content)
    else:
        assert reports.office_kind(content) is None


def test_office_files_are_checked_before_parsing(monkeypatch):
    assert reports.read_office(b"\xd0\xcf\x11\xe0 old binary .doc") is None
    assert reports.read_office(b"%PDF-1.7") is None
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY a "aaaa">]><w:document xmlns:w="w"/>')
    with pytest.raises(UnexpectedResponse, match="DTD"):
        reports.read_office(out.getvalue())
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("word/document.xml", "<w:document")
    with pytest.raises(UnexpectedResponse, match="çözümlenemedi"):
        reports.read_office(out.getvalue())
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("xl/workbook.xml", "not a workbook")
    with pytest.raises(UnexpectedResponse, match="Excel"):
        reports.read_office(out.getvalue())
    monkeypatch.setattr(reports, "MAX_UNZIPPED_BYTES", 10)
    with pytest.raises(UnexpectedResponse, match="boyut"):
        reports.read_office(petrol_docx())


def test_excel_sheet_row_limit(monkeypatch):
    monkeypatch.setattr(reports, "MAX_ROWS_PER_SHEET", 3)
    with pytest.raises(UnexpectedResponse, match="satır sınırını"):
        reports.read_xlsx(petrol_xlsx())


@respx.mock
def test_fetch_reads_word_then_excel_and_maps_holders(epdk, tmp_path):
    epdk._raw_store = RawStore(tmp_path)
    respx.get(PETROL_LIST).mock(return_value=_html(_listing({
        "2025 Yılı Petrol Piyasası Aralık Ayı Sektör Raporu": [("excel", "ilbazinda"), ("word", "rapor12"), ("pdf", "pdf12")],
        "2026 Yılı Petrol Piyasası Ocak Ayı Sektör Raporu": [("pdf", "pdf01"), ("excel", "ek01")],
    })))  # fmt: skip
    route = respx.get(url__startswith=DOWNLOAD).mock(side_effect=_downloads({"rapor12": petrol_docx(), "ek01": petrol_xlsx()}))
    df = epdk.fetch(FetchRequest.create("epdk", ["EPDK.PETROL.MOTORIN", "EPDK.PETROL.TOPLAM"], date(2025, 12, 1), date(2026, 3, 31)))
    assert [c.request.url.params["id"] for c in route.calls] == ["rapor12", "ek01"]  # Word first; PDFs never fetched
    motorin = df[df["series_id"] == "EPDK.PETROL.MOTORIN"]
    assert motorin[motorin["entity_id"] == ""].set_index("date")["value"].to_dict() == pytest.approx(
        {date(2025, 12, 31): 1052071.05, date(2026, 1, 31): 843476.0})
    dec = motorin[motorin["date"] == date(2025, 12, 31)].set_index("entity_id")
    assert set(dec.index) == {"", "PETROL_OFISI", "OPET_PETROLCULUK"}  # holders without motorin sales are left out
    assert dec.loc["PETROL_OFISI", "entity_name"] == "PETROL OFİSİ" and dec.loc["", "entity_name"] == "Toplam"
    jan = df[(df["series_id"] == "EPDK.PETROL.TOPLAM") & (df["date"] == date(2026, 1, 31))].set_index("entity_id")["value"]
    assert jan["PETROL_OFISI"] == pytest.approx(621077.0 + 13723.4)
    assert set(df["dataset_id"]) == {"petrol_aylik_sektor_raporu"} and set(df["unit"]) == {"ton"}
    assert not df["is_proxy"].any() and set(df["metric"]) == {"level"} and set(df["publication_lag_days"]) == {60}
    assert len(list(tmp_path.glob("epdk/*.bin"))) == 2  # both files, under their content hash


@respx.mock
def test_fetch_lpg_and_renamed_holders(epdk):
    respx.get(LPG_LIST).mock(return_value=_html(_listing({
        "2025 Yılı Sıvılaştırılmış Petrol Gazları (LPG) Piyasası Aralık Ayı Sektör Raporu": [("word", "lpg12")],
        "2026 Yılı Sıvılaştırılmış Petrol Gazları (LPG) Piyasası Ocak Ayı Sektör Raporu": [("excel", "lpg01")],
    })))  # fmt: skip
    respx.get(url__startswith=DOWNLOAD).mock(side_effect=_downloads({"lpg12": lpg_docx(), "lpg01": lpg_xlsx()}))
    df = epdk.fetch(FetchRequest.create("epdk", ["EPDK.LPG.OTOGAZ"], date(2025, 12, 1), date(2026, 1, 31)))
    assert set(df["entity_id"]) == {"", "AYGAZ", "PETROL_OFISI"}
    assert set(df["dataset_id"]) == {"lpg_aylik_sektor_raporu"}


@respx.mock
def test_old_names_map_to_the_current_holder(epdk):
    header = ["Şirket Ünvanı", "Lisans Tipi", "Benzin Türleri", "Motorin Türleri", "Genel Toplam"]
    old = docx(("p", "Tablo 3.2. 2016 Yılı Ocak Ayında Lisans Sahibi Şirketlere Göre Yurtiçi Satış Miktarları (ton)"),
               ("tbl", [header, ["OMV PETROL OFİSİ ANONİM ŞİRKETİ", "Dağıtıcı Lisansı", "1,0", "2,0", "3,0"], ["TOPLAM", "", "1,0", "2,0", "3,0"]]))  # fmt: skip
    respx.get(PETROL_LIST).mock(return_value=_html(_listing({"Petrol Piyasası Sektör Raporu Ocak 2016": [("word", "eski")]})))
    respx.get(url__startswith=DOWNLOAD).mock(return_value=_file(old))
    df = epdk.fetch(FetchRequest.create("epdk", ["EPDK.PETROL.TOPLAM"], date(2016, 1, 1), date(2016, 1, 31)))
    assert df.set_index("entity_id").loc["PETROL_OFISI", "entity_name"] == "PETROL OFİSİ"


@respx.mock
def test_lpg_starts_in_2017(epdk):
    listing = respx.get(LPG_LIST).mock(return_value=_html(_listing({"2016 Ocak LPG Raporu": [("word", "lpg16")]})))
    assert epdk.fetch(FetchRequest.create("epdk", ["EPDK.LPG.TOPLAM"], date(2016, 1, 1), date(2016, 12, 31))).empty
    assert not listing.called


@respx.mock
def test_binary_word_files_are_skipped_for_the_next_file(epdk):
    respx.get(PETROL_LIST).mock(return_value=_html(_listing({"Petrol Piyasası Sektör Raporu Aralık 2025": [("word", "ikili"), ("excel", "ekdosya")]})))
    respx.get(url__startswith=DOWNLOAD).mock(side_effect=_downloads({"ikili": b"\xd0\xcf\x11\xe0 binary", "ekdosya": petrol_docx()}))
    df = epdk.fetch(FetchRequest.create("epdk", ["EPDK.PETROL.BENZIN"], date(2025, 12, 1), date(2025, 12, 31)))
    assert df[df["entity_id"] == ""]["value"].tolist() == pytest.approx([257789.63])


@respx.mock
def test_months_before_2016_and_unpublished_months_are_not_requested(epdk):
    listing = respx.get(PETROL_LIST).mock(return_value=_html((FIXTURES / "petrol_listing.html").read_text(encoding="utf-8")))
    downloads = respx.get(url__startswith=DOWNLOAD)
    assert epdk.fetch(FetchRequest.create("epdk", ["EPDK.PETROL.TOPLAM"], date(2015, 1, 1), date(2015, 12, 31))).empty
    assert not listing.called  # nothing before 2016 is read
    assert epdk.fetch(FetchRequest.create("epdk", ["EPDK.PETROL.TOPLAM"], date(2026, 3, 1), date(2026, 5, 31))).empty
    assert listing.call_count == 1 and not downloads.called


@pytest.mark.parametrize(
    ("listing", "download", "error", "match"),
    [
        (httpx.Response(302, headers={"location": "/"}), None, UnexpectedResponse, "yönlendirme"),
        (httpx.Response(404), None, ProviderError, "HTTP 404"),
        (httpx.Response(200, json={}), None, UnexpectedResponse, "HTML değil"),
        (_html("<html>bakım</html>"), None, UnexpectedResponse, "aylık rapor bulunamadı"),
        (None, _html("<html>hata</html>"), UnexpectedResponse, "HTML döndürdü"),
        (None, _file(docx(("p", "boş rapor"))), UnexpectedResponse, "satış tablosu bulunamadı"),
        (None, httpx.Response(503), ProviderUnavailable, "503"),
    ],
)
@respx.mock
def test_bad_responses(epdk, listing, download, error, match):
    respx.get(PETROL_LIST).mock(return_value=listing or _html(_listing({"Petrol Piyasası Sektör Raporu Aralık 2025": [("word", "rapor")]})))
    respx.get(url__startswith=DOWNLOAD).mock(return_value=download)
    with pytest.raises(error, match=match):
        epdk.fetch(FetchRequest.create("epdk", ["EPDK.PETROL.TOPLAM"], date(2025, 12, 1), date(2025, 12, 31)))


@respx.mock
def test_shares_that_do_not_add_up_are_rejected(epdk):
    rows = [r[:] for r in PETROL_ROWS]
    rows[0][9] = "700.000,00"  # Petrol Ofisi's total no longer matches the published total
    respx.get(PETROL_LIST).mock(return_value=_html(_listing({"Petrol Piyasası Sektör Raporu Aralık 2025": [("word", "rapor")]})))
    respx.get(url__startswith=DOWNLOAD).mock(return_value=_file(petrol_docx(rows=rows)))
    with pytest.raises(UnexpectedResponse, match="100±0,5"):
        epdk.fetch(FetchRequest.create("epdk", ["EPDK.PETROL.BENZIN"], date(2025, 12, 1), date(2025, 12, 31)))


def test_unknown_series_and_transforms_rejected(epdk):
    with pytest.raises(InvalidInput, match="bilinmeyen seri"):
        epdk.fetch(FetchRequest.create("epdk", ["EPDK.YOK"], date(2026, 1, 1)))
    with pytest.raises(InvalidInput, match="desteklenmiyor"):
        epdk.fetch(FetchRequest.create("epdk", ["EPDK.PETROL.TOPLAM"], date(2026, 1, 1), formula=1))


def test_list_series_filters_catalog(epdk):
    assert set(epdk.list_series("lpg")["series_id"]) == {"EPDK.LPG.TOPLAM", "EPDK.LPG.OTOGAZ", "EPDK.LPG.TUPLU", "EPDK.LPG.DOKME"}
    assert list(epdk.list_series("motorin")["series_id"]) == ["EPDK.PETROL.MOTORIN"]
    assert len(epdk.list_series()) == len(epdk.source.series)


def _recent_listing(months_back: int) -> str:
    d = date.today().replace(day=1)
    for _ in range(months_back):
        d = (d - timedelta(days=1)).replace(day=1)
    names = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
    return _listing({f"{d.year} Yılı Petrol Piyasası {names[d.month - 1]} Ayı Sektör Raporu": [("word", "rapor")]})


@respx.mock
def test_healthcheck_ok(epdk):
    respx.get(PETROL_LIST).mock(return_value=_html(_recent_listing(2)))
    respx.get(url__startswith=DOWNLOAD).mock(return_value=_file(petrol_docx()))
    result = epdk.healthcheck()
    assert result.status is HealthStatus.OK, result.detail
    assert result.detail.startswith("EPDK.PETROL.TOPLAM son=") and result.checked == ("EPDK.PETROL.TOPLAM",)


@respx.mock
def test_healthcheck_fails_without_recent_reports(epdk):
    respx.get(PETROL_LIST).mock(return_value=_html(_recent_listing(12)))
    assert epdk.healthcheck().status is HealthStatus.FAIL


@respx.mock
def test_healthcheck_fails_on_stale_report(epdk, monkeypatch):
    monkeypatch.setattr("mlbooster.infrastructure.providers.epdk._HEALTH_LOOKBACK_DAYS", 400)
    respx.get(PETROL_LIST).mock(return_value=_html(_recent_listing(5)))
    respx.get(url__startswith=DOWNLOAD).mock(return_value=_file(petrol_docx()))
    result = epdk.healthcheck()
    assert result.status is HealthStatus.FAIL and "gün önce" in result.detail


@respx.mock
def test_healthcheck_reports_provider_errors(epdk):
    respx.get(PETROL_LIST).mock(return_value=httpx.Response(500))
    result = epdk.healthcheck()
    assert result.status is HealthStatus.FAIL and "ProviderUnavailable" in result.detail


def test_healthcheck_skipped_without_canaries(catalog):
    source = catalog.source("epdk")
    provider = EpdkProvider(replace(source, series=tuple(replace(s, canary=False) for s in source.series)), http=SecureHttpClient())
    assert provider.healthcheck().status is HealthStatus.SKIPPED
