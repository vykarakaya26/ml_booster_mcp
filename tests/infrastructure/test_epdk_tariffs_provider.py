import io
import json
from dataclasses import replace
from datetime import date

import httpx
import openpyxl
import pytest
import respx

from mlbooster.application.dto import FetchRequest
from mlbooster.application.errors import ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.epdk_reports import Grid
from mlbooster.infrastructure.providers.epdk_tariffs import (
    Decision,
    EpdkTariffProvider,
    effective_date,
    in_force,
    parse_decisions,
    parse_remote,
    read_grid,
    tariff_table,
)
from mlbooster.infrastructure.providers.registry import PROVIDERS
from mlbooster.infrastructure.storage.raw_store import RawStore

# Workbooks are built here with the real sheet names, header texts and row labels of the 2025-04 (two-sheet) and
# 2026-04 (one sheet) tariff tables (www.epdk.gov.tr, 2026-11-15); values are the published ones, fewer rows.
BASE = "https://www.epdk.gov.tr"
LIST_URL = f"{BASE}/Detay/GetFastAccessList"
DOWNLOAD = f"{BASE}/Detay/DownloadDocument"


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


OLD_HEAD = [None, None, None, "Perakende Tek Zamanlı Enerji Bedeli", "Perakende Gündüz Enerji Bedeli",
            "Perakende Puant Enerji Bedeli", "Perakende Gece Enerji Bedeli", "Dağıtım Bedeli",
            "Güç Bedeli Hariç Toplam Tarifeler (kr/kWh)", "Gündüz", "Puant", "Gece"]  # fmt: skip


