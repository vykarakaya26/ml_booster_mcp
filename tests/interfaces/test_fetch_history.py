"""`mlbooster fetch` for an incremental source (EPİAŞ, respx-mocked): the default history is pulled in chunks once,
later calls only request the missing days."""

import json
from datetime import date, datetime, timedelta

import httpx
import pytest
import respx
from typer.testing import CliRunner

from mlbooster.infrastructure.providers import epias
from mlbooster.interfaces import cli, mcp_server

PTF_URL = "https://seffaflik.epias.com.tr/electricity-service/v1/markets/dam/data/mcp"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("MLBOOSTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EPIAS_USERNAME", "kullanici@example.com")
    monkeypatch.setenv("EPIAS_PASSWORD", "s3cret-Pass-0123")
    monkeypatch.setattr("time.sleep", lambda _: None)  # rps limiter
    epias.TICKETS.clear()
    cli.container.cache_clear()
    mcp_server.container.cache_clear()
    yield
    epias.TICKETS.clear()


def _first_day(request: httpx.Request) -> httpx.Response:
    """Answers with the 24 hours of the first requested day."""
    day = datetime.fromisoformat(json.loads(request.content)["startDate"]).date()
    items = [{"date": f"{day}T{h:02d}:00:00+03:00", "hour": f"{h:02d}:00", "price": 100.0 + h} for h in range(24)]
    return httpx.Response(200, json={"items": items})


@respx.mock
def test_fetch_without_start_pulls_five_years_once_then_only_new_days(respx_mock):
    respx_mock.post(epias.TICKET_URL).mock(return_value=httpx.Response(201, text="TGT-1-abcDEF0123456789-x"))
    data = respx_mock.post(PTF_URL).mock(side_effect=_first_day)
    runner, today = CliRunner(), date.today()
    first = runner.invoke(cli.app, ["fetch", "--source", "epias", "--series", "EPIAS.PTF"])
    start = date(today.year - 5, today.month, 1)
    assert first.exit_code == 0, first.output
    assert f"Kaynaktan çekilen dönem: {start} → {today}" in first.output
    assert data.call_count == 6  # one request per year at most (PTF), cut at month ends

    second = runner.invoke(cli.app, ["fetch", "--source", "epias", "--series", "EPIAS.PTF"])
    last = max(datetime.fromisoformat(json.loads(c.request.content)["startDate"]).date() for c in data.calls[:6])
    assert f"Kaynaktan çekilen dönem: {last - timedelta(days=7)} → {today}" in second.output
    assert data.call_count == 7 and "EPIAS.PTF" in second.output

    window = runner.invoke(
        cli.app,
        [
            "fetch",
            "--source",
            "epias",
            "--series",
            "EPIAS.PTF",
            "--start",
            f"{start}",
            "--end",
            f"{start + timedelta(days=30)}",
        ],
    )
    assert "Yerel depo güncel" in window.output and data.call_count == 7

    out = mcp_server.fetch_series(["EPIAS.PTF"], source="epias", start=f"{today.year - 2}-01-01", refresh=True)
    assert out["fetched"] == [(f"{today.year - 2}-01-01", today.isoformat())] and data.call_count > 7
