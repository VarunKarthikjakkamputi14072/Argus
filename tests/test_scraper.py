"""Tests for the scraping service: extraction quality and robots.txt handling."""

from urllib.robotparser import RobotFileParser

import pytest

from backend.services.scraper import (
    USER_AGENT,
    RobotsDisallowedError,
    ScraperService,
)

ARTICLE_HTML = """
<html>
  <head>
    <title>Fallback Title</title>
    <meta property="og:title" content="The Real Headline" />
  </head>
  <body>
    <nav>Home About Contact</nav>
    <article>
      <h1>The Real Headline</h1>
      <p>The central bank raised interest rates by half a point on Wednesday,
         citing persistent inflation across the housing and energy sectors.</p>
      <p>Analysts had expected a quarter-point move, and markets fell sharply
         in the hour after the announcement as traders repriced their bets.</p>
      <p>The chair said further increases were likely if price growth did not
         slow in the coming months, leaving the door open to more tightening.</p>
    </article>
    <footer>Copyright 2024</footer>
  </body>
</html>
"""


def test_parse_html_pulls_title_and_body():
    scraper = ScraperService()
    result = scraper._parse_html(ARTICLE_HTML, "https://news.example.com/story")

    # og:title wins over the <title> tag.
    assert result["title"] == "The Real Headline"
    assert "interest rates" in result["content"]
    # nav/footer get stripped.
    assert "Copyright" not in result["content"]
    assert result["source_domain"] == "news.example.com"
    assert result["word_count"] > 0


def test_extract_prefers_trafilatura():
    scraper = ScraperService()
    result = scraper._extract(ARTICLE_HTML, "https://news.example.com/story")

    assert result["content"]
    assert "interest rates" in result["content"]
    assert result["word_count"] > 0
    assert result["title"]


def test_extract_falls_back_when_trafilatura_returns_nothing(monkeypatch):
    scraper = ScraperService()
    monkeypatch.setattr(
        "backend.services.scraper.trafilatura.extract", lambda *a, **k: None
    )

    result = scraper._extract(ARTICLE_HTML, "https://news.example.com/story")

    # Still get usable content via the BeautifulSoup fallback.
    assert "interest rates" in result["content"]
    assert result["title"] == "The Real Headline"


def test_robots_disallowed_blocks_fetch():
    scraper = ScraperService()
    rp = RobotFileParser()
    rp.parse(["User-agent: *", "Disallow: /"])
    # Prime the cache so no network call happens.
    scraper._robots_cache["https://blocked.example.com"] = rp

    assert scraper._robots_allowed("https://blocked.example.com/page") is False
    with pytest.raises(RobotsDisallowedError):
        scraper.fetch_article_sync("https://blocked.example.com/page")


def test_robots_allows_when_unreadable():
    scraper = ScraperService()
    # No robots.txt available -> default to allowing.
    scraper._robots_cache["https://open.example.com"] = None
    assert scraper._robots_allowed("https://open.example.com/page") is True


def test_user_agent_is_identifiable():
    assert "ArticlePipelineBot" in USER_AGENT
