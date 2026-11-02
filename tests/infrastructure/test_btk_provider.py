import logging
from dataclasses import replace
from datetime import date, timedelta

import httpx
import pandas as pd
import pytest
import respx

from mlbooster.application.dto import FetchRequest
from mlbooster.application.errors import ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import HealthStatus
from mlbooster.domain.observations import OBSERVATION_COLUMNS
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers import btk
from mlbooster.infrastructure.providers.btk import (
    BtkProvider,
    Report,
    entity_key,
    parse_fixed_broadband_shares,
    parse_index,
    parse_internet_subscribers,
    parse_mobile_shares,
    parse_remote,
    parse_revenue,
    pdf_text,
    quarter_end,
    report_lines,
    select_reports,
)
from mlbooster.infrastructure.providers.registry import PROVIDERS
from mlbooster.infrastructure.storage.raw_store import RawStore

# Excerpts of the text layer of the 2026-Q2 and 2019-Q4 reports (www.btk.gov.tr, read 2026-11-02).
INDEX_URL = "https://www.btk.gov.tr/elektronik-haberlesme-pazar-verileri"
S3 = "https://www.btk.gov.tr/s3/web-btk-site/7708c26c-5161-4919-8b3d-daa31d4ac8fb/2026"
REVENUE_2026Q2 = """\
net satış gelirleri 150,5 milyar ₺ olarak gerçekleşmiştir.
Çizelge 1-3 Türk Telekom ve Mobil İşletmecilerin Üç Aylık Net Satış Gelirleri, ₺
2025-2 2025-3 2025-4 2026-1 2026-2
TT Mobil 21.731.261.829 24.008.555.542 24.114.453.483 25.387.920.448 28.001.530.988
Turkcell 36.172.030.772 44.788.503.904 39.542.798.683 41.921.197.328 47.507.116.037
Türk Telekom 20.547.462.220 28.956.507.865 36.413.147.212 30.539.951.695 34.889.408.565
Vodafone 31.290.715.358 35.261.977.865 35.662.500.299 36.866.578.408 40.130.314.870
TOPLAM 109.741.470.17 133.015.545.176 135.732.899.676 134.715.647.878 150.528.370.460
Çizelge 1-4’te TT Mobil, Turkcell, Türk Telekom ve Vodafone haricindeki işletmecilerin çeyrek"""
INVESTMENT_FIRST = """\
Çizelge 1-6 Türk Telekom ve Mobil Şebeke İşletmecilerinin Üç Aylık Yatırımları, ₺
2025-2 2025-3
TT Mobil 1 2
Turkcell 1 2
Türk Telekom 1 2
Vodafone 1 2
TOPLAM 4 8"""
REVENUE_2016Q4 = """\
Çizelge 1-3 Türk Telekom ve Mobil İşletmecilerin Üç Aylık Net Satış Gelirleri, ₺
2016-3 2016-4
Türk Telekom 2.328.159.868 2.344.148.488
Turkcell 2.717.113.260 2.834.568.891
Vodafone 2.562.122.190 2.565.586.869
Avea 1.491.183.734 1.525.005.665
Toplam 9.098.579.052 9.269.309.913"""
MOBILE_2026Q2 = """\
abone sayısına göre Turkcell’in trafiğinin TByte, olarak Turkcell’in %39,1, %8,4, TT Mobil’in gerçekleşmiştir.
Şekil 4-13’te mobil işletmecilerin abone sayılarına göre pazar paylarına yer verilmektedir. 2026
yılı ikinci üç aylık dönem itibarıyla abone sayısına göre Turkcell’in %39,1, Vodafone’un %28,8 ve TT
Mobil’in ise %32’lik paya sahip olduğu görülmektedir."""
MOBILE_2019Q4 = (
    "2019 yılı dördüncü üç aylık dönem itibarıyla abone sayısına göre Turkcell’in %40,5, Vodafone’un %31,1, "
    "TT Mobil’in ise Şekil 4-12 Mobil İşletmecilerin %28,4’lük paya sahip olduğu görülmektedir."
)
FIXED_2026Q2 = """\
Çizelge 3-2 Sabit Genişbant İşletmecilerin Pazar Payları
İşletmeci Abone (%) İşletmeci Gelir (%)
TTNet 53,07 TTNet 54,73
Superonline 15,06 Superonline 18,50
Turksat 7,40 Turksat 3,26
Turknet 6,92 Turknet 6,72
Vodafone Net 6,14 Vodafone Net 8,58
Digital Platform 1,68 Digital Platform 2,14
Millenicom 1,56 Millenicom 1,44
Andromeda TV 1,20 Andromeda TV 1,08
Diğer 6,97 Diğer 3,56
TOPLAM 100,00 TOPLAM 100,00
Çizelge 3-3’de sabit genişbant hizmetine ilişkin son 6 yıllık gelir bilgilerine yer verilmektedir."""
INTERNET_2026Q2 = """\
Çizelge 3-1 Toplam İnternet Abone Sayıları
Çeyrek
Yıllık Büyüme
Dönem
2025-2 2026-1 2026-2 Oranı
Büyüme Oranı
(2025-2...2026-2)
(2026-1..2026-2)
xDSL 9.607.418 8.347.167 8.000.522 -4,2% -16,7%
Kablo İnternet 1.462.907 1.513.449 1.497.363 -1,1% 2,4%
Fiber (Toplam) 8.770.569 10.302.332 10.698.289 3,8% 22,0%
Diğer 219.665 216.512 213.332 -1,5% -2,9%
TOPLAM 97.361.083 99.504.397 100.846.626 1,3% 3,6%"""


