"""Bronze: ham yanıtlar, içerik hash'i ve çekim zamanıyla (vintage)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

_EXT = {"json": "json", "xml": "xml", "csv": "csv", "pdf": "pdf", "spreadsheet": "xlsx", "html": "html"}


class BronzeStore:
    def __init__(self, root: Path):
        self.root = root

    def save(self, source_id: str, url: str, content: bytes, content_type: str) -> Path:
        digest = hashlib.sha256(content).hexdigest()
        ext = next((e for k, e in _EXT.items() if k in content_type.lower()), "bin")
        directory = self.root / source_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{digest[:20]}.{ext}"
        if not path.exists():
            path.write_bytes(content)
        record = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "url": url,  # URL'de sır bulunmaz; anahtarlar header'da gider
            "sha256": digest,
            "content_type": content_type,
            "path": path.name,
        }
        with (directory / "manifest.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path
