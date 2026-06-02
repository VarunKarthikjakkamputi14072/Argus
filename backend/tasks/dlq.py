from backend.celery_app import celery
from backend.db.session import SyncSessionLocal
from backend.models.database import Article, TaskStatus
from backend.services.events import publish_event
import logging

logger = logging.getLogger(__name__)


@celery.task(name="backend.tasks.dlq.handle_dead_letter", queue="dead_letter", acks_late=True)
def handle_dead_letter(article_id: str, error: str, original_task: str):
    """Receives permanently failed tasks after all retries exhausted."""
    logger.error(
        "Dead letter received",
        extra={"article_id": article_id, "error": error, "task": original_task},
    )
    session = SyncSessionLocal()
    try:
        article = session.query(Article).filter(Article.id == article_id).first()
        if article:
            article.status = TaskStatus.FAILED
            article.error_message = f"[DLQ] {error}"
            session.commit()
            publish_event(
                "status", article_id=article_id, status="failed",
                error=error, task=original_task,
            )
    except Exception as e:
        logger.error(f"Failed to process DLQ for {article_id}: {e}")
        session.rollback()
    finally:
        session.close()
