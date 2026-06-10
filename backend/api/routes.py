"""
API routes for the Article Processing Platform.

Provides endpoints for submitting URLs, querying articles,
viewing LLM insights, and monitoring task queue status.
"""

import uuid
from datetime import datetime
from typing import Optional

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, HttpUrl, Field
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from backend.config import get_settings
from backend.db.session import get_db
from backend.models.database import Article, ArticleMetadata, TaskStatus
from backend.services.cache import url_cache
from backend.services.events import EVENTS_CHANNEL
from backend.celery_app import celery

router = APIRouter()


class SeedTopicRequest(BaseModel):
    topic: str
    limit: int = Field(default=10, le=20, description="Max articles to seed per call")


class SubmitURLRequest(BaseModel):
    url: HttpUrl


class SubmitBatchRequest(BaseModel):
    urls: list[HttpUrl]


class ArticleResponse(BaseModel):
    id: str
    url: str
    title: Optional[str]
    source_domain: Optional[str]
    word_count: Optional[float]
    status: str
    scraped_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class MetadataResponse(BaseModel):
    summary: Optional[str]
    entities: Optional[dict]
    sentiment_score: Optional[float]
    sentiment_label: Optional[str]
    llm_model_used: Optional[str]
    processed_at: Optional[datetime]


class ArticleDetailResponse(ArticleResponse):
    metadata: Optional[MetadataResponse]


@router.post("/articles/submit", status_code=202)
async def submit_url(request: SubmitURLRequest, db: AsyncSession = Depends(get_db)):
    """Submit a single URL for scraping and LLM processing."""
    url_str = str(request.url)

    existing = await db.execute(select(Article).where(Article.url == url_str))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="URL already submitted")

    article = Article(
        id=uuid.uuid4(),
        url=url_str,
        status=TaskStatus.PENDING,
    )
    db.add(article)
    await db.flush()

    from backend.tasks.scraping import scrape_article
    task = scrape_article.delay(str(article.id), url_str)

    article.celery_task_id = task.id
    article.status = TaskStatus.STARTED

    return {
        "article_id": str(article.id),
        "task_id": task.id,
        "status": "submitted",
    }


@router.post("/articles/submit-batch", status_code=202)
async def submit_batch(request: SubmitBatchRequest, db: AsyncSession = Depends(get_db)):
    """Submit multiple URLs for batch processing."""
    results = []
    for url in request.urls:
        url_str = str(url)
        existing = await db.execute(select(Article).where(Article.url == url_str))
        if existing.scalar_one_or_none():
            results.append({"url": url_str, "status": "duplicate", "article_id": None})
            continue

        article = Article(id=uuid.uuid4(), url=url_str, status=TaskStatus.PENDING)
        db.add(article)
        await db.flush()

        from backend.tasks.scraping import scrape_article
        task = scrape_article.delay(str(article.id), url_str)
        article.celery_task_id = task.id
        article.status = TaskStatus.STARTED

        results.append({
            "url": url_str,
            "status": "submitted",
            "article_id": str(article.id),
            "task_id": task.id,
        })

    return {"submitted": len([r for r in results if r["status"] == "submitted"]), "results": results}


