import asyncio

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
    assert set(tools) == {"list_sectors", "list_sources", "show_map", "discover_series", "fetch_series", "build_panel", "health"}
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


def test_markdown_renders_every_sector():
    c = cli.container()
    for sector in c.list_sectors():
        assert "## Kaynaklar" in sector_map_markdown(c.sector_map(sector.id))
