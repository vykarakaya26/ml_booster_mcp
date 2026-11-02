import asyncio

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from mlbooster.interfaces import cli, mcp_server
from mlbooster.interfaces.presenters import sector_map_markdown


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("MLBOOSTER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("EVDS_API_KEY", raising=False)
    cli.container.cache_clear()
    mcp_server.container.cache_clear()


def test_mcp_tools_registered_with_annotations():
    tools = {t.name: t for t in asyncio.run(mcp_server.server.list_tools())}
    assert set(tools) == {
        "list_sectors", "list_sources", "show_map", "discover_series", "fetch_series", "build_panel", "health",
        "search_series", "set_feature_series", "audit_panel", "run_backtest", "check_updates",
        "analyze_market_share", "analyze_source_market_share", "prepare_panel", "engineer_features",
    }  # fmt: skip
    assert tools["show_map"].annotations.read_only_hint is True
    assert tools["fetch_series"].annotations.destructive_hint is False


def test_mcp_tool_rejects_injection_with_safe_message():
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="Geçersiz seri"):
        mcp_server.fetch_series(["TP.TUKFIY2025.GENEL&type=xml"])
    with pytest.raises(ToolError, match="Geçersiz sektör"):
        mcp_server.show_map("../../etc/passwd")


def test_mcp_show_map(catalog):
    assert "| Kaynak |" in mcp_server.show_map("sigorta")


def test_cli_commands():
    runner = CliRunner()
    assert "bankacilik" in runner.invoke(cli.app, ["catalog", "sectors"]).output
    out = runner.invoke(cli.app, ["map", "show", "--sector", "telekom"])
    assert out.exit_code == 0 and "BTK" in out.output
    bad = runner.invoke(cli.app, ["fetch", "--series", "TP&x=1"])
    assert bad.exit_code == 1 and "Geçersiz" in bad.output
    health = runner.invoke(cli.app, ["health", "--source", "evds3"])
    assert health.exit_code == 0 and "skipped" in health.output


def test_markdown_shows_turkish_status_labels(catalog):
    md = mcp_server.show_map("bankacilik")
    assert "| Durum |" in md and "pending_discovery" not in md and "no_provider" not in md


def test_markdown_renders_every_sector():
    c = cli.container()
    for sector in c.list_sectors():
        assert "## Kaynaklar" in sector_map_markdown(c.sector_map(sector.id))


def test_mcp_market_share_inline_records():
    from mcp.server.mcpserver.exceptions import ToolError

    records = [
        {"entity": e, "date": f"2025-Q{q}", "value": v + q}
        for q in (1, 2, 3, 4)
        for e, v in (("A", 50), ("B", 30), ("C", 20))
    ]
    out = mcp_server.analyze_market_share(records, focus="B", kind="stock")
    assert out["meta"]["periods_per_year"] == 4 and out["competitors"]["focus"] == "B"
    with pytest.raises(ToolError, match="Odak oyuncu"):
        mcp_server.analyze_market_share(records, focus="Z")


def test_cli_market_share(tmp_path):
    src = tmp_path / "share.csv"
    lines = ["entity;date;value"] + [f"{e};2025-{m:02d};{v + m},5" for m in range(1, 13) for e, v in (("A", 50), ("B", 30))]
    src.write_text("\n".join(lines), encoding="utf-8")
    out = CliRunner().invoke(cli.app, ["market-share", "--input", str(src), "--focus", "A", "--json", str(tmp_path / "o.json")])
    assert out.exit_code == 0, out.output
    assert "HHI" in out.output and (tmp_path / "o.json").exists()
    assert "CR3 %" in out.output and "pay kazanıyor" in out.output  # Turkish number format and labels
    assert "gaining" not in out.output and "losing" not in out.output
    bad = CliRunner().invoke(cli.app, ["market-share", "--input", str(tmp_path / "missing.xlsx")])
    assert bad.exit_code == 1 and "Desteklenen" in bad.output


def _bddk_bulletin(monkeypatch):
    """Mocks the BDDK report endpoint: the recorded Sep 2026 report for Jan–Sep 2026, 'latest month' message after."""
    import json
    from pathlib import Path
    from urllib.parse import parse_qsl

    import httpx

    monkeypatch.setattr("mlbooster.infrastructure.http_client.RateLimiter.wait", lambda self: None)
    report = json.loads((Path(__file__).parents[1] / "infrastructure/fixtures/bddk/grup_2026_09.json").read_text(encoding="utf-8"))

    def handler(request):
        form = dict(parse_qsl(request.content.decode("ascii")))
        if form["yil"] == "2026" and int(form["ay"]) <= 9:
            return httpx.Response(200, json=report)
        return httpx.Response(200, json={"success": False, "error": f"{form['yil']} yılının en son 9 ayına ait veri bulunmaktadır!"})

    return handler


