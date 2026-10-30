"""Ortam ayarları. Sırlar yalnızca ortam değişkeninden / .env'den okunur."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent
USER_AGENT = "mlbooster/0.1 (ML Booster Map)"

# Önce çalışma dizinindeki .env, sonra ~/.mlbooster/.env. Mevcut ortam değişkenleri ezilmez.
load_dotenv(find_dotenv(usecwd=True), override=False)
load_dotenv(Path.home() / ".mlbooster" / ".env", override=False)


def catalog_dir() -> Path:
    return Path(os.environ.get("MLBOOSTER_CATALOG_DIR", PACKAGE_DIR / "catalog_data"))


def data_dir() -> Path:
    return Path(os.environ.get("MLBOOSTER_DATA_DIR", Path.home() / ".mlbooster" / "data")).expanduser()


def secret(env_var: str) -> str | None:
    value = os.environ.get(env_var, "").strip()
    return value or None
