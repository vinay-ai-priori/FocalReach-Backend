from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from scraper.config.settings import ScraperSettings


def _key_for(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


class DiskCache:
    def __init__(self, settings: ScraperSettings):
        self.dir = Path(settings.cache_dir)
        self.ttl = settings.cache_ttl_seconds
        self.dir.mkdir(parents=True, exist_ok=True)

    def get(self, url: str) -> str | None:
        path = self.dir / f"{_key_for(url)}.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if time.time() - payload.get("cached_at", 0) > self.ttl:
            return None
        return payload.get("html")

    def set(self, url: str, html: str) -> None:
        path = self.dir / f"{_key_for(url)}.json"
        try:
            path.write_text(
                json.dumps({"cached_at": time.time(), "html": html}),
                encoding="utf-8",
            )
        except OSError:
            pass
