"""On-disk cache of raw upstream responses.

Two reasons this exists, both practical:

1. Google Places bills per request. Re-running ingestion while developing the
   downstream pipeline should not cost money, so raw payloads are cached and
   replayed until `--refresh` is passed.
2. Cached payloads are what the fixtures are generated *from* (`trip fixtures
   --from-cache`), which keeps mock data honest — it is real response shape,
   not something hand-invented.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Cached payloads older than this are refetched.
DEFAULT_TTL_SECONDS = 7 * 24 * 3600


class ResponseCache:
    def __init__(self, root: Path, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.root = root
        self.ttl = ttl_seconds

    def _path(self, namespace: str, key: str) -> Path:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]
        return self.root / namespace / f"{digest}.json"

    def get(self, namespace: str, key: str) -> Any | None:
        path = self._path(namespace, key)
        if not path.exists():
            return None
        if self.ttl and (time.time() - path.stat().st_mtime) > self.ttl:
            log.debug("cache expired: %s/%s", namespace, key)
            return None
        try:
            payload = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload.get("body")

    def put(self, namespace: str, key: str, body: Any) -> None:
        path = self._path(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"key": key, "fetched_at": time.time(), "body": body}, ensure_ascii=False),
            encoding="utf-8",
        )

    def entries(self, namespace: str) -> list[dict[str, Any]]:
        """All cached payloads for a namespace, used by fixture generation."""
        folder = self.root / namespace
        if not folder.is_dir():
            return []
        out = []
        for path in sorted(folder.glob("*.json")):
            try:
                out.append(json.loads(path.read_text("utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return out