def old_layout(scale: float = 1.0, *, mesken_tiers: bool = True) -> bytes:
    """2016-01..2025-04 layout: energy and distribution on 'Faaliyet Bazlı', power fee on 'Nihai Tarife Tablosu'."""
    k = scale
    mesken = (
        [[None, None, "Mesken (8 kWh/gün ve altı)", 49.4065 * k, 195.064 * k, 374.7551 * k, 51.3656 * k, 183.6166 * k, 233.0231 * k],
         [None, None, "Mesken (8 kWh/gün üstü)", 161.546 * k, 165.9326 * k, 325.6581 * k, 38.201 * k, 183.6166 * k, 345.1626 * k]]
        if mesken_tiers else [[None, None, "Mesken", 120.0 * k, 121.0 * k, 200.0 * k, 50.0 * k, 100.0 * k, 220.0 * k]]
    )  # fmt: skip
    main = [
        [None, "EPDK Tarafından Onaylanan ve 5 Nisan 2025 Tarihinden İtibaren Uygulanacak Tarifeler"],
        [None, None, None, "Faaliyet Bazlı Tüketici Tarifeleri (kr/kWh)", None, None, None, None, "Güç Bedeli Hariç Toplam Tarifeler (kr/kWh)"],
        ["İletim Sistemi Kullanıcıları", "Görevli Tedarik Şirketinden Enerji Alan İletim Sistemi Kullanıcıları", *OLD_HEAD[2:]],
        [None, "Tüketici", None, 312.4942 * k, 316.4941 * k, 507.6545 * k, 162.2084 * k, 0, 312.4942 * k],
        ["Dağıtım Sistemi Kullanıcıları ", "Dağıtım Sistemi Kullanıcıları ", *OLD_HEAD[2:]],
        [None, "Orta Gerilim "],
        [None, "Çift Terimli "],
        [None, None, "Sanayi", 269.3343 * k, 272.0786 * k, 493.1613 * k, 131.7097 * k, 81.0595 * k, 350.3938 * k],
        [None, None, "Kamu ve Özel Hizmetler Sektörü ile Diğer", 301.84 * k, 304.5 * k, 540.1 * k, 150.0 * k, 126.3293 * k, 428.17 * k],
        [None, "Tek Terimli"],
        [None, None, "Sanayi", 269.3343 * k, 272.0786 * k, 493.1613 * k, 131.7097 * k, 89.5372 * k, 358.8715 * k],
        [None, "Alçak Gerilim"],
        [None, "Tek Terimli"],
        [None, None, "Sanayi", 269.3343 * k, 273.5148 * k, 473.4073 * k, 112.167 * k, 138.5324 * k, 407.8667 * k],
        [None, None, "Kamu ve Özel Hizmetler Sektörü ile Diğer (30 kWh/gün ve altı)", 257.3982 * k, 331.1054 * k, 569.8767 * k, 141.1242 * k, 187.741 * k, 445.1392 * k],
        [None, None, "Kamu ve Özel Hizmetler Sektörü ile Diğer (30 kWh/gün üstü)", 326.9374 * k, 331.1054 * k, 569.8767 * k, 141.1242 * k, 187.741 * k, 514.6784 * k],
        *mesken,
        [None, None, "Tarımsal Faaliyetler", 194.0087 * k, 201.566 * k, 361.6219 * k, 65.2611 * k, 155.263 * k, 349.2717 * k],
        [None, None, "Aydınlatma", 336.5818 * k, None, None, None, 179.8147 * k, 516.3965 * k],
        [None, "Fon, pay, vergi vb. yasal yükümlülükler ayrıca ilave edilir."],
    ]
    power = [
        [None, "EPDK tarafından onaylanan ve 5 Nisan 2025 Tarihinden İtibaren Uygulanacak Vergi, Fon ve Paylar Hariç Tarifeler"],
        [None, "Dağıtım Sistemi Kullanıcıları"],
        [None, "Görevli Tedarik Şirketinden Enerji Alan Tüketiciler", None, None, None, None, None, None, None,
         "Özel Tedarikçiden Enerji Alan Tüketiciler İçin Sistem Kullanım Tarifeleri"],
        [None, None, "Kapasite ", None, "Aktif Enerji + Dağıtım", None, None, None, "Reaktif Enerji", None, "Kapasite"],
        [None, None, "Güç Bedeli", "Güç Aşım Bedeli", "Tek Zamanlı ", "Gündüz", "Puant", "Gece", None, None, "Güç Bedeli", "Güç Aşım Bedeli"],
        [None, None, "kr/Ay/kW", "kr/Ay/kW", "kr/kWh", "kr/kWh", "kr/kWh", "kr/kWh", "kr/kVARh", None, "kr/Ay/kW", "kr/Ay/kW"],
        [None, "Orta Gerilim ", None, None, None, None, None, None, None, "Orta Gerilim "],
        [None, "Çift Terimli ", None, None, None, None, None, None, None, "Çift Terimli "],
        [None, "Sanayi", 2693.8043 * k, 5387.6086 * k, 350.3938 * k, 353.1, 574.2, 212.7, 264.5474, "Sanayi", 9999.0, 19998.0],
        [None, "Üreticiler İçin Veriş Yönünde Çift Terimli Dağıtım Tarifeleri"],
        [None, "Üretici ", 2528.6352, 5057.2704, 17.0239, None, None, None, 264.5474],
    ]
    return xlsx({"Faaliyet Bazlı Tarife Tablosu": main, "Nihai Tarife Tablosu": power, "Yeşil Tarife Tablosu": [["Yeşil Enerji Bedeli"], [339.7375]]})


