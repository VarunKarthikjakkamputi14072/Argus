from celery import Celery
from backend.config import get_settings

settings = get_settings()

celery = Celery(
    "article_pipeline",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "backend.tasks.scraping.*": {"queue": "scraping"},
        "backend.tasks.processing.*": {"queue": "processing"},
        "backend.tasks.dlq.*": {"queue": "dead_letter"},
    },
    task_default_queue="scraping",
    task_default_exchange="pipeline",
    task_default_routing_key="pipeline.default",
    beat_schedule={
        "cleanup-stale-tasks": {
            "task": "backend.tasks.scraping.cleanup_stale_tasks",
            "schedule": 300.0,
        },
    },
)

celery.autodiscover_tasks(["backend.tasks.scraping", "backend.tasks.processing", "backend.tasks.dlq"])
