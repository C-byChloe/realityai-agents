"""Event-driven cache invalidation.

Listens for course update events from the gRPC service and invalidates
all cached responses for the updated course_id.
"""

import logging
from typing import Callable

from caching.response_cache import ResponseCache

logger = logging.getLogger(__name__)

# Singleton cache instance (shared with query agent)
_cache = ResponseCache()

# Event listeners for testing and extensibility
_listeners: list[Callable[[str, str], None]] = []


def get_cache() -> ResponseCache:
    """Get the shared response cache instance."""
    return _cache


def set_cache(cache: ResponseCache) -> None:
    """Replace the shared cache (for testing)."""
    global _cache
    _cache = cache


def on_course_update(course_id: str, event_type: str = "update") -> dict:
    """Handle a course update event by invalidating cached responses.

    Args:
        course_id: The course that was updated.
        event_type: Type of event (update, delete).

    Returns:
        Dict with invalidation result.
    """
    count = _cache.invalidate_course(course_id)
    logger.info("Cache invalidated for course %s: %d entries removed", course_id, count)

    # Notify listeners
    for listener in _listeners:
        try:
            listener(course_id, event_type)
        except Exception as e:
            logger.warning("Listener error (non-blocking): %s", e)

    return {
        "course_id": course_id,
        "event_type": event_type,
        "entries_invalidated": count,
    }


def flush_all() -> dict:
    """Flush all cached responses (admin operation)."""
    _cache.flush()
    logger.info("Cache flushed: all entries removed")
    return {"flushed": True}


def add_listener(fn: Callable[[str, str], None]) -> None:
    """Register a listener for cache invalidation events."""
    _listeners.append(fn)


def clear_listeners() -> None:
    """Remove all listeners."""
    _listeners.clear()
