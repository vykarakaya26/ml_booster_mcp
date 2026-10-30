"""ML Booster skill'leri için bağımlılıksız rapor üretimi."""

import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"


def require_python() -> None:
    if sys.version_info < (3, 10):
        sys.exit("Python 3.10+ gerekli")
