import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest
import respx

from mlbooster.application.dto import FetchRequest
from mlbooster.application.errors import ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.model import HealthStatus
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.bddk import BddkProvider, _Availability, parse_remote, row_key
from mlbooster.infrastructure.providers.registry import PROVIDERS
from mlbooster.infrastructure.storage.raw_store import RawStore

# Responses recorded from www.bddk.org.tr (Aylık Bülten, table 1) on 2026-11-02, rows trimmed to the ones used.
FIXTURES = Path(__file__).parent / "fixtures" / "bddk"
URL = "https://www.bddk.org.tr/BultenAylik/tr/Home/BasitRaporGetir"
GRUP = ["10001", "10003", "10004", "10008", "10009", "10010"]


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _json(name: str) -> httpx.Response:
    return httpx.Response(200, json=_fixture(name))


def _form(call) -> list[tuple[str, str]]:
    return parse_qsl(call.request.content.decode("ascii"))


def _by_month(responses: dict[tuple[str, str], str]):
    """respx side effect: answers by the (yil, ay) form fields; unknown months fail the test."""

    def handler(request: httpx.Request) -> httpx.Response:
        form = dict(parse_qsl(request.content.decode("ascii")))
        return _json(responses[(form["yil"], form["ay"])])

    return handler


@pytest.fixture
def bddk(catalog):
    provider = BddkProvider(catalog.source("bddk"), http=SecureHttpClient(rps=None, max_retries=1, sleep=lambda _: None))
    yield provider
    provider.close()


def test_registered_and_catalog_points_to_adapter(catalog):
    source = catalog.source("bddk")
    assert PROVIDERS[source.provider] is BddkProvider
    assert source.canaries and all(parse_remote(s.remote) for s in source.series)


def test_refuses_untrusted_host(catalog):
    hijacked = replace(catalog.source("bddk"), base_url="https://evil.example/BultenAylik/")
    with pytest.raises(ProviderError, match="yalnızca"):
        BddkProvider(hijacked, http=SecureHttpClient())


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("TOPLAM AKTİFLER", "TOPLAM_AKTIFLER"),
        ("TOPLAM AKTİFLER (1+..+20)-(12+13)", "TOPLAM_AKTIFLER"),  # 2002 label
        ("Krediler*", "KREDILER"),
        ("Mevduat (Katılım Fonu)***", "MEVDUAT"),
        ("TOPLAM ÖZKAYNAKLAR (39+..+45)", "TOPLAM_OZKAYNAKLAR"),
        ("a) Vadesiz Mevduat", "A_VADESIZ_MEVDUAT"),
        ("İştirakler,  Bağlı  ve Birlikte Kontrol Edilen Ort.", "ISTIRAKLER_BAGLI_VE_BIRLIKTE_KONTROL_EDILEN_ORT"),
    ],
)
def test_row_key_is_stable_across_label_versions(label, key):
    assert row_key(label) == key


def test_parse_remote():
    assert parse_remote("1/TOPLAM_AKTIFLER") == (1, ("10001",), "TOPLAM_AKTIFLER", "Toplam")
    assert parse_remote("1,10002+10009/MEVDUAT.Tp") == (1, ("10002", "10009"), "MEVDUAT", "Tp")


@pytest.mark.parametrize(
    "remote", ["TOPLAM_AKTIFLER", "1/toplam", "1,1001/KREDILER", "1,10001+10001/KREDILER", "x,10001/KREDILER", "1/../X"]
)
def test_parse_remote_rejects_malformed(remote):
    with pytest.raises(InvalidInput, match="remote"):
        parse_remote(remote)


