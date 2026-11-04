"""Executable form of the onion dependency rule: an inner ring cannot import an outer one."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "src" / "mlbooster"
LAYERS = ["domain", "application", "infrastructure", "interfaces"]  # inside out
IO_LIBS = {"httpx", "duckdb", "pyarrow", "yaml", "mcp", "mcp_types", "typer", "pydantic", "dotenv", "requests", "starlette", "uvicorn"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _files(layer: str) -> list[Path]:
    return sorted((ROOT / layer).rglob("*.py"))


@pytest.mark.parametrize("layer", LAYERS)
def test_layer_does_not_import_outer_layers(layer):
    # The composition root (bootstrap) is in the outermost ring; only interfaces may use it.
    outer = LAYERS[LAYERS.index(layer) + 1 :] + (["bootstrap"] if layer != "interfaces" else [])
    violations = [
        f"{f.relative_to(ROOT)} → {name}"
        for f in _files(layer)
        for name in _imports(f)
        if any(name == f"mlbooster.{o}" or name.startswith(f"mlbooster.{o}.") for o in outer)
    ]
    assert not violations, "Dependency rule violation:\n" + "\n".join(violations)


@pytest.mark.parametrize("layer", ["domain", "application"])
def test_core_is_free_of_io_libraries(layer):
    violations = [
        f"{f.relative_to(ROOT)} → {name}"
        for f in _files(layer)
        for name in _imports(f)
        if name.split(".")[0] in IO_LIBS
    ]
    assert not violations, "The core imports an I/O library:\n" + "\n".join(violations)


def test_only_composition_root_wires_infrastructure_into_interfaces():
    offenders = [
        str(f.relative_to(ROOT))
        for f in _files("interfaces")
        for name in _imports(f)
        if name.startswith("mlbooster.infrastructure")
    ]
    assert not offenders, f"Interfaces must not depend on infrastructure directly (use bootstrap): {offenders}"


# Container fields that hold ports. Interfaces call use cases only; reaching a port directly bypasses
# the validation gate in the application layer (CLAUDE.md: "interfaces only call use cases").
PORT_FIELDS = {"catalog", "providers", "observations", "panels", "overrides", "updates", "tables", "_tables", "evidence_store", "_evidence", "documents"}


def _port_accesses(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in PORT_FIELDS
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "container"
        ):
            hits.append(f"{path.relative_to(ROOT)}:{node.lineno} container().{node.attr}")
    return hits


def test_interfaces_call_use_cases_not_ports():
    violations = [hit for f in _files("interfaces") for hit in _port_accesses(f)]
    assert not violations, "Interfaces must go through use cases:\n" + "\n".join(violations)


def test_evidence_stays_out_of_the_observation_and_feature_path():
    """Published figures are compiled claims: nothing that builds panels or features may even import them."""
    feature_path = ["use_cases/panel.py", "use_cases/series.py", "use_cases/modeling.py", "use_cases/market_share.py"]
    feature_path = [ROOT / "application" / p for p in feature_path]
    feature_path += [ROOT / "domain" / "services" / f for f in ("brand_features.py", "feature_engineering.py", "market_share.py")]
    offenders = [str(f.relative_to(ROOT)) for f in feature_path for name in _imports(f) if "evidence" in name]
    assert not offenders, f"Evidence must not reach panels or features: {offenders}"
