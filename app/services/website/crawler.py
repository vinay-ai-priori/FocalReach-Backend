"""Website crawler: httpx + Trafilatura first, BeautifulSoup for link/meta parsing,
Playwright only as a fallback for JavaScript-rendered sites."""

from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup

from app.core.config import settings
from app.core.exceptions import WebsiteUnreachableError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Pages most likely to describe the business
PRIORITY_PATHS = ("about", "about-us", "services", "solutions", "products", "product", "company", "what-we-do")


@dataclass
class CrawlResult:
    content: str = ""
    page_title: str | None = None
    meta_description: str | None = None
    pages: list[dict] = field(default_factory=list)
    used_playwright: bool = False
    engine: str = "httpx"


def _extract_text(html: str, url: str) -> str:
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False) or ""
    if not text:
        # BeautifulSoup fallback when trafilatura yields nothing
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "noscript"]):
            tag.decompose()
        text = " ".join(soup.get_text(" ").split())
    return text.strip()


def _parse_meta(html: str) -> tuple[str | None, str | None]:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.string.strip() if soup.title and soup.title.string else None
    desc_tag = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    description = desc_tag.get("content", "").strip() if desc_tag else None
    return title, description


def _discover_internal_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    base_domain = urlparse(base_url).netloc.removeprefix("www.")
    found: dict[str, int] = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"].split("#")[0])
        parsed = urlparse(href)
        if parsed.netloc.removeprefix("www.") != base_domain or parsed.scheme not in ("http", "https"):
            continue
        path = parsed.path.strip("/").lower()
        if not path or href in found:
            continue
        for rank, keyword in enumerate(PRIORITY_PATHS):
            if keyword in path:
                found[href] = rank
                break
    return [url for url, _ in sorted(found.items(), key=lambda kv: kv[1])]


def _fetch_with_playwright(url: str) -> str:
    from playwright.sync_api import sync_playwright

    logger.info("Playwright fallback for %s", url)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=settings.CRAWLER_USER_AGENT)
            page.goto(url, wait_until="networkidle", timeout=settings.CRAWLER_TIMEOUT_SECONDS * 1000)
            return page.content()
        finally:
            browser.close()


def crawl_website(url: str) -> CrawlResult:
    result = CrawlResult()
    headers = {"User-Agent": settings.CRAWLER_USER_AGENT}

    with httpx.Client(timeout=settings.CRAWLER_TIMEOUT_SECONDS, follow_redirects=True, headers=headers) as client:
        try:
            response = client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise WebsiteUnreachableError(f"Failed to fetch website: {exc.__class__.__name__}") from exc

        html = response.text
        text = _extract_text(html, url)

        # JS-rendered site heuristic: nearly empty extraction from a 200 response
        if len(text) < settings.MIN_CONTENT_LENGTH_FOR_PLAYWRIGHT_FALLBACK:
            try:
                html = _fetch_with_playwright(url)
                text = _extract_text(html, url)
                result.used_playwright = True
                result.engine = "playwright"
            except Exception as exc:  # Playwright unavailable or failed; keep httpx content
                logger.warning("Playwright fallback failed for %s: %s", url, exc)

        result.page_title, result.meta_description = _parse_meta(html)
        result.pages.append({"url": url, "title": result.page_title, "chars": len(text)})
        contents = [text]

        # Crawl a handful of high-signal internal pages (about/services/...)
        for link in _discover_internal_links(html, url)[: settings.CRAWLER_MAX_PAGES - 1]:
            try:
                sub = client.get(link)
                sub.raise_for_status()
                sub_text = _extract_text(sub.text, link)
                if len(sub_text) > 100:
                    sub_title, _ = _parse_meta(sub.text)
                    result.pages.append({"url": link, "title": sub_title, "chars": len(sub_text)})
                    contents.append(sub_text)
            except httpx.HTTPError:
                continue

    result.content = "\n\n---\n\n".join(contents)[:60000]
    return result