def new_layout() -> bytes:
    """2026-04 layout: one sheet with every component."""
    return xlsx({"Tarife Tablosu": [
        [None, None, None, "4 Nisan 2026 Tarihinden İtibaren Geçerli Vergiler Hariç Elektrik Tarifeleri"],
        [None, None, None, "Abone / Kullanıcı Grubu", "Görevli Tedarik Şirketinden Enerji Alan Tüketiciler İçin\nAktif Enerji Bedelleri",
         None, None, None, "Dağıtım Sistemi Kullanıcısı Üretici ve Tüketiciler İçin Dağıtım Sistem Kullanım Bedelleri"],
        [None, None, None, None, "Tek Zamanlı Enerji Bedeli\n(kr/kWh)", "Çok Zamanlı Enerji Bedeli\n(kr/kWh)", None, None,
         "Dağıtım Bedeli\n(kr/kWh)", "Güç Bedeli\n(kr/kW/ay)", "Reaktif Enerji Bedeli\n(kr/kVarh)"],
        [None, None, None, None, None, "Gündüz", "Puant", "Gece"],
        [None, None, None, "İletim Sistemi Kullanıcıları", 312.4942, 316.4941, 507.6545, 162.2084],
        [None, "Dağıtım Sistemi Kullanıcıları ", "OG Çift Terim", "Sanayi", 290.9687, 295.6124, 495.3736, 134.385, 107.0498, 3557.5915, 349.3698],
        [None, None, None, "Mesken", 203.6685, 209.1502, 388.8424, 65.454, 165.2488, 8378.9424],
        [None, None, "OG Tek Terim", "Sanayi", 290.9687, 295.6124, 495.3736, 134.385, 118.2457, None, 349.3698],
        [None, None, "AG Tek Terim", "Sanayi", 298.5253, 303.4583, 515.7441, 132.1229, 182.9503, None, 349.3698],
        [None, None, None, "Kamu ve Özel Hizmetler Sektörü ile Diğer (30 kWh/gün üstü)", 345.4688, 350.262, 597.3903, 153.6314, 247.9368, None, 349.3698],
        [None, None, None, "Mesken (8 kWh/gün ve altı)", 49.4065, 195.064, 374.7551, 51.3656, 242.49],
        [None, None, None, "Tarımsal Faaliyetler", 233.3838, 242.8304, 422.8933, 89.4874, 203.7247, None, 349.3698],
        [None, None, "Üretici", "Tek Terimli Üretici", None, None, None, None, 30.3056, None, 349.3698],
        [None, None, None, "Çift Terimli Üretici", None, None, None, None, 22.3428, 3318.6771, 349.3698],
        [None, "Çok zamanlı tarife uygulamasında; ... Gündüz 06-17, Puant 17-22, Gece 22-06 saatleri arasındadır."],
    ]})  # fmt: skip


def entry(decided: str, number: str, title: str, files: list[tuple[str, int]]) -> dict:
    """One list entry as returned by GetFastAccessList (fields trimmed to those the site sends for this list)."""
    return {
        "Title": title, "Date": decided, "Number": number, "RgDate": "", "RgNumber": "", "IsLink": 1, "Url": None,
        "FastAccessDetail": [{"ContentId": c, "ContentType": t, "IsFileType": 2, "Title": "Ek"} for c, t in files],
    }  # fmt: skip


LISTING = {
    "Type": 1, "State": 1, "fId": "1327",
    "model": [
        entry("02.04.2026", "14461", "4/4/2026' dan itibaren geçerli olacak elektrik tarifeleri tabloları hakkında",
              [("hDZgaY7BFKo=", 11), ("pI+XZjfJIU0=", 9)]),
        entry("27.03.2025", "13426", "5/4/2025’ten itibaren geçerli olacak elektrik tarifeleri tabloları hakkında",
              [("b4lTJPy9dhY=", 11), ("o7F6WhiwiSs=", 9)]),
        entry("30.01.2021", "9954", "01/01/2021 tarihinden itibaren geçerli elektrik tarife tabloları hakkında", [("k/f4r+Ghn4U=", 9)]),
        entry("23.07.2020", "9468", "1/8/2020 tarihinden geçerli yeşil tarife tablosu hakkında", [("ZeQuoAX3svQ=", 9)]),
        entry("30.09.2020", "9580", "1/10/2020 tarihinden geçerli elektrik tarife tabloları hakkında", [("bl5GnAG0I0E=", 9)]),
        entry("28.12.2017", "7617", "2018 1. Çeyrek Perakende Elektrik Tarife Tabloları", [("a/wkS3e/xJY=", 9)]),
        entry("11.05.2017", "7070", "Dağıtım Lisansı Sahibi Tüzel Kişiler ve Görevli Tedarik Şirketlerinin ...", [("GzbLpwRNkmA=", 11)]),
        {"Title": None, "Date": "bad", "FastAccessDetail": None},
        "not an entry",
    ],
}  # fmt: skip


def _json(doc) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(doc).encode(), headers={"content-type": "application/json; charset=utf-8"})


