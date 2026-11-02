"""Bronze: raw responses, with content hash and fetch time."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from mlbooster.domain.validation import slug
from mlbooster.infrastructure.settings import ensure_private_dir

_FILE = re.compile(r"[0-9a-f]{32}\.[a-z]{2,4}")  # file names written by save()
_EXT = {"json": "json", "xml": "xml", "csv": "csv", "pdf": "pdf", "spreadsheet": "xlsx", "html": "html"}


class RawStore:
    def __init__(self, root: Path):
        self._root = root

    def save(self, source_id: str, url: str, content: bytes, content_type: str) -> Path:
        digest = hashlib.sha256(content).hexdigest()
        ext = next((e for k, e in _EXT.items() if k in content_type.lower()), "bin")
        directory = ensure_private_dir(self._root / slug(source_id, "source"))
        path = directory / f"{digest[:32]}.{ext}"  # the file name is derived only from the hash
        if not path.exists():
            path.write_bytes(content)
        record = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "url": url,  # the URL contains no secrets; keys go in the header
            "sha256": digest,
            "content_type": content_type[:100],
            "file": path.name,
        }
        with (directory / "manifest.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path

    def load(self, source_id: str, url: str) -> bytes | None:
        """Content saved last for `url`, if its file is still intact. Meant for immutable downloads (report files whose
        URL changes with every new version), so that they are not downloaded again."""
        directory = self._root / slug(source_id, "source")
        manifest = directory / "manifest.jsonl"
        if not manifest.is_file():
            return None
        record = None
        with manifest.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict) and item.get("url") == url:
                    record = item
        name = str(record.get("file", "")) if record else ""
        if not _FILE.fullmatch(name) or not (directory / name).is_file():
            return None
        content = (directory / name).read_bytes()
        return content if hashlib.sha256(content).hexdigest() == record.get("sha256") else None
