"""Sürüm kontrolü: GitHub'daki en son `vX.Y.Z` tag'ini kurulu sürümle karşılaştırır.

Gizlilik/güvenlik: istek yalnızca api.github.com'a gider (kimlik bilgisi yok), sonuç 24 saat yerelde
önbelleğe alınır, `MLBOOSTER_NO_UPDATE_CHECK=1` ile tamamen kapatılır. Ağ hatası sessizce "bilinmiyor"
döner; araç çalışmasını asla engellemez.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from mlbooster.application.errors import ProviderError
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.settings import ensure_private_dir

REPO = "vykarakaya26/ml_booster_mcp"
TAGS_URL = f"https://api.github.com/repos/{REPO}/tags?per_page=30"
CACHE_SECONDS = 24 * 3600
_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def parse_version(text: str) -> tuple[int, int, int] | None:
    m = _TAG.match(text if text.startswith("v") else f"v{text}")
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


class GitHubReleaseSource:
    def __init__(self, cache_path: Path, http: SecureHttpClient | None = None):
        self._cache = cache_path
        self._http = http

    def latest_version(self) -> str | None:
        if os.environ.get("MLBOOSTER_NO_UPDATE_CHECK", "").strip() in {"1", "true", "yes"}:
            return None
        try:
            cached = json.loads(self._cache.read_text(encoding="utf-8"))
            if time.time() - cached["checked_at"] < CACHE_SECONDS:
                return cached["latest"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        latest = self._fetch()
        if latest is not None:
            try:
                ensure_private_dir(self._cache.parent)
                self._cache.write_text(json.dumps({"checked_at": time.time(), "latest": latest}), encoding="utf-8")
            except OSError:
                pass
        return latest

    def _fetch(self) -> str | None:
        http = self._http or SecureHttpClient(rps=None, timeout=5.0, max_retries=0, max_bytes=512 * 1024)
        try:
            response = http.get(TAGS_URL, headers={"Accept": "application/vnd.github+json"})
            if response.status != 200:
                return None
            tags = [t.get("name", "") for t in json.loads(response.content)]
        except (ProviderError, ValueError, AttributeError):
            return None
        finally:
            if self._http is None:
                http.close()
        versions = [(parse_version(t), t) for t in tags if parse_version(t)]
        return max(versions)[1].lstrip("v") if versions else None