@router.post("/articles/seed-topic", status_code=202)
async def seed_topic(request: SeedTopicRequest, db: AsyncSession = Depends(get_db)):
    """Pull news articles for a topic from APIForge and queue them for processing.

    APIForge acts as a caching, rate-limited proxy in front of NewsAPI — repeated
    calls for the same topic within the cache window hit Redis, not the upstream.
    The returned article URLs are fed directly into the existing batch scrape flow.

    Requires APIFORGE_BASE_URL and APIFORGE_API_KEY to be set in the environment.
    Returns 503 if APIForge is unreachable, 400 if the integration is not configured.
    """
    settings = get_settings()

    if not settings.apiforge_base_url or not settings.apiforge_api_key:
        raise HTTPException(
            status_code=400,
            detail=(
                "APIForge integration not configured. "
                "Set APIFORGE_BASE_URL and APIFORGE_API_KEY in your environment."
            ),
        )

    limit = request.limit

    # Call APIForge — it handles caching, rate limiting, and circuit breaking
    # for the NewsAPI upstream so Argus doesn't have to.
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.apiforge_base_url.rstrip('/')}/api/news",
                params={"topic": request.topic, "limit": limit},
                headers={"X-API-Key": settings.apiforge_api_key},
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"APIForge returned {exc.response.status_code} for topic '{request.topic}'",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not reach APIForge at {settings.apiforge_base_url}: {exc}",
        ) from exc

    news_data = resp.json()
    articles_list = news_data.get("articles", [])

    if not articles_list:
        return {"submitted": 0, "results": [], "topic": request.topic, "source": "apiforge"}

    # Extract URLs and feed into the existing batch submit flow.
    results = []
    for item in articles_list:
        url_str = item.get("url")
        if not url_str:
            continue

        existing = await db.execute(select(Article).where(Article.url == url_str))
        if existing.scalar_one_or_none():
            results.append({"url": url_str, "status": "duplicate", "article_id": None})
            continue

        article = Article(id=uuid.uuid4(), url=url_str, status=TaskStatus.PENDING)
        db.add(article)
        await db.flush()

        from backend.tasks.scraping import scrape_article
        task = scrape_article.delay(str(article.id), url_str)
        article.celery_task_id = task.id
        article.status = TaskStatus.STARTED

        results.append({
            "url": url_str,
            "status": "submitted",
            "article_id": str(article.id),
            "task_id": task.id,
        })

    submitted_count = len([r for r in results if r["status"] == "submitted"])
    return {
        "submitted": submitted_count,
        "results": results,
        "topic": request.topic,
        "source": "apiforge",
    }


