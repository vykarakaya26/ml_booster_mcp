"""Ortam ayarları. Sırlar yalnızca ortam değişkeninden / .env'den okunur, asla katalogdan."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parents[1]
USER_AGENT = "mlbooster/0.2 (ML Booster Map)"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    catalog_dir: Path

    @classmethod
    def from_env(cls, load_env_files: bool = True) -> Settings:
        if load_env_files:
            # Önce çalışma dizinindeki .env, sonra ~/.mlbooster/.env. Mevcut değişkenler ezilmez.
            load_dotenv(find_dotenv(usecwd=True), override=False)
            load_dotenv(Path.home() / ".mlbooster" / ".env", override=False)
        return cls(
            data_dir=Path(os.environ.get("MLBOOSTER_DATA_DIR", Path.home() / ".mlbooster" / "data")).expanduser(),
            catalog_dir=Path(os.environ.get("MLBOOSTER_CATALOG_DIR", PACKAGE_DIR / "catalog_data")).expanduser(),
        )


def ensure_private_dir(path: Path) -> Path:
    """Eksik dizinleri (ara dizinler dahil) yalnızca sahibinin erişebileceği izinlerle (0700) oluşturur.
    `mkdir(parents=True, mode=...)` modu yalnızca son dizine uygular; bu yüzden tek tek oluşturulur.
    Önceden var olan dizinlerin izinlerine dokunulmaz."""
    for directory in [*reversed(path.parents), path]:
        if not directory.exists():
            directory.mkdir(mode=0o700, exist_ok=True)
    return path
