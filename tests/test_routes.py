"""API route tests. Celery and Redis are stubbed so no broker/server is needed."""

import uuid

import pytest


class _FakeAsyncResult:
    def __init__(self, task_id="fake-task-id"):
        self.id = task_id


@pytest.fixture(autouse=True)
def stub_celery(monkeypatch):
    """Don't actually enqueue anything — just hand back a task id."""
    monkeypatch.setattr(
        "backend.tasks.scraping.scrape_article.delay",
        lambda *a, **k: _FakeAsyncResult(),
    )


@pytest.fixture(autouse=True)
def stub_url_cache(fake_redis):
    """Point the cache singleton at fakeredis so /stats works."""
    from backend.services.cache import url_cache

    url_cache._client = fake_redis
    yield


async def test_submit_returns_202(client):
    resp = await client.post("/api/articles/submit", json={"url": "https://example.com/a"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "submitted"
    assert body["article_id"]
    assert body["task_id"]


async def test_submit_duplicate_returns_409(client):
    await client.post("/api/articles/submit", json={"url": "https://example.com/dup"})
    resp = await client.post("/api/articles/submit", json={"url": "https://example.com/dup"})
    assert resp.status_code == 409


async def test_submit_rejects_bad_url(client):
    resp = await client.post("/api/articles/submit", json={"url": "not-a-url"})
    assert resp.status_code == 422  # pydantic HttpUrl validation


async def test_list_articles_empty(client):
    resp = await client.get("/api/articles")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_and_filter(client):
    await client.post("/api/articles/submit", json={"url": "https://example.com/news"})

    resp = await client.get("/api/articles")
    assert len(resp.json()) == 1

    # search match
    resp = await client.get("/api/articles", params={"search": "example"})
    assert len(resp.json()) == 1

    # search miss
    resp = await client.get("/api/articles", params={"search": "nomatch"})
    assert resp.json() == []


async def test_list_invalid_status_400(client):
    resp = await client.get("/api/articles", params={"status": "bogus"})
    assert resp.status_code == 400


async def test_get_article_invalid_id_400(client):
    resp = await client.get("/api/articles/not-a-uuid")
    assert resp.status_code == 400


async def test_get_article_not_found_404(client):
    resp = await client.get(f"/api/articles/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_stats_shape(client):
    await client.post("/api/articles/submit", json={"url": "https://example.com/x"})
    resp = await client.get("/api/stats")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("total_articles", "completed", "failed", "in_progress", "avg_sentiment", "cache"):
        assert key in body
    assert body["total_articles"] == 1
