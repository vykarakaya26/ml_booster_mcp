"""Drift checks: facts stated in several places (code, packaging, docs) must agree.

Each test pins one rule from CLAUDE.md so it cannot silently go stale.
"""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

import mlbooster

REPO = Path(__file__).resolve().parents[1]
MCP_SERVER = REPO / "src" / "mlbooster" / "interfaces" / "mcp_server.py"
PATH_LIKE = re.compile(r"(^|_)(path|file|dir|directory|folder)s?($|_)")


def _mcp_tools() -> dict[str, list[str]]:
    """{tool name: parameter names} for every function decorated with `@server.tool(...)`."""
    tree = ast.parse(MCP_SERVER.read_text(encoding="utf-8"))
    tools = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Attribute) and target.attr == "tool":
                args = node.args
                tools[node.name] = [a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
    return tools


def test_package_version_matches_pyproject():
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == mlbooster.__version__


def test_plugin_tracks_commits_and_has_no_project_mcp_config():
    plugin = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert "version" not in plugin, "plugin.json must not pin a version; the plugin follows the git commit"
    assert "${CLAUDE_PLUGIN_ROOT}" in plugin["mcpServers"]["mlbooster"]["args"]
    assert not (REPO / ".mcp.json").exists(), "a root .mcp.json would run as a project MCP without the plugin root"
    assert len(plugin["keywords"]) == len(set(plugin["keywords"])), "duplicate plugin keywords"


def test_mcp_tools_take_no_file_paths():
    offenders = {name: p for name, params in _mcp_tools().items() for p in params if PATH_LIKE.search(p)}
    assert not offenders, f"MCP tools must not accept file paths: {offenders}"


def test_readme_lists_every_mcp_tool():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    tools = _mcp_tools()
    assert len(tools) >= 10
    missing = [t for t in tools if f"`{t}`" not in readme]
    assert not missing, f"README does not mention MCP tools: {missing}"


def test_claude_md_lists_every_skill():
    claude_md = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    skills = sorted(p.parent.name for p in (REPO / "skills").glob("*/SKILL.md"))
    missing = [s for s in skills if s not in claude_md]
    assert not missing, f"CLAUDE.md does not mention skills: {missing}"
