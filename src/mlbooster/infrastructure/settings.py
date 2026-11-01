"""Environment settings. Secrets are read only from environment variables / .env, never from the catalog."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parents[1]
USER_AGENT = "mlbooster/0.5 (ML Booster Map)"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    catalog_dir: Path
    overrides_path: Path

    @classmethod
    def from_env(cls, load_env_files: bool = True) -> Settings:
        home = Path(os.environ.get("MLBOOSTER_HOME", Path.home() / ".mlbooster")).expanduser()
        if load_env_files:
            # First .env in the working directory, then $MLBOOSTER_HOME/.env. Existing variables are not overwritten.
            load_dotenv(find_dotenv(usecwd=True), override=False)
            load_dotenv(home / ".env", override=False)
        return cls(
            overrides_path=Path(
                os.environ.get("MLBOOSTER_OVERRIDES", home / "catalog" / "overrides.yaml")
            ).expanduser(),
            data_dir=Path(os.environ.get("MLBOOSTER_DATA_DIR", Path.home() / ".mlbooster" / "data")).expanduser(),
            catalog_dir=Path(os.environ.get("MLBOOSTER_CATALOG_DIR", PACKAGE_DIR / "catalog_data")).expanduser(),
        )


def ensure_private_dir(path: Path) -> Path:
    """Creates missing directories (including intermediate ones) with owner-only permissions (0700).
    `mkdir(parents=True, mode=...)` applies the mode only to the last directory, so they are created one by one.
    Permissions of pre-existing directories are left untouched."""
    for directory in [*reversed(path.parents), path]:
        if not directory.exists():
            directory.mkdir(mode=0o700, exist_ok=True)
    return path
