"""
Redis-backed URL cache with application-level LRU eviction policy.

Strategy:
- URLs are stored in a Redis sorted set keyed by access timestamp.
- Each URL also has a hash entry storing metadata (first seen, hit count).
- When the set size exceeds MAX_URLS, the oldest-accessed entries are evicted.
- Redis server-level maxmemory-policy is set to allkeys-lru as a safety net,
  but application-level eviction provides finer control over which keys survive.
"""

import time
import redis
from backend.config import get_settings

settings = get_settings()

SEEN_URLS_KEY = "pipeline:seen_urls"
URL_META_PREFIX = "pipeline:url_meta:"
MAX_URLS = settings.redis_max_urls
EVICTION_BATCH = max(1, MAX_URLS // 10)


class URLCache:
    def __init__(self):
        self._client = redis.Redis.from_url(
            settings.redis_url, decode_responses=True
        )

    @property
    def client(self) -> redis.Redis:
        return self._client

    def has_seen(self, url: str) -> bool:
        score = self._client.zscore(SEEN_URLS_KEY, url)
        if score is not None:
            self._client.zadd(SEEN_URLS_KEY, {url: time.time()})
            self._client.hincrby(f"{URL_META_PREFIX}{url}", "hits", 1)
            return True
        return False

    def mark_seen(self, url: str) -> None:
        current_size = self._client.zcard(SEEN_URLS_KEY)
        if current_size >= MAX_URLS:
            self._evict_lru()

        now = time.time()
        self._client.zadd(SEEN_URLS_KEY, {url: now})
        self._client.hset(
            f"{URL_META_PREFIX}{url}",
            mapping={"first_seen": str(now), "hits": "1"},
        )
        self._client.expire(f"{URL_META_PREFIX}{url}", settings.redis_cache_ttl)

    def remove(self, url: str) -> None:
        self._client.zrem(SEEN_URLS_KEY, url)
        self._client.delete(f"{URL_META_PREFIX}{url}")

    def get_stats(self) -> dict:
        size = self._client.zcard(SEEN_URLS_KEY)
        memory_info = self._client.info("memory")
        return {
            "cached_urls": size,
            "max_urls": MAX_URLS,
            "utilization_pct": round((size / MAX_URLS) * 100, 2) if MAX_URLS else 0,
            "used_memory_human": memory_info.get("used_memory_human", "N/A"),
            "eviction_policy": "application-lru + server allkeys-lru fallback",
        }

    def _evict_lru(self) -> None:
        """
        Evict the least-recently-used URLs from the sorted set.
        Removes EVICTION_BATCH entries with the lowest scores (oldest access time).
        """
        victims = self._client.zrange(SEEN_URLS_KEY, 0, EVICTION_BATCH - 1)
        if victims:
            pipe = self._client.pipeline()
            pipe.zrem(SEEN_URLS_KEY, *victims)
            for url in victims:
                pipe.delete(f"{URL_META_PREFIX}{url}")
            pipe.execute()

    def flush(self) -> None:
        urls = self._client.zrange(SEEN_URLS_KEY, 0, -1)
        pipe = self._client.pipeline()
        pipe.delete(SEEN_URLS_KEY)
        for url in urls:
            pipe.delete(f"{URL_META_PREFIX}{url}")
        pipe.execute()


url_cache = URLCache()