@router.get("/articles", response_model=list[ArticleResponse])
async def list_articles(
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List articles with optional filtering by status or search term."""
    query = select(Article).order_by(Article.created_at.desc())

    if status:
        try:
            task_status = TaskStatus(status)
            query = query.where(Article.status == task_status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            or_(
                Article.title.ilike(search_pattern),
                Article.url.ilike(search_pattern),
                Article.source_domain.ilike(search_pattern),
            )
        )

    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    articles = result.scalars().all()

    return [
        ArticleResponse(
            id=str(a.id),
            url=a.url,
            title=a.title,
            source_domain=a.source_domain,
            word_count=a.word_count,
            status=a.status.value,
            scraped_at=a.scraped_at,
            created_at=a.created_at,
        )
        for a in articles
    ]


@router.get("/articles/{article_id}")
async def get_article(article_id: str, db: AsyncSession = Depends(get_db)):
    """Get detailed article information including LLM metadata."""
    try:
        uid = uuid.UUID(article_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid article ID")

    result = await db.execute(
        select(Article).options(joinedload(Article.metadata_entry)).where(Article.id == uid)
    )
    article = result.scalar_one_or_none()

    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    metadata = None
    if article.metadata_entry:
        m = article.metadata_entry
        metadata = {
            "summary": m.summary,
            "entities": m.entities,
            "sentiment_score": m.sentiment_score,
            "sentiment_label": m.sentiment_label,
            "llm_model_used": m.llm_model_used,
            "processed_at": m.processed_at.isoformat() if m.processed_at else None,
        }

    return {
        "id": str(article.id),
        "url": article.url,
        "title": article.title,
        "source_domain": article.source_domain,
        "word_count": article.word_count,
        "status": article.status.value,
        "error_message": article.error_message,
        "scraped_at": article.scraped_at.isoformat() if article.scraped_at else None,
        "created_at": article.created_at.isoformat(),
        "metadata": metadata,
    }


@router.get("/queue/status")
async def queue_status():
    """Get current Celery queue statistics and active task info."""
    inspector = celery.control.inspect()

    active = inspector.active() or {}
    reserved = inspector.reserved() or {}
    stats = inspector.stats() or {}

    total_active = sum(len(tasks) for tasks in active.values())
    total_reserved = sum(len(tasks) for tasks in reserved.values())

    workers = []
    for worker_name, worker_stats in stats.items():
        worker_active = active.get(worker_name, [])
        worker_reserved = reserved.get(worker_name, [])
        workers.append({
            "name": worker_name,
            "status": "online",
            "active_tasks": len(worker_active),
            "reserved_tasks": len(worker_reserved),
            "total_completed": worker_stats.get("total", {}).get(
                "backend.tasks.scraping.scrape_article", 0
            ) + worker_stats.get("total", {}).get(
                "backend.tasks.processing.process_article", 0
            ),
            "pool_size": worker_stats.get("pool", {}).get("max-concurrency", 0),
            "tasks": [
                {
                    "id": t["id"],
                    "name": t["name"],
                    "started": t.get("time_start"),
                }
                for t in worker_active
            ],
        })

    return {
        "total_active": total_active,
        "total_reserved": total_reserved,
        "total_workers": len(stats),
        "workers": workers,
    }


@router.get("/queue/tasks")
async def queue_tasks(db: AsyncSession = Depends(get_db)):
    """Get articles currently being processed (in-flight tasks)."""
    result = await db.execute(
        select(Article)
        .where(Article.status.in_([TaskStatus.STARTED, TaskStatus.SCRAPING, TaskStatus.PROCESSING]))
        .order_by(Article.created_at.desc())
    )
    articles = result.scalars().all()

    return [
        {
            "article_id": str(a.id),
            "url": a.url,
            "status": a.status.value,
            "task_id": a.celery_task_id,
            "created_at": a.created_at.isoformat(),
        }
        for a in articles
    ]


@router.get("/stats")
async def platform_stats(db: AsyncSession = Depends(get_db)):
    """Get overall platform statistics."""
    total = await db.execute(select(func.count(Article.id)))
    completed = await db.execute(
        select(func.count(Article.id)).where(Article.status == TaskStatus.COMPLETED)
    )
    failed = await db.execute(
        select(func.count(Article.id)).where(Article.status == TaskStatus.FAILED)
    )
    in_progress = await db.execute(
        select(func.count(Article.id)).where(
            Article.status.in_([TaskStatus.STARTED, TaskStatus.SCRAPING, TaskStatus.PROCESSING])
        )
    )
    avg_sentiment = await db.execute(select(func.avg(ArticleMetadata.sentiment_score)))

    cache_stats = url_cache.get_stats()

    return {
        "total_articles": total.scalar() or 0,
        "completed": completed.scalar() or 0,
        "failed": failed.scalar() or 0,
        "in_progress": in_progress.scalar() or 0,
        "avg_sentiment": round(avg_sentiment.scalar() or 0, 3),
        "cache": cache_stats,
    }


@router.get("/insights")
async def get_insights(
    limit: int = Query(default=20, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Get LLM-processed insights for completed articles."""
    result = await db.execute(
        select(Article)
        .options(joinedload(Article.metadata_entry))
        .where(Article.status == TaskStatus.COMPLETED)
        .order_by(Article.created_at.desc())
        .limit(limit)
    )
    articles = result.scalars().unique().all()

    insights = []
    for a in articles:
        if a.metadata_entry:
            m = a.metadata_entry
            insights.append({
                "article_id": str(a.id),
                "url": a.url,
                "title": a.title,
                "source_domain": a.source_domain,
                "summary": m.summary,
                "entities": m.entities,
                "sentiment_score": m.sentiment_score,
                "sentiment_label": m.sentiment_label,
                "processed_at": m.processed_at.isoformat() if m.processed_at else None,
            })

    return insights


@router.get("/events")
async def events(request: Request):
    """Server-Sent Events stream of live article status changes.

    Subscribes to the Redis channel that the Celery workers publish to, so the
    dashboard can update without polling. Sends a keep-alive comment every
    15 seconds so idle connections (and proxies) don't drop the stream.
    """
    settings = get_settings()

    async def event_stream():
        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        pubsub = client.pubsub()
        await pubsub.subscribe(EVENTS_CHANNEL)
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=15.0
                )
                if message is None:
                    yield ": keep-alive\n\n"
                    continue
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                yield f"data: {data}\n\n"
        finally:
            await pubsub.unsubscribe(EVENTS_CHANNEL)
            await pubsub.aclose()
            await client.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
