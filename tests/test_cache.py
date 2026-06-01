"""Tests for the Redis-backed URL cache and its LRU eviction."""

import pytest

from backend.services import cache as cache_module
from backend.services.cache import SEEN_URLS_KEY, URLCache


@pytest.fixture
def cache(fake_redis):
    c = URLCache()
    c._client = fake_redis
    return c


def test_mark_and_has_seen(cache):
    assert cache.has_seen("https://example.com/a") is False
    cache.mark_seen("https://example.com/a")
    assert cache.has_seen("https://example.com/a") is True


def test_has_seen_bumps_hit_count(cache):
    cache.mark_seen("https://example.com/a")
    cache.has_seen("https://example.com/a")
    hits = cache._client.hget("pipeline:url_meta:https://example.com/a", "hits")
    assert int(hits) >= 2


def test_evict_lru_removes_oldest(cache, monkeypatch):
    monkeypatch.setattr(cache_module, "EVICTION_BATCH", 2)
    for i in range(5):
        cache._client.zadd(SEEN_URLS_KEY, {f"u{i}": i})

    cache._evict_lru()

    remaining = cache._client.zrange(SEEN_URLS_KEY, 0, -1)
    assert "u0" not in remaining
    assert "u1" not in remaining
    assert "u4" in remaining
    assert len(remaining) == 3


def test_mark_seen_triggers_eviction_at_capacity(cache, monkeypatch):
    monkeypatch.setattr(cache_module, "MAX_URLS", 3)
    monkeypatch.setattr(cache_module, "EVICTION_BATCH", 1)
    for i in range(3):
        cache._client.zadd(SEEN_URLS_KEY, {f"old{i}": i})

    cache.mark_seen("https://example.com/new")

    remaining = cache._client.zrange(SEEN_URLS_KEY, 0, -1)
    assert "old0" not in remaining  # oldest evicted
    assert "https://example.com/new" in remaining


def test_get_stats_shape(cache):
    cache.mark_seen("https://example.com/a")
    stats = cache.get_stats()
    assert stats["cached_urls"] == 1
    assert "utilization_pct" in stats
    assert "eviction_policy" in stats
