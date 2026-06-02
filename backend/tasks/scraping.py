"""
Celery tasks for article scraping.

Handles URL fetching, deduplication via the Redis cache,
and chaining to the LLM processing task on success.
"""

from datetime import datetime

from celery import shared_task
from celery.utils.log import get_task_logger
from sqlalchemy import update

from backend.db.session import SyncSessionLocal
from backend.models.database import Article, TaskStatus
from backend.services.cache import url_cache
from backend.services.events import publish_event
from backend.services.scraper import scraper_service

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    name="backend.tasks.scraping.scrape_article",
    queue="scraping",
    max_retries=3,
    default_retry_delay=10,
    acks_late=True,
)
def scrape_article(self, article_id: str, url: str):
    """
    Scrape a single article URL and persist the raw content.
    On success, chains to the LLM processing task.
    """
    logger.info(f"Scraping article {article_id}: {url}")

    session = SyncSessionLocal()
    try:
        session.execute(
            update(Article)
            .where(Article.id == article_id)
            .values(status=TaskStatus.SCRAPING, celery_task_id=self.request.id)
        )
        session.commit()
        publish_event("status", article_id=article_id, status="scraping", url=url)

        if url_cache.has_seen(url):
            logger.info(f"URL already processed (cache hit): {url}")
            existing = session.query(Article).filter(Article.url == url, Article.id != article_id).first()
            if existing and existing.raw_content:
                session.execute(
                    update(Article)
                    .where(Article.id == article_id)
                    .values(
                        title=existing.title,
                        raw_content=existing.raw_content,
                        source_domain=existing.source_domain,
                        word_count=existing.word_count,
                        status=TaskStatus.PROCESSING,
                        scraped_at=datetime.utcnow(),
                    )
                )
                session.commit()
                publish_event(
                    "status", article_id=article_id, status="processing",
                    title=existing.title, source_domain=existing.source_domain,
                )
                from backend.tasks.processing import process_article
                process_article.apply_async(args=[article_id], queue="processing")
                return {"status": "cache_hit", "article_id": article_id}

        result = scraper_service.fetch_article_sync(url)

        session.execute(
            update(Article)
            .where(Article.id == article_id)
            .values(
                title=result["title"],
                raw_content=result["content"],
                source_domain=result["source_domain"],
                word_count=result["word_count"],
                status=TaskStatus.PROCESSING,
                scraped_at=datetime.utcnow(),
            )
        )
        session.commit()
        publish_event(
            "status", article_id=article_id, status="processing",
            title=result["title"], source_domain=result["source_domain"],
            word_count=result["word_count"],
        )

        url_cache.mark_seen(url)

        from backend.tasks.processing import process_article
        process_article.apply_async(args=[article_id], queue="processing")

        logger.info(f"Scraping complete for {article_id}, dispatching to LLM processing")
        return {"status": "scraped", "article_id": article_id, "word_count": result["word_count"]}

    except Exception as exc:
        session.rollback()
        logger.error(f"Scraping failed for {article_id}: {exc}")
        if self.request.retries >= self.max_retries:
            from backend.tasks.dlq import handle_dead_letter
            handle_dead_letter.apply_async(args=[article_id, str(exc), "scrape_article"], queue="dead_letter")
            return
        session.execute(
            update(Article)
            .where(Article.id == article_id)
            .values(status=TaskStatus.FAILED, error_message=str(exc))
        )
        session.commit()
        raise self.retry(exc=exc)
    finally:
        session.close()


@shared_task(
    name="backend.tasks.scraping.cleanup_stale_tasks",
    queue="scraping",
)
def cleanup_stale_tasks():
    """Periodic task to mark stale STARTED/SCRAPING tasks as FAILED."""
    from datetime import timedelta

    session = SyncSessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(minutes=30)
        stale = (
            session.query(Article)
            .filter(
                Article.status.in_([TaskStatus.STARTED, TaskStatus.SCRAPING]),
                Article.updated_at < cutoff,
            )
            .all()
        )
        for article in stale:
            article.status = TaskStatus.FAILED
            article.error_message = "Task timed out (stale)"
        session.commit()
        logger.info(f"Cleaned up {len(stale)} stale tasks")
    finally:
        session.close()
