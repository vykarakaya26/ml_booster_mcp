from dataclasses import replace
from datetime import date

import httpx
import pytest
import respx

from mlbooster.application.dto import FetchRequest
from mlbooster.application.errors import ProviderError, UnexpectedResponse
from mlbooster.domain.errors import InconsistentCatalog, InvalidInput
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.sdmx import SdmxCsvProvider, parse_period

CSV = (
    "FREQ,REF_AREA,TITLE,TIME_PERIOD,OBS_VALUE\n"
    'M,TR,"Turkey - Real, Broad",2026-08,104.5\n'
    'M,TR,"Turkey - Real, Broad",2026-09,105.1\n'
    'M,TR,"Turkey - Real, Broad",2026-10,\n'
)


@pytest.fixture
def bis(catalog):
    provider = SdmxCsvProvider(catalog.source("bis"), http=SecureHttpClient(rps=None, max_retries=1, sleep=lambda _: None))
    yield provider
    provider.close()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2026-10-23", date(2026, 10, 23)), ("2026-02", date(2026, 2, 28)), ("2025-Q4", date(2025, 12, 31)), ("2025", date(2025, 12, 31))],
)
def test_parse_period(raw, expected):
    assert parse_period(raw) == expected


def test_refuses_untrusted_host(catalog):
    hijacked = replace(catalog.source("bis"), base_url="https://evil.example/api/v1/data/")
    with pytest.raises(ProviderError, match="yalnızca"):
        SdmxCsvProvider(hijacked, http=SecureHttpClient())


@respx.mock
def test_fetch_uses_catalog_remote_and_parses_csv(bis):
    route = respx.get(url__startswith="https://stats.bis.org/api/v1/data/WS_EER/M.R.B.TR").mock(
        return_value=httpx.Response(200, text=CSV, headers={"content-type": "text/csv"})
    )
    df = bis.fetch(FetchRequest.create("bis", ["BIS.REER.TR"], date(2026, 8, 1), date(2026, 10, 31)))
    url = str(route.calls.last.request.url)
    assert "format=csv" in url and "startPeriod=2026-08-01" in url
    assert df["value"].tolist() == [104.5, 105.1]  # boş gözlem düşer
    assert df["date"].tolist() == [date(2026, 8, 31), date(2026, 9, 30)]
    assert set(df["freq"]) == {"M"} and df["publication_lag_days"].iloc[0] == 20


def test_unknown_alias_rejected(bis):
    with pytest.raises(InvalidInput, match="bilinmeyen seri"):
        bis.fetch(FetchRequest.create("bis", ["WS_EER"], date(2026, 1, 1)))


@respx.mock
def test_multiple_series_and_html_rejected(bis):
    dup = CSV + 'M,TR,"x",2026-09,99\n'
    respx.get(url__startswith="https://stats.bis.org/").mock(return_value=httpx.Response(200, text=dup))
    with pytest.raises(UnexpectedResponse, match="birden fazla"):
        bis.fetch(FetchRequest.create("bis", ["BIS.REER.TR"], date(2026, 1, 1)))
    respx.get(url__startswith="https://stats.bis.org/").mock(return_value=httpx.Response(200, text="<html/>", headers={"content-type": "text/html"}))
    with pytest.raises(UnexpectedResponse, match="HTML"):
        bis.fetch(FetchRequest.create("bis", ["BIS.REER.TR"], date(2026, 1, 1)))


@respx.mock
def test_404_means_no_data(bis):
    respx.get(url__startswith="https://stats.bis.org/").mock(return_value=httpx.Response(404, text="NoResultsFound"))
    assert bis.fetch(FetchRequest.create("bis", ["BIS.REER.TR"], date(2026, 1, 1))).empty


@pytest.mark.parametrize("remote", ["../secret/x", "WS_EER/..", "a/b/c", "WS EER/x", "WS_EER/x?y=1"])
def test_remote_path_validation(tmp_path, remote):
    from mlbooster.infrastructure.catalog_yaml import YamlCatalogRepository

    (tmp_path / "sources").mkdir()
    (tmp_path / "sectors").mkdir()
    (tmp_path / "sources" / "x.yaml").write_text(
        f"""id: bis
name: X
owner: X
base_url: https://stats.bis.org/api/v1/data/
access: rest_api
auth: {{type: none}}
license: {{notes: t}}
frequency: [M]
formats: [csv]
sectors: []
publication_lag_days: 0
verification_status: reported
series:
  - {{code: BIS.X, name: x, freq: M, remote: "{remote}"}}
"""
    )
    with pytest.raises(InconsistentCatalog):
        YamlCatalogRepository(tmp_path).load()
