from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FetchResult:
    url: str
    html: str | None
    status_code: int | None
    fetched_via: str  # "httpx" | "playwright"
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.html is not None and self.error is None
