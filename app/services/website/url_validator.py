from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.core.exceptions import WebsiteUnreachableError


def normalize_url(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        raise WebsiteUnreachableError("URL is empty.")
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    if not parsed.netloc or "." not in parsed.netloc:
        raise WebsiteUnreachableError(f"'{raw}' is not a valid website URL.")
    return raw


def extract_domain(url: str) -> str:
    netloc = urlparse(normalize_url(url)).netloc.lower()
    return netloc.removeprefix("www.")


def verify_reachable(url: str) -> str:
    """Small HTTP call to confirm the site is reachable. Returns the final URL after redirects."""
    headers = {"User-Agent": settings.CRAWLER_USER_AGENT}
    try:
        with httpx.Client(timeout=10, follow_redirects=True, headers=headers) as client:
            response = client.head(url)
            if response.status_code in (405, 501, 403):
                # Some servers reject HEAD; retry with a tiny GET
                response = client.get(url, headers={**headers, "Range": "bytes=0-2048"})
            if response.status_code >= 400:
                raise WebsiteUnreachableError(
                    f"Website responded with HTTP {response.status_code}. Please check the URL."
                )
            return str(response.url)
    except httpx.HTTPError as exc:
        raise WebsiteUnreachableError(f"Could not reach website: {exc.__class__.__name__}") from exc