def _lines(text: str) -> list[str]:
    return report_lines([text])


def _anchor(url: str, label: str) -> str:
    return (
        f'<div class="matrix-block"><a href="{url}" target="_blank" class="flex"><img src="/img/pdf.svg" alt="pdf"/>'
        f'<div class="truncate">{label}</div><div class="text-xs">pdf - 40.9 MB</div></a></div>'
    )


def _revenue(periods: list[tuple[int, int]], scale: float = 1.0) -> str:
    """Çizelge 1-3 for the given quarters; values grow with the quarter so overlapping reports can be told apart."""
    head = " ".join(f"{y}-{q}" for y, q in periods)
    base = {"TT Mobil": 1_000_000, "Turkcell": 2_000_000, "Türk Telekom": 3_000_000, "Vodafone": 4_000_000}
    rows = {k: [int(v * scale) + y * 10 + q for y, q in periods] for k, v in base.items()}
    total = [sum(col) for col in zip(*rows.values(), strict=True)]
    fmt = lambda xs: " ".join(f"{x:,}".replace(",", ".") for x in xs)  # noqa: E731
    body = "\n".join(f"{k} {fmt(v)}" for k, v in rows.items())
    return f"Çizelge 1-3 Türk Telekom ve Mobil İşletmecilerin Üç Aylık Net Satış Gelirleri, ₺\n{head}\n{body}\nTOPLAM {fmt(total)}"


def _mobile(turkcell: str = "39,1") -> str:
    return f"dönem itibarıyla abone sayısına göre Turkcell’in %{turkcell}, Vodafone’un %28,8 ve TT Mobil’in ise %32’lik paya sahip"


class FakePdf:
    """Stands in for PDFium: a report's "PDF" content is a key into a dict of page texts."""

    def __init__(self) -> None:
        self.pages: dict[bytes, list[str]] = {}

    def add(self, key: str, *pages: str) -> bytes:
        content = b"%PDF-1.7 " + key.encode()
        self.pages[content] = list(pages)
        return content

    def __call__(self, content: bytes) -> list[str]:
        return self.pages[content]


@pytest.fixture
def fake_pdf(monkeypatch) -> FakePdf:
    fake = FakePdf()
    monkeypatch.setattr(btk, "pdf_text", fake)
    return fake


@pytest.fixture
def provider(catalog):
    p = BtkProvider(catalog.source("btk"), http=SecureHttpClient(rps=None, max_retries=1, sleep=lambda _: None))
    yield p
    p.close()