def test_cli_market_share_from_bddk_source(monkeypatch, tmp_path):
    import respx

    with respx.mock:
        route = respx.post("https://www.bddk.org.tr/BultenAylik/tr/Home/BasitRaporGetir").mock(side_effect=_bddk_bulletin(monkeypatch))
        args = ["market-share", "--source", "bddk", "--series", "BDDK.AKTIF", "--start", "2026-01", "--end", "2026-11"]
        out = CliRunner().invoke(cli.app, [*args, "--json", str(tmp_path / "o.json")])
        assert out.exit_code == 0, out.output
        assert route.call_count == 10  # Jan–Oct; November is skipped after the 'latest month' message
        again = CliRunner().invoke(cli.app, args)
        assert again.exit_code == 0 and route.call_count == 10  # served from the local store
    assert "Kaynak: BDDK" in out.output and "5 oyuncu" in out.output and "Mevduat-Kamu" in out.output
    assert "%37,72" in out.output  # 21,491,757 / 56,982,049 million TL, Sep 2026
    import json

    meta = json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))["meta"]
    assert meta["kind"] == "stock" and meta["source"]["series"] == "BDDK.AKTIF" and meta["has_total"]


def test_cli_market_share_needs_exactly_one_input(tmp_path):
    for args in ([], ["--source", "bddk"], ["--series", "BDDK.AKTIF"], ["--input", "x.csv", "--source", "bddk", "--series", "BDDK.AKTIF"]):
        out = CliRunner().invoke(cli.app, ["market-share", *args])
        assert out.exit_code == 1 and "--input ya da --source" in out.output


def test_mcp_source_market_share(monkeypatch):
    import respx
    from mcp.server.mcpserver.exceptions import ToolError

    with respx.mock:
        respx.post("https://www.bddk.org.tr/BultenAylik/tr/Home/BasitRaporGetir").mock(side_effect=_bddk_bulletin(monkeypatch))
        out = mcp_server.analyze_source_market_share("bddk", "BDDK.AKTIF", focus="Katılım", start="2026-01-01", end="2026-09-30")
    assert out["meta"]["entities"] == 5 and out["competitors"]["focus"] == "Katılım"
    assert out["meta"]["source"]["attribution"] == "Kaynak: BDDK — BDDK Aylık Bülten"
    with pytest.raises(ToolError, match="YYYY-AA-GG"):
        mcp_server.analyze_source_market_share("bddk", "BDDK.AKTIF", start="dün")
    with pytest.raises(ToolError, match="Geçersiz"):
        mcp_server.analyze_source_market_share("../x", "BDDK.AKTIF")


def test_cli_number_format_is_turkish():
    assert cli.tr_num(12345.6, ",.0f") == "12.346"
    assert cli.tr_num(31.5, ".2f") == "31,50"
    assert cli.tr_num(-0.25, "+.2f") == "-0,25"


def test_backtest_panel_file_and_reader(tmp_path):
    from mlbooster.infrastructure.table_reader import LocalTableReader

    idx = pd.date_range("2015-01-31", periods=80, freq="ME")
    frame = pd.DataFrame({"date": idx.strftime("%Y-%m-%d"), "target": np.arange(80.0) + np.sin(np.arange(80)), "x": np.cos(np.arange(80))})
    path = tmp_path / "selected.csv"
    frame.to_csv(path, index=False)
    panel = LocalTableReader().read_panel(path)
    assert isinstance(panel.index, pd.DatetimeIndex) and list(panel.columns) == ["target", "x"]
    out = CliRunner().invoke(cli.app, ["backtest", "--sector", "makro_genel", "--target", "target", "--min-train", "36",
                                       "--models", "naive,ridge", "--panel-file", str(path), "--eval-start", "2019-12"])  # fmt: skip
    assert out.exit_code == 0, out.output
    assert "en iyi" in out.output


def test_period_end_reads_months_as_month_end():
    from datetime import date

    assert cli._period_end("2022-12") == date(2022, 12, 31)
    assert cli._period_end("2024-02") == date(2024, 2, 29)
    assert cli._period_end("2022-12-15") == date(2022, 12, 15)
    assert cli._period_end(None) is None


def test_safe_tool_logs_unexpected_errors_without_message(caplog):
    @mcp_server.safe_tool
    def broken():
        raise RuntimeError("/Users/someone/secret/path")

    with caplog.at_level("ERROR", logger=mcp_server.log.name), pytest.raises(RuntimeError):
        broken()
    assert "broken" in caplog.text and "RuntimeError" in caplog.text
    assert "/Users/someone" not in caplog.text