def _file(content: bytes) -> httpx.Response:
    return httpx.Response(200, content=content, headers={"content-type": "application/octet-stream"})


def _site(files: dict[str, bytes], listing=LISTING):
    respx.post(LIST_URL).mock(return_value=_json(listing))
    route = respx.get(DOWNLOAD).mock(side_effect=lambda request: _file(files[request.url.params["id"]]))
    return route


FILES = {"pI+XZjfJIU0=": new_layout(), "o7F6WhiwiSs=": old_layout(), "k/f4r+Ghn4U=": old_layout(0.2),
         "bl5GnAG0I0E=": old_layout(0.19, mesken_tiers=False), "a/wkS3e/xJY=": old_layout(0.1, mesken_tiers=False)}  # fmt: skip


@pytest.fixture
def make(catalog):
    def factory(today=date(2026, 11, 15), raw_store=None):
        return EpdkTariffProvider(
            catalog.source("epdk_tarife"), http=SecureHttpClient(rps=None, max_retries=1, sleep=lambda _: None),
            raw_store=raw_store, today=lambda: today,
        )  # fmt: skip

    return factory


@pytest.fixture
def tariffs(make):
    provider = make()
    yield provider
    provider.close()


def _req(codes, start, end):
    return FetchRequest.create("epdk_tarife", [codes] if isinstance(codes, str) else codes, start, end)


# --- Catalog, remote, list -------------------------------------------------------------------------------------


def test_registered_and_catalog_points_to_adapter(catalog):
    source = catalog.source("epdk_tarife")
    assert PROVIDERS[source.provider] is EpdkTariffProvider
    assert source.canaries and all(parse_remote(s.remote) for s in source.series)
    assert source.publication_lag_days == 0


def test_refuses_untrusted_host(catalog):
    with pytest.raises(ProviderError, match="yalnızca"):
        EpdkTariffProvider(replace(catalog.source("epdk_tarife"), base_url="https://evil.example/"), http=SecureHttpClient())


def test_parse_remote():
    assert parse_remote("AG_TEK.SANAYI/ENERJI_TEK") == ("AG_TEK", ("SANAYI",), "ENERJI_TEK")
    assert parse_remote("AG_TEK.MESKEN,MESKEN_DUSUK/DAGITIM") == ("AG_TEK", ("MESKEN", "MESKEN_DUSUK"), "DAGITIM")


@pytest.mark.parametrize(
    "remote", ["AG_TEK/ENERJI_TEK", "YG_TEK.SANAYI/ENERJI_TEK", "AG_TEK.KONUT/ENERJI_TEK", "AG_TEK.SANAYI/VERGI",
               "ag_tek.SANAYI/ENERJI_TEK", "AG_TEK.SANAYI,/GUC"],
)  # fmt: skip
def test_parse_remote_rejects(remote):
    with pytest.raises(InvalidInput):
        parse_remote(remote)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("4/4/2026' dan itibaren geçerli olacak elektrik tarifeleri tabloları hakkında", date(2026, 4, 4)),
        ("01/10/2019 tarihinden itibaren geçerli elektrik tarife tabloları hakkında", date(2019, 10, 1)),
        ("1.8.2018'den itibaren geçerli tarife tabloları", date(2018, 8, 1)),
        ("2018 1. Çeyrek Perakende Elektrik Tarife Tabloları", date(2018, 1, 1)),
        ("2016 3. Çeyrek Tarife Tabloları", date(2016, 7, 1)),
        ("Maliyet Bazlı Fiyatlandırma Mekanizması", None),
        ("31/02/2020 tarihinden itibaren", None),
    ],
)
def test_effective_date_from_title(title, expected):
    assert effective_date(title) == expected


def test_parse_decisions_keeps_tariff_tables_only():
    decisions = parse_decisions(LISTING)
    assert [(d.effective, d.number) for d in decisions] == [
        (date(2018, 1, 1), "7617"), (date(2020, 10, 1), "9580"), (date(2021, 1, 1), "9954"),
        (date(2025, 4, 5), "13426"), (date(2026, 4, 4), "14461"),
    ]  # fmt: skip  (green-tariff-only and decision-text-only entries are skipped)
    assert decisions[-1].doc_ids == ("pI+XZjfJIU0=",) and decisions[-1].decided == date(2026, 4, 2)