@respx.mock
def test_fetch_posts_form_and_maps_groups_to_entities(bddk, tmp_path):
    bddk._raw_store = RawStore(tmp_path)
    route = respx.post(URL).mock(side_effect=_by_month({("2026", "8"): "grup_2026_08.json", ("2026", "9"): "grup_2026_09.json"}))
    df = bddk.fetch(FetchRequest.create("bddk", ["BDDK.AKTIF", "BDDK.KREDI"], date(2026, 8, 1), date(2026, 9, 30)))

    assert route.call_count == 2  # one request per month for both series of the same table
    form = _form(route.calls.last)
    assert form[:4] == [("tabloNo", "1"), ("yil", "2026"), ("ay", "9"), ("paraBirimi", "TL")]
    assert [v for k, v in form if k == "taraf"] == GRUP
    assert route.calls.last.request.headers["content-type"] == "application/x-www-form-urlencoded"

    sep = df[df["date"] == date(2026, 9, 30)].set_index(["series_id", "entity_id"])["value"]
    assert sep[("BDDK.AKTIF", "")] == 56982049  # matches the 30 Oct 2026 news bulletin
    assert sep[("BDDK.KREDI", "")] == 28765658
    assert sep[("BDDK.AKTIF", "10009")] == 21491757
    groups = sep.loc["BDDK.AKTIF"].drop("")
    assert abs(groups.sum() - sep[("BDDK.AKTIF", "")]) <= 2  # the bank groups add up to the sector
    names = dict(zip(df["entity_id"], df["entity_name"], strict=True))
    assert names == {
        "": "Sektör", "10003": "Katılım", "10004": "Kalkınma ve Yatırım",
        "10008": "Mevduat-Yerli Özel", "10009": "Mevduat-Kamu", "10010": "Mevduat-Yabancı",
    }  # fmt: skip
    assert set(df["dataset_id"]) == {"aylik_bulten_1"} and set(df["unit"]) == {"million_try"}
    assert set(df["currency"]) == {"TRY"} and set(df["freq"]) == {"M"} and set(df["metric"]) == {"level"}
    assert not df["is_proxy"].any() and set(df["publication_lag_days"]) == {30}
    assert len(df) == 2 * 2 * 6
    manifest = (tmp_path / "bddk" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(manifest) == 2 and "yil=2026&ay=9" in json.loads(manifest[-1])["url"]


@respx.mock
def test_groups_of_several_series_are_requested_once_and_mapped_by_code(bddk):
    route = respx.post(URL).mock(return_value=_json("all_2026_09.json"))
    df = bddk.fetch(FetchRequest.create("bddk", ["BDDK.AKTIF.SAHIPLIK", "BDDK.MEVDUAT"], date(2026, 9, 1), date(2026, 9, 30)))
    assert route.call_count == 1
    assert [v for k, v in _form(route.calls.last) if k == "taraf"] == [f"100{i:02d}" for i in (1, 3, 4, 5, 6, 7, 8, 9, 10)]
    own = df[df["series_id"] == "BDDK.AKTIF.SAHIPLIK"].set_index("entity_id")
    assert own["entity_name"].to_dict() == {"": "Sektör", "10006": "Kamu", "10005": "Yerli Özel", "10007": "Yabancı"}
    assert own.loc["10006", "value"] == 26889201 and abs(own["value"].drop("").sum() - own.loc["", "value"]) <= 2
    assert set(df[df["series_id"] == "BDDK.MEVDUAT"]["entity_id"]) == {"", *GRUP[1:]}


@respx.mock
def test_old_labels_and_tp_column(bddk):
    respx.post(URL).mock(return_value=_json("grup_2002_12.json"))
    df = bddk.fetch(
        FetchRequest.create("bddk", ["BDDK.AKTIF", "BDDK.MEVDUAT", "BDDK.KREDI.TP"], date(2002, 12, 1), date(2002, 12, 31))
    )
    total = df[df["entity_id"] == ""].set_index("series_id")["value"]
    assert total["BDDK.AKTIF"] == 212681 and total["BDDK.MEVDUAT"] == 137973  # 2002 labels carry formulas and stars
    assert 0 < total["BDDK.KREDI.TP"] < 48981  # TL part of the 48,981 million TL loans


@respx.mock
def test_unpublished_months_are_skipped_after_the_first_message(bddk):
    route = respx.post(URL).mock(
        side_effect=_by_month({("2026", "9"): "grup_2026_09.json", ("2026", "10"): "latest_2026_10.json", ("2027", "1"): "no_year_2027_01.json"})
    )
    df = bddk.fetch(FetchRequest.create("bddk", ["BDDK.AKTIF"], date(2026, 9, 1), date(2027, 3, 31)))
    assert sorted(set(df["date"])) == [date(2026, 9, 30)]
    asked = [(dict(_form(c))["yil"], dict(_form(c))["ay"]) for c in route.calls]
    assert asked == [("2026", "9"), ("2026", "10"), ("2027", "1")]  # Nov/Dec 2026 and Feb/Mar 2027 not requested


@respx.mock
def test_first_year_messages(bddk):
    route = respx.post(URL).mock(
        side_effect=_by_month({("2002", "1"): "only_2002_01.json", ("2002", "12"): "grup_2002_12.json"})
    )
    df = bddk.fetch(FetchRequest.create("bddk", ["BDDK.AKTIF"], date(1999, 1, 1), date(2002, 12, 31)))
    assert set(df["date"]) == {date(2002, 12, 31)} and route.call_count == 2  # nothing before 2002 is requested


def test_availability_learns_from_messages():
    seen = _Availability()
    assert seen.learn(_fixture("starts_2001_12.json")["error"]) and not seen.has(2001, 12) and seen.has(2002, 1)
    assert seen.learn(_fixture("latest_2026_10.json")["error"]) and seen.has(2026, 9) and not seen.has(2026, 10)
    assert seen.learn(_fixture("no_year_2027_01.json")["error"]) and not seen.has(2027, 6)
    assert not seen.learn("Basit Rapor üretilirken hata oluştu!")


@respx.mock
@pytest.mark.parametrize(
    ("response", "error", "match"),
    [
        (httpx.Response(302, headers={"location": "https://www.bddk.org.tr/"}), UnexpectedResponse, "yönlendirme"),
        (httpx.Response(403, text="Forbidden"), ProviderError, "HTTP 403"),
        (httpx.Response(200, text="<!doctype html>", headers={"content-type": "text/html"}), UnexpectedResponse, "JSON yerine"),
        (httpx.Response(200, text="{bozuk", headers={"content-type": "application/json"}), UnexpectedResponse, "çözümlenemedi"),
        (httpx.Response(200, json=[1]), UnexpectedResponse, "nesne"),
        (httpx.Response(200, json={"success": False, "error": "Basit Rapor üretilirken hata oluştu!"}), UnexpectedResponse, "üretilemedi"),
        (httpx.Response(200, json={"success": False}), UnexpectedResponse, "boş mesaj"),
        (httpx.Response(200, json={"success": True, "Json": "x"}), UnexpectedResponse, "gövdesi"),
        (httpx.Response(200, json={"success": True, "Json": {"colModels": []}}), UnexpectedResponse, "satır listesi"),
        (_json("error_bad_group.json"), UnexpectedResponse, "6 grup istendi, 0 grup"),
    ],
)
def test_bad_responses(bddk, response, error, match):
    respx.post(URL).mock(return_value=response)
    with pytest.raises(error, match=match):
        bddk.fetch(FetchRequest.create("bddk", ["BDDK.AKTIF"], date(2026, 9, 1), date(2026, 9, 30)))


def _mutated(change) -> httpx.Response:
    doc = _fixture("grup_2026_09.json")
    change(doc["Json"])
    return httpx.Response(200, json=doc)


@respx.mock
@pytest.mark.parametrize(
    ("change", "match"),
    [
        (lambda j: j["colModels"].pop(0), "kolon yapısı"),
        (lambda j: [r["cell"].__setitem__(2, "Başka") for r in j["data"]["rows"]], "TOPLAM_AKTIFLER' satırı"),
        (lambda j: [c.__setitem__("name", "X") for c in j["colModels"][4:]], "'Toplam' kolonu"),
        (lambda j: j["data"]["rows"][1]["cell"].__setitem__(6, "1.234"), "sayısal olmayan"),
    ],
)
def test_changed_report_layout_is_reported(bddk, change, match):
    respx.post(URL).mock(return_value=_mutated(change))
    with pytest.raises(UnexpectedResponse, match=match):
        bddk.fetch(FetchRequest.create("bddk", ["BDDK.AKTIF"], date(2026, 9, 1), date(2026, 9, 30)))


@respx.mock
def test_empty_cells_are_skipped(bddk):
    def blank(j):
        for r in j["data"]["rows"]:
            if r["cell"][0] == "Katılım":
                r["cell"][6] = None

    respx.post(URL).mock(return_value=_mutated(blank))
    df = bddk.fetch(FetchRequest.create("bddk", ["BDDK.AKTIF"], date(2026, 9, 1), date(2026, 9, 30)))
    assert "10003" not in set(df["entity_id"]) and len(df) == 5


def test_unknown_series_and_transforms_rejected(bddk):
    with pytest.raises(InvalidInput, match="bilinmeyen seri"):
        bddk.fetch(FetchRequest.create("bddk", ["BDDK.YOK"], date(2026, 1, 1)))
    with pytest.raises(InvalidInput, match="desteklenmiyor"):
        bddk.fetch(FetchRequest.create("bddk", ["BDDK.AKTIF"], date(2026, 1, 1), formula=1))


def test_fetch_of_an_empty_range_returns_no_rows(bddk):
    assert bddk.fetch(FetchRequest.create("bddk", ["BDDK.AKTIF"], date(1990, 1, 1), date(1990, 12, 31))).empty


def test_list_series_filters_catalog(bddk):
    assert "BDDK.KREDI" in bddk.list_series()["series_id"].tolist()
    found = bddk.list_series("SAHİPLİK")
    assert set(found["series_id"]) == {"BDDK.AKTIF.SAHIPLIK", "BDDK.KREDI.SAHIPLIK", "BDDK.MEVDUAT.SAHIPLIK"}


def _no_year(request: httpx.Request) -> httpx.Response:
    year = dict(parse_qsl(request.content.decode("ascii")))["yil"]
    return httpx.Response(200, json={"success": False, "error": f"{year} yılına ait veri bulunmamaktadır!"})


def _published_until(last: date):
    """Side effect mimicking the bulletin: the recorded report up to `last` (the period comes from the request),
    the 'latest month' message after it within its year and the 'no data' message for later years."""

    def handler(request: httpx.Request) -> httpx.Response:
        form = dict(parse_qsl(request.content.decode("ascii")))
        year, month = int(form["yil"]), int(form["ay"])
        if (year, month) <= (last.year, last.month):
            return _json("grup_2026_09.json")
        if year == last.year:
            error = f"{year} yılının en son {last.month} ayına ait veri bulunmaktadır!"
            return httpx.Response(200, json={"success": False, "error": error})
        return _no_year(request)

    return handler


@respx.mock
def test_healthcheck_ok(bddk):
    last_month = date.today().replace(day=1) - timedelta(days=1)
    respx.post(URL).mock(side_effect=_published_until(last_month))
    result = bddk.healthcheck()
    assert result.status is HealthStatus.OK, result.detail
    assert f"BDDK.AKTIF son={last_month}" in result.detail and result.checked == ("BDDK.AKTIF",)


@respx.mock
def test_healthcheck_fails_without_recent_observations(bddk):
    respx.post(URL).mock(side_effect=_no_year)
    result = bddk.healthcheck()
    assert result.status is HealthStatus.FAIL and "gözlem yok" in result.detail


@respx.mock
def test_healthcheck_fails_on_stale_series(bddk, monkeypatch):
    monkeypatch.setattr("mlbooster.infrastructure.providers.bddk._HEALTH_LOOKBACK_DAYS", 400)
    respx.post(URL).mock(side_effect=_published_until(date.today() - timedelta(days=400)))
    result = bddk.healthcheck()
    assert result.status is HealthStatus.FAIL and "son gözlem" in result.detail


@respx.mock
def test_healthcheck_reports_provider_errors(bddk):
    respx.post(URL).mock(return_value=httpx.Response(403, text="Forbidden"))
    result = bddk.healthcheck()
    assert result.status is HealthStatus.FAIL and "HTTP 403" in result.detail


def test_healthcheck_skipped_without_canaries(catalog):
    source = replace(catalog.source("bddk"), series=())
    assert BddkProvider(source, http=SecureHttpClient()).healthcheck().status is HealthStatus.SKIPPED
