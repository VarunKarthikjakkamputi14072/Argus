"""
Web scraping service.

Fetches a URL and pulls out the article title and body text. The main
extractor is trafilatura, which is much better at finding the actual article
content (and dropping nav/ads/boilerplate) than a hand-rolled tag walk. If
trafilatura comes back empty I fall back to a BeautifulSoup pass so we still
get something usable.

It also checks robots.txt before fetching and caches the result per domain so
we're not hammering a site or ignoring its rules.
"""

import httpx
import trafilatura
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from typing import Optional

from backend.config import get_settings

settings = get_settings()

USER_AGENT = (
    "Mozilla/5.0 (compatible; ArticlePipelineBot/1.0; "
    "+https://github.com/article-pipeline)"
)


class RobotsDisallowedError(Exception):
    """The site's robots.txt tells us not to fetch this URL."""


class ScraperService:
    def __init__(self):
        self.timeout = settings.scrape_timeout
        self.headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }
        # domain -> RobotFileParser (or None if we couldn't read robots.txt)
        self._robots_cache: dict[str, Optional[RobotFileParser]] = {}

    # --- robots.txt -------------------------------------------------------

    def _robots_allowed(self, url: str) -> bool:
        """Return True if robots.txt allows us to fetch this URL.

        If robots.txt can't be read for any reason we allow the fetch rather
        than block the whole pipeline on a flaky host.
        """
        parsed = urlparse(url)
        domain = f"{parsed.scheme}://{parsed.netloc}"

        if domain not in self._robots_cache:
            self._robots_cache[domain] = self._load_robots(domain)

        rp = self._robots_cache[domain]
        if rp is None:
            return True
        return rp.can_fetch(USER_AGENT, url)

    def _load_robots(self, domain: str) -> Optional[RobotFileParser]:
        try:
            resp = httpx.get(
                f"{domain}/robots.txt",
                timeout=min(self.timeout, 10),
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        rp = RobotFileParser()
        rp.parse(resp.text.splitlines())
        return rp

    # --- fetching ---------------------------------------------------------

    async def fetch_article(self, url: str) -> dict:
        if not self._robots_allowed(url):
            raise RobotsDisallowedError(f"robots.txt disallows fetching {url}")
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers=self.headers,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

        return self._extract(response.text, url)

    def fetch_article_sync(self, url: str) -> dict:
        if not self._robots_allowed(url):
            raise RobotsDisallowedError(f"robots.txt disallows fetching {url}")
        with httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers=self.headers,
        ) as client:
            response = client.get(url)
            response.raise_for_status()

        return self._extract(response.text, url)

    # --- extraction -------------------------------------------------------

    def _extract(self, html: str, url: str) -> dict:
        """Try trafilatura first; fall back to BeautifulSoup if it finds nothing."""
        content = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )

        if content and content.strip():
            title = self._trafilatura_title(html) or self._soup_title(html)
        else:
            # Trafilatura couldn't make sense of the page — use the old path.
            parsed = self._parse_html(html, url)
            return parsed

        domain = urlparse(url).netloc
        return {
            "url": url,
            "title": title,
            "content": content.strip(),
            "source_domain": domain,
            "word_count": len(content.split()),
        }

    def _trafilatura_title(self, html: str) -> Optional[str]:
        try:
            meta = trafilatura.extract_metadata(html)
        except Exception:
            return None
        if meta and getattr(meta, "title", None):
            return meta.title.strip()
        return None

    def _soup_title(self, html: str) -> Optional[str]:
        return self._extract_title(BeautifulSoup(html, "lxml"))

    def _parse_html(self, html: str, url: str) -> dict:
        soup = BeautifulSoup(html, "lxml")

        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        title = self._extract_title(soup)
        content = self._extract_content(soup)
        domain = urlparse(url).netloc

        return {
            "url": url,
            "title": title,
            "content": content,
            "source_domain": domain,
            "word_count": len(content.split()) if content else 0,
        }

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            return og_title["content"].strip()

        h1 = soup.find("h1")
        if h1:
            return h1.get_text(strip=True)

        title_tag = soup.find("title")
        if title_tag:
            return title_tag.get_text(strip=True)

        return None

    def _extract_content(self, soup: BeautifulSoup) -> str:
        article = soup.find("article")
        if article:
            paragraphs = article.find_all("p")
        else:
            main = soup.find("main") or soup.find("div", {"role": "main"})
            container = main if main else soup.body
            paragraphs = container.find_all("p") if container else []

        texts = [p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)]
        return "\n\n".join(texts)


scraper_service = ScraperService()
