"""Tiny pub/sub helper for pushing live status updates to the dashboard.

The Celery workers and the API run in different processes, so the API can't
just watch task state in memory. Instead each task publishes a small JSON event
to a Redis channel when an article changes status, and the /api/events SSE
endpoint subscribes to that channel and forwards events to the browser.
"""

import json
import logging

import redis

from backend.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

EVENTS_CHANNEL = "argus:events"

# Reuse one sync client for the workers; publishing is fire-and-forget.
_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)


def publish_event(event_type: str, **fields) -> None:
    """Publish an event to the dashboard channel.

    Never raises — a dropped status update shouldn't fail the actual task.
    """
    payload = {"type": event_type, **fields}
    try:
        _client.publish(EVENTS_CHANNEL, json.dumps(payload, default=str))
    except redis.RedisError as exc:
        logger.warning("Failed to publish event %s: %s", event_type, exc)
