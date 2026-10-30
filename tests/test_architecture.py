"""Onion bağımlılık kuralının çalıştırılabilir hali: içteki halka dıştakini import edemez."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "src" / "mlbooster"
LAYERS = ["domain", "application", "infrastructure", "interfaces"]  # içten dışa
IO_LIBS = {"httpx", "duckdb", "pyarrow", "yaml", "mcp", "mcp_types", "typer", "pydantic", "dotenv", "requests"}


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
    # Composition root (bootstrap) en dış halkadadır; yalnızca interfaces onu kullanabilir.
    outer = LAYERS[LAYERS.index(layer) + 1 :] + (["bootstrap"] if layer != "interfaces" else [])
    violations = [
        f"{f.relative_to(ROOT)} → {name}"
        for f in _files(layer)
        for name in _imports(f)
        if any(name == f"mlbooster.{o}" or name.startswith(f"mlbooster.{o}.") for o in outer)
    ]
    assert not violations, "Bağımlılık kuralı ihlali:\n" + "\n".join(violations)


@pytest.mark.parametrize("layer", ["domain", "application"])
def test_core_is_free_of_io_libraries(layer):
    violations = [
        f"{f.relative_to(ROOT)} → {name}"
        for f in _files(layer)
        for name in _imports(f)
        if name.split(".")[0] in IO_LIBS
    ]
    assert not violations, "Çekirdek G/Ç kütüphanesi import ediyor:\n" + "\n".join(violations)


def test_only_composition_root_wires_infrastructure_into_interfaces():
    offenders = [
        str(f.relative_to(ROOT))
        for f in _files("interfaces")
        for name in _imports(f)
        if name.startswith("mlbooster.infrastructure")
    ]
    assert not offenders, f"Arayüzler altyapıya doğrudan bağlanmamalı (bootstrap kullan): {offenders}"