@pytest.mark.parametrize("doc", [{"State": 0, "model": []}, {"State": 1}, [], {"State": 1, "model": []},
                                 {"State": 1, "model": [{"Title": "x"}] * 501}])  # fmt: skip
def test_parse_decisions_rejects_unexpected_lists(doc):
    with pytest.raises(UnexpectedResponse):
        parse_decisions(doc)


def test_in_force_is_point_in_time():
    decisions = parse_decisions(LISTING)
    # 1/1/2021 was decided on 30 January 2021: December 2020 keeps the October tariff, January 2021 gets the new one.
    assert in_force(decisions, date(2020, 12, 31)).number == "9580"
    assert in_force(decisions, date(2021, 1, 29)).number == "9580"
    assert in_force(decisions, date(2021, 1, 31)).number == "9954"
    # Decided 27 March, in force from 5 April: not in March.
    assert in_force(decisions, date(2025, 3, 31)).number == "9954"
    assert in_force(decisions, date(2025, 4, 30)).number == "13426"
    assert in_force(decisions, date(2017, 12, 31)) is None
    correction = Decision(date(2025, 4, 10), date(2025, 4, 5), "13999", "düzeltme", ("x",))
    assert in_force([*decisions, correction], date(2025, 4, 30)).number == "13999"


# --- Tables ----------------------------------------------------------------------------------------------------


def _sheets(content: bytes):
    from mlbooster.infrastructure.providers.epdk_reports import read_xlsx

    return read_xlsx(content, titles=True)


def test_old_layout_components_and_power_fee():
    table = tariff_table(_sheets(old_layout()), "test")
    assert table.values[("AG_TEK", "SANAYI")] == {"ENERJI_TEK": 269.3343, "ENERJI_GUNDUZ": 273.5148, "ENERJI_PUANT": 473.4073,
                                                  "ENERJI_GECE": 112.167, "DAGITIM": 138.5324}  # fmt: skip  (totals block ignored)
    assert table.values[("OG_CIFT", "SANAYI")]["GUC"] == 2693.8043  # from 'Nihai', not the other-supplier block
    assert table.values[("OG_CIFT", "SANAYI")]["REAKTIF"] == 264.5474
    assert table.values[("ILETIM", "TUKETICI")]["ENERJI_TEK"] == 312.4942
    assert table.get("AG_TEK", ("KAMU_OZEL", "KAMU_OZEL_YUKSEK"), "ENERJI_TEK") == 326.9374
    assert table.get("AG_TEK", ("MESKEN", "MESKEN_DUSUK"), "ENERJI_TEK") == 49.4065
    assert table.get("AG_TEK", ("AYDINLATMA",), "ENERJI_GUNDUZ") is None
    assert not any(section == "URETICI" for section, _ in table.values)


def test_new_layout_components():
    table = tariff_table(_sheets(new_layout()), "test")
    assert table.values[("ILETIM", "TUKETICI")] == {"ENERJI_TEK": 312.4942, "ENERJI_GUNDUZ": 316.4941, "ENERJI_PUANT": 507.6545,
                                                    "ENERJI_GECE": 162.2084}  # fmt: skip
    og = table.values[("OG_CIFT", "SANAYI")]
    assert og["GUC"] == 3557.5915 and og["DAGITIM"] == 107.0498 and og["REAKTIF"] == 349.3698
    assert table.values[("OG_TEK", "SANAYI")]["DAGITIM"] == 118.2457
    assert table.values[("AG_TEK", "SANAYI")]["ENERJI_PUANT"] == 515.7441
    assert table.values[("OG_CIFT", "MESKEN")]["GUC"] == 8378.9424
    assert ("AG_TEK", "MESKEN_DUSUK") in table.values and ("AG_TEK", "KAMU_OZEL_YUKSEK") in table.values
    assert {section for section, _ in table.values} == {"ILETIM", "OG_CIFT", "OG_TEK", "AG_TEK"}  # producers left out


