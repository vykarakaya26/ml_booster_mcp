"""Bronze: ham yanıtlar, içerik hash'i ve çekim zamanıyla."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from mlbooster.domain.validation import slug
from mlbooster.infrastructure.settings import ensure_private_dir

_EXT = {"json": "json", "xml": "xml", "csv": "csv", "pdf": "pdf", "spreadsheet": "xlsx", "html": "html"}


class RawStore:
    def __init__(self, root: Path):
        self._root = root

    def save(self, source_id: str, url: str, content: bytes, content_type: str) -> Path:
        digest = hashlib.sha256(content).hexdigest()
        ext = next((e for k, e in _EXT.items() if k in content_type.lower()), "bin")
        directory = ensure_private_dir(self._root / slug(source_id, "kaynak"))
        path = directory / f"{digest[:32]}.{ext}"  # dosya adı yalnızca hash'ten türetilir
        if not path.exists():
            path.write_bytes(content)
        record = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "url": url,  # URL'de sır bulunmaz; anahtarlar header'da gider
            "sha256": digest,
            "content_type": content_type[:100],
            "file": path.name,
        }
        with (directory / "manifest.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path