def _serve(reports: dict[tuple[int, int], bytes]) -> dict[tuple[int, int], respx.Route]:
    """Index page listing the given quarters, and one PDF route per report."""
    routes, anchors = {}, []
    for (y, q), content in reports.items():
        url = f"{S3}/05/{y}{q}-report.pdf"
        anchors.append(_anchor(url, f"{y}-Q{q}"))
        routes[(y, q)] = respx.get(url).mock(return_value=httpx.Response(200, content=content, headers={"content-type": "application/pdf"}))
    respx.get(INDEX_URL).mock(return_value=httpx.Response(200, text="<html>" + "".join(anchors) + "</html>", headers={"content-type": "text/html; charset=utf-8"}))
    return routes


def test_registered_and_catalog_points_to_adapter(catalog):
    source = catalog.source("btk")
    assert PROVIDERS[source.provider] is BtkProvider
    assert source.canaries and all(parse_remote(s.remote) for s in source.series)
    assert source.license.attribution == "required" and source.verification == "verified"


def test_refuses_untrusted_host(catalog):
    hijacked = replace(catalog.source("btk"), base_url="https://evil.example/elektronik-haberlesme-pazar-verileri")
    with pytest.raises(ProviderError, match="yalnızca"):
        BtkProvider(hijacked, http=SecureHttpClient())


@pytest.mark.parametrize(
    ("label", "key"),
    [("TT Mobil", "TT_MOBIL"), ("Avea", "TT_MOBIL"), ("Türk Telekom", "TURK_TELEKOM"), ("Türksat", "TURKSAT"),
     ("Turksat", "TURKSAT"), ("D-Smart", "DSMART"), ("Diğer", "DIGER"), ("Vodafone Net", "VODAFONE_NET")],
)  # fmt: skip
def test_entity_key_is_stable_across_spellings(label, key):
    assert entity_key(label) == key


def test_quarter_end():
    assert [quarter_end(2026, q) for q in (1, 2, 3, 4)] == [date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30), date(2026, 12, 31)]


def test_parse_remote():
    assert parse_remote("gelir/net_satis") == ("gelir", "net_satis")
    assert parse_remote("sabit_genisbant_pay/gelir") == ("sabit_genisbant_pay", "gelir")


@pytest.mark.parametrize("remote", ["gelir", "gelir/abone", "yok/abone", "../x", "GELIR/net_satis"])
def test_parse_remote_rejects_unknown(remote):
    with pytest.raises(InvalidInput, match="remote"):
        parse_remote(remote)


def test_parse_index_keeps_report_links_of_the_pinned_host():
    page = "".join([
        _anchor(f"{S3}/10/6a6ae76a-f3ad-4af1-95e3-bbb95b0d1a07.pdf", "2026-Q2"),
        _anchor(f"{S3}/08/0b817999-9118-4725-a222-49a08dc8e4c0.pdf", "2026-Q1"),
        _anchor(f"{S3}/10/other.pdf", "2026-Q2"),  # second link for a quarter: the first one wins
        _anchor("/s3/web-btk-site/x/2026/03/71cf434e.pdf", "2019-Q1"),  # relative link
        _anchor("https://evil.example/s3/a.pdf", "2018-Q1"),
        _anchor("http://www.btk.gov.tr/s3/a.pdf", "2018-Q2"),
        _anchor(f"{S3}/10/a.docx", "2018-Q3"),
        _anchor(f"{S3}/10/b.pdf", "Sunum"),
    ])  # fmt: skip
    reports = parse_index(page)
    assert reports == [
        Report(date(2026, 6, 30), f"{S3}/10/6a6ae76a-f3ad-4af1-95e3-bbb95b0d1a07.pdf"),
        Report(date(2026, 3, 31), f"{S3}/08/0b817999-9118-4725-a222-49a08dc8e4c0.pdf"),
        Report(date(2019, 3, 31), "https://www.btk.gov.tr/s3/web-btk-site/x/2026/03/71cf434e.pdf"),
    ]


def _reports(*quarters: tuple[int, int]) -> list[Report]:
    return [Report(quarter_end(y, q), f"{S3}/{y}{q}.pdf") for y, q in quarters]