def test_missing_sheet_or_components_are_errors():
    with pytest.raises(UnexpectedResponse, match="sayfası yok"):
        tariff_table([("Yeşil Tarife Tablosu", Grid("", [["Yeşil Enerji Bedeli"], [339.7]]))], "t")
    no_night = Grid("", [[None, None, "Tek Zamanlı", "Gündüz", "Puant", "Dağıtım Bedeli"], [None, "Alçak Gerilim"],
                         [None, "Tek Terimli"], [None, "Sanayi", 1.0, 1.0, 1.0, 1.0]])  # fmt: skip
    with pytest.raises(UnexpectedResponse, match="ENERJI_GECE"):
        tariff_table([("Tarife Tablosu", no_night)], "t")
    assert read_grid(Grid("", [["başlık yok"], [1.0, 2.0]])).values == {}


def test_implausible_values_are_rejected():
    grid = Grid("", [[None, None, "Tek Zamanlı", "Gündüz", "Puant", "Gece", "Dağıtım Bedeli"], [None, "AG Tek Terim"],
                     [None, "Sanayi", -5.0, 1.0, 1.0, 1.0, 1.0]])  # fmt: skip
    with pytest.raises(UnexpectedResponse, match="olağan dışı"):
        read_grid(grid)


# --- Fetch -----------------------------------------------------------------------------------------------------


@respx.mock
def test_fetch_writes_the_tariff_in_force_at_each_month_end(tariffs, tmp_path):
    route = _site(FILES)
    tariffs._raw_store = RawStore(tmp_path)
    codes = ["EPDK.TARIFE.SANAYI.AG.ENERJI", "EPDK.TARIFE.SANAYI.OG.GUC", "EPDK.TARIFE.SANAYI.OG.ENERJI.MWH"]
    df = tariffs.fetch(_req(codes, date(2025, 2, 1), date(2026, 12, 31)))
    ag = df[df["series_id"] == codes[0]].set_index("date")["value"]
    assert ag[date(2025, 3, 31)] == pytest.approx(0.2 * 2.693343)  # the 2021 tariff until the April 2025 decision
    assert ag[date(2025, 4, 30)] == pytest.approx(2.693343)
    assert ag[date(2026, 3, 31)] == pytest.approx(2.693343) and ag[date(2026, 4, 30)] == pytest.approx(2.985253)
    assert max(ag.index) == date(2026, 10, 31)  # the current month (November 2026) is not complete
    guc = df[df["series_id"] == codes[1]].set_index("date")["value"]
    assert guc[date(2025, 4, 30)] == pytest.approx(26.938043) and guc[date(2026, 4, 30)] == pytest.approx(35.575915)
    mwh = df[df["series_id"] == codes[2]].set_index("date")["value"]
    assert mwh[date(2026, 4, 30)] == pytest.approx(2909.687)
    assert set(df["unit"]) == {"try_per_kwh", "try_per_kw_month", "try_per_mwh"} and set(df["freq"]) == {"M"}
    assert set(df["entity_id"]) == {""} and set(df["publication_lag_days"]) == {0} and set(df["currency"]) == {"TRY"}
    assert route.call_count == 3  # one download per decision in use, cached for every series
    assert len(list(tmp_path.rglob("*"))) > 0


@respx.mock
def test_tiered_groups_fall_back_to_the_named_tier(tariffs):
    _site(FILES)
    code = "EPDK.TARIFE.MESKEN.AG.ENERJI"
    df = tariffs.fetch(_req(code, date(2020, 11, 1), date(2025, 4, 30))).set_index("date")["value"]
    assert df[date(2020, 11, 30)] == pytest.approx(0.19 * 1.20)  # single residential rate
    assert df[date(2025, 4, 30)] == pytest.approx(0.494065)  # low tier after the split


@respx.mock
def test_months_before_2016_are_not_requested(tariffs):
    route = respx.post(LIST_URL).mock(return_value=_json(LISTING))
    assert tariffs.fetch(_req("EPDK.TARIFE.SANAYI.AG.ENERJI", date(2014, 1, 1), date(2015, 12, 31))).empty
    assert not route.called


