"""Dependency-free report generation for the ML Booster skills."""

import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"


def require_python() -> None:
    if sys.version_info < (3, 10):
        sys.exit("Python 3.10 veya üzeri gereklidir")