def test_select_reports_uses_the_newest_report_covering_each_quarter():
    every = _reports(*[(y, q) for y in (2024, 2025, 2026) for q in (1, 2, 3, 4) if (y, q) <= (2026, 2)])
    periods = lambda rs: [(r.period.year, (r.period.month - 1) // 3 + 1) for r in rs]  # noqa: E731
    # five quarters per report: 2026-Q2 covers 2025-Q2..2026-Q2, 2025-Q1 covers 2024-Q1..2025-Q1
    assert periods(select_reports(every, date(2024, 1, 1), date(2026, 6, 30), 5)) == [(2025, 1), (2026, 2)]
    # the last quarter of a range comes from the newest report that still lists it (revised figures)
    assert periods(select_reports(every, date(2025, 4, 1), date(2025, 9, 30), 5)) == [(2026, 2)]
    assert periods(select_reports(every, date(2025, 7, 1), date(2026, 6, 30), 1)) == [(2025, 3), (2025, 4), (2026, 1), (2026, 2)]
    assert periods(select_reports(every, date(2025, 7, 1), date(2026, 6, 30), 2)) == [(2025, 4), (2026, 2)]


def test_select_reports_skips_quarters_without_a_report():
    gaps = _reports((2018, 1), (2018, 3))  # 2018-Q2 and 2018-Q4 are not on the announcement page
    assert select_reports(gaps, date(2018, 1, 1), date(2018, 12, 31), 1) == gaps
    assert select_reports(gaps, date(2017, 1, 1), date(2017, 12, 31), 1) == []


def test_parse_revenue_replaces_a_misprinted_total(caplog):
    rows = parse_revenue(_lines(INVESTMENT_FIRST.replace("Yatırımları", "Bilgileri") + "\n" + REVENUE_2026Q2)[7:])
    by = {(d, e): v for d, e, _, v in rows}
    assert by[(date(2026, 6, 30), "")] == 150_528_370_460
    assert by[(date(2026, 6, 30), "TURKCELL")] == 47_507_116_037
    assert by[(date(2025, 6, 30), "")] == 109_741_470_179  # printed as "109.741.470.17": sum of the operators
    assert "unreadable" in caplog.text
    names = {e: n for _, e, n, _ in rows}
    assert names == {"": "TOPLAM", "TT_MOBIL": "TT Mobil", "TURKCELL": "Turkcell", "TURK_TELEKOM": "Türk Telekom", "VODAFONE": "Vodafone"}
    assert len(rows) == 25


def test_parse_revenue_skips_the_investment_table_and_maps_avea():
    rows = parse_revenue(_lines(INVESTMENT_FIRST + "\n" + REVENUE_2016Q4))
    by = {(d, e): v for d, e, _, v in rows}
    assert by[(date(2016, 12, 31), "TT_MOBIL")] == 1_525_005_665
    assert by[(date(2016, 12, 31), "")] == 9_269_309_913 and len(rows) == 10


def test_parse_revenue_drops_periods_that_do_not_add_up(caplog):
    text = REVENUE_2016Q4.replace("9.269.309.913", "9.269.309.999").replace("2.328.159.868", "2.328.159.x")
    assert parse_revenue(_lines(text)) == []
    assert "differs" in caplog.text and "unreadable value" in caplog.text


def test_parse_revenue_needs_every_operator(caplog):
    assert parse_revenue(_lines(REVENUE_2016Q4.replace("Vodafone 2.562.122.190 2.565.586.869\n", ""))) == []
    assert parse_revenue(_lines("2025-1 2025-2\nTurkcell 1 2")) == []  # no caption
    assert "expected operators" in caplog.text


@pytest.mark.parametrize(
    ("text", "shares"),
    [(MOBILE_2026Q2, {"TURKCELL": 39.1, "VODAFONE": 28.8, "TT_MOBIL": 32.0}),
     (MOBILE_2019Q4, {"TURKCELL": 40.5, "VODAFONE": 31.1, "TT_MOBIL": 28.4}),
     ("abone sayısına göre Turkcell’in %44, Vodafone’un %31,3, Avea’nın ise %24,7’lik pazar payına sahip",
      {"TURKCELL": 44.0, "VODAFONE": 31.3, "TT_MOBIL": 24.7})],
)  # fmt: skip
def test_parse_mobile_shares(text, shares):
    rows = parse_mobile_shares(text, date(2026, 6, 30))
    assert {e: v for _, e, _, v in rows} == {"": 100.0, **shares}
    assert {d for d, *_ in rows} == {date(2026, 6, 30)}


def test_parse_mobile_shares_rejects_shares_that_do_not_add_up(caplog):
    assert parse_mobile_shares(_mobile("45,1"), date(2026, 6, 30)) == []
    assert "not found" in caplog.text


def test_parse_fixed_broadband_shares():
    out = parse_fixed_broadband_shares(_lines(FIXED_2026Q2), date(2026, 6, 30))
    abone = {e: (n, v) for _, e, n, v in out["abone"]}
    assert abone["TTNET"] == ("TTNet", 53.07) and abone["TURKSAT"] == ("Türksat", 7.40) and abone["DIGER"] == ("Diğer", 6.97)
    assert abone[""] == ("TOPLAM", 100.0) and len(abone) == 10
    assert {e: v for _, e, _, v in out["gelir"]}["SUPERONLINE"] == 18.50


def test_parse_fixed_broadband_shares_accepts_decimal_points_and_a_missing_total():
    """2025-Q1 prints no TOPLAM row, 2025-Q2 uses decimal points."""
    text = FIXED_2026Q2.replace("TOPLAM 100,00 TOPLAM 100,00\n", "").replace("53,07", "53.07")
    out = parse_fixed_broadband_shares(_lines(text), date(2025, 6, 30))
    assert {e: v for _, e, _, v in out["abone"]}["TTNET"] == 53.07 and len(out["gelir"]) == 10


def test_parse_fixed_broadband_shares_rejects_an_incomplete_table(caplog):
    """The 2025-Q4 report lists shares that add up to 98.35 only."""
    out = parse_fixed_broadband_shares(_lines(FIXED_2026Q2.replace("Diğer 6,97 Diğer 3,56\n", "")), date(2025, 12, 31))
    assert out == {} and "failed validation" in caplog.text
    assert parse_fixed_broadband_shares(["Gelir 2020 2021"], date(2025, 12, 31)) == {}


def test_parse_internet_subscribers():
    out = parse_internet_subscribers(_lines(INTERNET_2026Q2))
    assert [(d, v) for d, _, _, v in out["toplam"]] == [
        (date(2025, 6, 30), 97_361_083), (date(2026, 3, 31), 99_504_397), (date(2026, 6, 30), 100_846_626)
    ]  # fmt: skip
    assert out["fiber"][-1] == (date(2026, 6, 30), "", "TOPLAM", 10_698_289)


def test_parse_internet_subscribers_validates(caplog):
    assert parse_internet_subscribers(_lines(INTERNET_2026Q2.replace("Fiber (Toplam) 8.770.569", "Fiber (Toplam) 98.770.569"))) == {}
    assert parse_internet_subscribers(_lines(INTERNET_2026Q2.replace("2025-2 2026-1 2026-2 Oranı", "Oranı"))) == {}
    assert parse_internet_subscribers(["xDSL yok"]) == {}
    assert "failed validation" in caplog.text and "not found" in caplog.text


def _minimal_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def test_pdf_text_reads_the_text_layer():
    assert pdf_text(_minimal_pdf("TOPLAM 100,00")) == ["TOPLAM 100,00"]
    with pytest.raises(UnexpectedResponse, match="PDF"):
        pdf_text(b"%PDF-1.4 truncated")


@respx.mock
def test_fetch_reads_the_newest_report_for_each_quarter(provider, fake_pdf, tmp_path):
    provider._raw_store = RawStore(tmp_path)
    q4 = [(2025, 1), (2025, 2), (2025, 3), (2025, 4)]
    routes = _serve({
        (2026, 1): fake_pdf.add("2026-1", _revenue([(2025, 1)] + q4[1:] + [(2026, 1)], scale=2), _mobile("39,3").replace("%28,8 ve TT Mobil’in ise %32", "%28,8 ve TT Mobil’in ise %31,9")),
        (2025, 4): fake_pdf.add("2025-4", _revenue([(2024, 4)] + q4), _mobile()),
    })  # fmt: skip
    df = provider.fetch(FetchRequest.create("btk", ["BTK.GELIR", "BTK.MOBIL.ABONE.PAY"], date(2025, 1, 1), date(2026, 3, 31)))

    assert list(df.columns) == OBSERVATION_COLUMNS
    assert routes[(2026, 1)].call_count == 1 and routes[(2025, 4)].call_count == 1
    gelir = df[df["series_id"] == "BTK.GELIR"].set_index(["date", "entity_id"])["value"]
    assert gelir[(date(2025, 6, 30), "TURKCELL")] == 2 * 2_000_000 + 20252  # revised figure of the newer report
    assert gelir[(date(2025, 3, 31), "")] == 2 * 10_000_000 + 4 * 20251
    assert set(gelir.index.get_level_values("date")) == {quarter_end(y, q) for y, q in [*q4, (2026, 1)]}
    mobil = df[df["series_id"] == "BTK.MOBIL.ABONE.PAY"].set_index(["date", "entity_id"])["value"]
    assert mobil[(date(2026, 3, 31), "TURKCELL")] == 39.3 and mobil[(date(2025, 12, 31), "TURKCELL")] == 39.1
    first = df.iloc[0]
    assert first["source_id"] == "btk" and first["freq"] == "Q" and first["metric"] == "level"
    assert set(df["dataset_id"]) == {"pazar_verileri_gelir", "pazar_verileri_mobil_abone_pay"}
    assert set(df["publication_lag_days"]) == {100} and not df["is_proxy"].any()
    assert set(df.loc[df["series_id"] == "BTK.GELIR", "currency"]) == {"TRY"}

    again = provider.fetch(FetchRequest.create("btk", ["BTK.GELIR"], date(2025, 10, 1), date(2026, 3, 31)))
    assert routes[(2026, 1)].call_count == 1  # read back from the bronze store, not downloaded again
    assert len(again) == 10
    assert len(list((tmp_path / "btk").glob("*.pdf"))) == 2


@respx.mock
def test_fetch_keeps_quarters_of_a_report_within_the_period(provider, fake_pdf):
    _serve({(2026, 2): fake_pdf.add("2026-2", INTERNET_2026Q2, FIXED_2026Q2)})
    df = provider.fetch(FetchRequest.create("btk", ["BTK.INTERNET.ABONE", "BTK.SGB.ABONE.PAY", "BTK.SGB.GELIR.PAY"], date(2026, 1, 1), date(2026, 6, 30)))
    internet = df[df["series_id"] == "BTK.INTERNET.ABONE"]
    assert list(internet["date"]) == [date(2026, 3, 31), date(2026, 6, 30)]  # the year-ago column is outside the period
    assert set(internet["entity_id"]) == {""} and set(internet["unit"]) == {"count"}
    shares = df[(df["series_id"] == "BTK.SGB.GELIR.PAY") & (df["entity_id"] != "")]
    assert abs(shares["value"].sum() - 100) < 0.5


@respx.mock
def test_fetch_without_matching_tables_returns_empty_frame(provider, fake_pdf):
    _serve({(2026, 2): fake_pdf.add("2026-2", "boş sayfa")})
    df = provider.fetch(FetchRequest.create("btk", ["BTK.FIBER.ABONE"], date(2026, 1, 1), date(2026, 6, 30)))
    assert df.empty and list(df.columns) == OBSERVATION_COLUMNS


def test_fetch_rejects_transformations_and_unknown_series(provider):
    with pytest.raises(InvalidInput, match="formül"):
        provider.fetch(FetchRequest.create("btk", ["BTK.GELIR"], date(2025, 1, 1), formula=1))
    with pytest.raises(InvalidInput, match="bilinmeyen seri"):
        provider.fetch(FetchRequest.create("btk", ["BTK.YOK"], date(2025, 1, 1)))


@pytest.mark.parametrize(
    ("response", "error", "message"),
    [(httpx.Response(302, headers={"location": "https://www.btk.gov.tr/"}), UnexpectedResponse, "yönlendirme"),
     (httpx.Response(404), ProviderError, "HTTP 404"),
     (httpx.Response(200, json={"items": []}), UnexpectedResponse, "text/html"),
     (httpx.Response(200, text="<html>rapor yok</html>", headers={"content-type": "text/html"}), UnexpectedResponse, "bağlantısı")],
)  # fmt: skip
@respx.mock
def test_index_errors(provider, response, error, message):
    respx.get(INDEX_URL).mock(return_value=response)
    with pytest.raises(error, match=message):
        provider.reports()


@respx.mock
def test_report_that_is_not_a_pdf_is_refused(provider):
    routes = _serve({(2026, 2): b"<html>"})
    routes[(2026, 2)].mock(return_value=httpx.Response(200, content=b"<html>giris</html>", headers={"content-type": "application/pdf"}))
    with pytest.raises(UnexpectedResponse, match="PDF değil"):
        provider.fetch(FetchRequest.create("btk", ["BTK.GELIR"], date(2026, 4, 1), date(2026, 6, 30)))


def test_list_series(provider):
    found = provider.list_series("pazar payları")
    assert set(found["series_id"]) == {"BTK.MOBIL.ABONE.PAY", "BTK.SGB.ABONE.PAY", "BTK.SGB.GELIR.PAY"}
    assert len(provider.list_series()) == len(provider.source.series)


def _last_quarters() -> list[tuple[int, int]]:
    """The two quarters before the current one."""
    today = date.today()
    i = today.year * 4 + (today.month - 1) // 3 - 1
    return [((i - 1) // 4, (i - 1) % 4 + 1), (i // 4, i % 4 + 1)]


@respx.mock
def test_healthcheck_ok(provider, fake_pdf):
    quarters = _last_quarters()
    _serve({quarters[-1]: fake_pdf.add("last", _revenue(quarters), _mobile())})
    result = provider.healthcheck()
    assert result.status is HealthStatus.OK, result.detail
    assert "BTK.GELIR" in result.detail and "BTK.MOBIL.ABONE.PAY" in result.detail


@respx.mock
def test_healthcheck_fails_when_a_canary_is_missing(provider, fake_pdf):
    quarters = _last_quarters()
    _serve({quarters[-1]: fake_pdf.add("last", _revenue(quarters))})
    result = provider.healthcheck()
    assert result.status is HealthStatus.FAIL and "BTK.MOBIL.ABONE.PAY" in result.detail


@respx.mock
def test_healthcheck_fails_on_stale_reports(provider, fake_pdf, monkeypatch):
    quarters = _last_quarters()
    _serve({quarters[-1]: fake_pdf.add("last", _revenue(quarters), _mobile())})
    monkeypatch.setattr(btk, "_STALENESS_MARGIN_DAYS", -400)
    result = provider.healthcheck()
    assert result.status is HealthStatus.FAIL and "son gözlem" in result.detail


@respx.mock
def test_healthcheck_reports_provider_errors(provider):
    respx.get(INDEX_URL).mock(return_value=httpx.Response(403))
    result = provider.healthcheck()
    assert result.status is HealthStatus.FAIL and "HTTP 403" in result.detail


def test_healthcheck_skipped_without_canaries(catalog):
    source = replace(catalog.source("btk"), series=())
    assert BtkProvider(source, http=SecureHttpClient()).healthcheck().status is HealthStatus.SKIPPED


def test_large_share_moves_are_logged(caplog):
    frame = pd.DataFrame({
        "series_id": ["S"] * 3, "entity_id": ["A"] * 3, "unit": ["percent"] * 3, "value": [40.0, 41.0, 55.0],
        "date": [date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31)],
    })  # fmt: skip
    with caplog.at_level(logging.WARNING):
        btk._warn_jumps(frame)
    assert "14.0 points" in caplog.text and caplog.text.count("points") == 1


def test_quarters_far_back_are_not_requested():
    reports = _reports((2026, 2))
    assert select_reports(reports, date(2026, 4, 1) - timedelta(days=1), date(2026, 6, 30), 1) == reports