@respx.mock
def test_months_without_a_known_tariff_are_skipped(tariffs):
    _site(FILES)
    df = tariffs.fetch(_req("EPDK.TARIFE.SANAYI.AG.ENERJI", date(2017, 11, 1), date(2018, 2, 28)))
    assert list(df["date"]) == [date(2018, 1, 31), date(2018, 2, 28)]


@pytest.mark.parametrize(
    ("listing", "download", "match"),
    [
        (httpx.Response(302, headers={"location": "/"}), None, "yönlendirmesi"),
        (httpx.Response(500), None, "HTTP 500"),
        (httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"}), None, "JSON değil"),
        (httpx.Response(200, content=b"{bad", headers={"content-type": "application/json"}), None, "çözümlenemedi"),
        (None, httpx.Response(200, text="<html>", headers={"content-type": "text/html"}), "HTML"),
        (None, _file(b"\xd0\xcf\x11\xe0 old xls"), "okunabilir Excel"),
    ],
)
@respx.mock
def test_bad_responses(tariffs, listing, download, match):
    respx.post(LIST_URL).mock(return_value=listing or _json(LISTING))
    respx.get(DOWNLOAD).mock(return_value=download or _file(new_layout()))
    with pytest.raises(ProviderError, match=match):
        tariffs.fetch(_req("EPDK.TARIFE.SANAYI.AG.ENERJI", date(2026, 5, 1), date(2026, 5, 31)))


def test_unknown_series_units_and_transforms_rejected(tariffs, catalog):
    with pytest.raises(InvalidInput, match="bilinmeyen seri"):
        tariffs.fetch(_req("EPDK.TARIFE.YOK", date(2026, 1, 1), date(2026, 2, 1)))
    with pytest.raises(InvalidInput, match="desteklenmiyor"):
        tariffs.fetch(FetchRequest.create("epdk_tarife", ["EPDK.TARIFE.SANAYI.AG.ENERJI"], date(2026, 1, 1), date(2026, 2, 1), agg="avg"))
    source = catalog.source("epdk_tarife")
    wrong = replace(source, series=tuple(replace(s, unit="try_per_kwh") if s.code == "EPDK.TARIFE.SANAYI.OG.GUC" else s for s in source.series))
    provider = EpdkTariffProvider(wrong, http=SecureHttpClient(rps=None))
    with pytest.raises(InvalidInput, match="birimi"):
        provider.fetch(_req("EPDK.TARIFE.SANAYI.OG.GUC", date(2026, 1, 1), date(2026, 2, 1)))


def test_list_series_filters_catalog(tariffs):
    found = tariffs.list_series("puant")
    assert set(found["series_id"]) == {"EPDK.TARIFE.SANAYI.AG.PUANT", "EPDK.TARIFE.SANAYI.OG.PUANT"}
    assert len(tariffs.list_series()) == len(tariffs.source.series)


# --- Health ----------------------------------------------------------------------------------------------------


@respx.mock
def test_healthcheck_ok(tariffs):
    _site(FILES)
    result = tariffs.healthcheck()
    assert result.status is HealthStatus.OK and "14461" in result.detail and "2026-10-31" in result.detail


@respx.mock
def test_healthcheck_fails_without_recent_values(make):
    _site(FILES)
    provider = make(today=date(2017, 11, 15))  # no tariff decision known yet in the lookback window
    assert provider.healthcheck().status is HealthStatus.FAIL


@respx.mock
def test_healthcheck_reports_provider_errors(tariffs):
    respx.post(LIST_URL).mock(return_value=httpx.Response(503))
    result = tariffs.healthcheck()
    assert result.status is HealthStatus.FAIL and "HTTP 503" in result.detail


def test_healthcheck_skipped_without_canaries(catalog):
    source = catalog.source("epdk_tarife")
    bare = replace(source, series=tuple(replace(s, canary=False) for s in source.series))
    assert EpdkTariffProvider(bare, http=SecureHttpClient()).healthcheck().status is HealthStatus.SKIPPED
