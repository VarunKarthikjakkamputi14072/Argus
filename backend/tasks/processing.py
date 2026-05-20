"""
Celery tasks for LLM processing of scraped articles.

Takes raw article content, sends it to the LLM for analysis,
and persists the structured metadata (summary, entities, sentiment).
"""

from datetime import datetime

from celery import shared_task
from celery.utils.log import get_task_logger
from sqlalchemy import update

from backend.db.session import SyncSessionLocal
from backend.models.database import Article, ArticleMetadata, TaskStatus
from backend.services.llm_processor import llm_processor

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    name="backend.tasks.processing.process_article",
    queue="processing",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def process_article(self, article_id: str):
    """
    Process a scraped article through the LLM and store structured metadata.
    """
    logger.info(f"Processing article {article_id} with LLM")

    session = SyncSessionLocal()
    try:
        article = session.query(Article).filter(Article.id == article_id).first()
        if not article:
            logger.error(f"Article {article_id} not found")
            return {"status": "not_found", "article_id": article_id}

        if not article.raw_content:
            session.execute(
                update(Article)
                .where(Article.id == article_id)
                .values(status=TaskStatus.FAILED, error_message="No content to process")
            )
            session.commit()
            return {"status": "no_content", "article_id": article_id}

        result = llm_processor.safe_analyze(
            text=article.raw_content, title=article.title
        )

        metadata = ArticleMetadata(
            article_id=article.id,
            summary=result["summary"],
            entities=result["entities"],
            sentiment_score=result["sentiment_score"],
            sentiment_label=result["sentiment_label"],
            llm_model_used=result["model_used"],
            token_usage=result["token_usage"],
            processed_at=datetime.utcnow(),
        )
        session.add(metadata)

        session.execute(
            update(Article)
            .where(Article.id == article_id)
            .values(status=TaskStatus.COMPLETED)
        )
        session.commit()

        logger.info(
            f"Processing complete for {article_id}: "
            f"sentiment={result['sentiment_score']:.2f} ({result['sentiment_label']})"
        )
        return {
            "status": "processed",
            "article_id": article_id,
            "sentiment": result["sentiment_score"],
        }

    except Exception as exc:
        session.rollback()
        logger.error(f"LLM processing failed for {article_id}: {exc}")
        if self.request.retries >= self.max_retries:
            from backend.tasks.dlq import handle_dead_letter
            handle_dead_letter.apply_async(args=[article_id, str(exc), "process_article"], queue="dead_letter")
            return
        session.execute(
            update(Article)
            .where(Article.id == article_id)
            .values(status=TaskStatus.FAILED, error_message=f"LLM error: {exc}")
        )
        session.commit()
        raise self.retry(exc=exc)
    finally:
        session.close()
