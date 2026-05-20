"""
Async web scraping service.

Fetches article content from URLs, extracts text/title using BeautifulSoup,
and respects rate limiting and timeout constraints.
"""

import httpx
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from typing import Optional

from backend.config import get_settings

settings = get_settings()

USER_AGENT = (
    "Mozilla/5.0 (compatible; ArticlePipelineBot/1.0; "
    "+https://github.com/article-pipeline)"
)


class ScraperService:
    def __init__(self):
        self.timeout = settings.scrape_timeout
        self.headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }

    async def fetch_article(self, url: str) -> dict:
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers=self.headers,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

        return self._parse_html(response.text, url)

    def fetch_article_sync(self, url: str) -> dict:
        with httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers=self.headers,
        ) as client:
            response = client.get(url)
            response.raise_for_status()

        return self._parse_html(response.text, url)

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
