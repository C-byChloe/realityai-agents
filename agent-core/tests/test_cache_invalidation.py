"""Tests for event-driven cache invalidation."""

import pytest

from caching.invalidation import (
    add_listener,
    clear_listeners,
    flush_all,
    get_cache,
    on_course_update,
    set_cache,
)
from caching.response_cache import CacheEntry, ResponseCache


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Use a fresh cache for each test."""
    cache = ResponseCache()
    set_cache(cache)
    clear_listeners()
    yield cache
    clear_listeners()


class TestOnCourseUpdate:
    def test_invalidates_cached_entries(self, _fresh_cache):
        cache = _fresh_cache
        cache.set("key1", CacheEntry(response="response1", course_ids=["CS101"]))
        cache.set("key2", CacheEntry(response="response2", course_ids=["CS101"]))
        cache.set("key3", CacheEntry(response="response3", course_ids=["CS201"]))

        result = on_course_update("CS101")
        assert result["entries_invalidated"] == 2
        assert result["course_id"] == "CS101"

        # CS101 entries gone, CS201 still there
        assert cache.get("key1") is None
        assert cache.get("key2") is None
        assert cache.get("key3") is not None

    def test_no_entries_to_invalidate(self):
        result = on_course_update("NONEXISTENT")
        assert result["entries_invalidated"] == 0

    def test_notifies_listeners(self):
        events = []
        add_listener(lambda cid, et: events.append((cid, et)))
        on_course_update("CS101", "update")
        assert events == [("CS101", "update")]

    def test_listener_error_is_non_blocking(self):
        def bad_listener(cid, et):
            raise RuntimeError("broken listener")

        add_listener(bad_listener)
        # Should not raise
        result = on_course_update("CS101")
        assert result["course_id"] == "CS101"


class TestFlushAll:
    def test_flushes_all_entries(self, _fresh_cache):
        cache = _fresh_cache
        cache.set("key1", CacheEntry(response="r1", course_ids=["CS101"]))
        cache.set("key2", CacheEntry(response="r2", course_ids=["CS201"]))

        result = flush_all()
        assert result["flushed"] is True
        assert cache.get("key1") is None
        assert cache.get("key2") is None


class TestListeners:
    def test_add_and_clear(self):
        add_listener(lambda cid, et: None)
        add_listener(lambda cid, et: None)
        clear_listeners()
        # Verify no listeners fire
        events = []
        on_course_update("CS101")
        assert events == []

    def test_multiple_listeners(self):
        events1, events2 = [], []
        add_listener(lambda cid, et: events1.append(cid))
        add_listener(lambda cid, et: events2.append(cid))
        on_course_update("CS101")
        assert events1 == ["CS101"]
        assert events2 == ["CS101"]
