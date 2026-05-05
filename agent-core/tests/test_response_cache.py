"""Unit tests for the Redis-backed response cache."""

import time
from unittest.mock import patch

import pytest

from caching.response_cache import (
    CacheEntry,
    ResponseCache,
    is_cacheable,
)


# ---------------------------------------------------------------------------
# Cache Key Tests
# ---------------------------------------------------------------------------


class TestCacheKey:
    def test_same_query_same_key(self):
        k1 = ResponseCache.make_key("What time does CS101 meet?", ["CS101"], "Fall2025")
        k2 = ResponseCache.make_key("What time does CS101 meet?", ["CS101"], "Fall2025")
        assert k1 == k2

    def test_different_query_different_key(self):
        k1 = ResponseCache.make_key("What time does CS101 meet?")
        k2 = ResponseCache.make_key("Who teaches CS101?")
        assert k1 != k2

    def test_normalized_case(self):
        k1 = ResponseCache.make_key("CS101 Schedule")
        k2 = ResponseCache.make_key("cs101 schedule")
        assert k1 == k2

    def test_whitespace_normalized(self):
        k1 = ResponseCache.make_key("  CS101  ")
        k2 = ResponseCache.make_key("CS101")
        assert k1 == k2

    def test_course_ids_sorted(self):
        k1 = ResponseCache.make_key("test", ["CS201", "CS101"])
        k2 = ResponseCache.make_key("test", ["CS101", "CS201"])
        assert k1 == k2


# ---------------------------------------------------------------------------
# Cache Hit/Miss Tests
# ---------------------------------------------------------------------------


class TestCacheHitMiss:
    def test_cache_miss_returns_none(self):
        cache = ResponseCache()
        assert cache.get("nonexistent") is None

    def test_cache_hit_returns_entry(self):
        cache = ResponseCache()
        entry = CacheEntry(response="CS101 meets MWF 10am", course_ids=["CS101"])
        key = ResponseCache.make_key("CS101 schedule", ["CS101"])

        cache.set(key, entry)
        result = cache.get(key)

        assert result is not None
        assert result.response == "CS101 meets MWF 10am"

    def test_different_key_is_miss(self):
        cache = ResponseCache()
        entry = CacheEntry(response="answer")
        cache.set("key1", entry)
        assert cache.get("key2") is None


# ---------------------------------------------------------------------------
# TTL Expiration Tests
# ---------------------------------------------------------------------------


class TestTTLExpiration:
    def test_expired_entry_returns_none(self):
        cache = ResponseCache(ttl=1)  # 1 second TTL
        entry = CacheEntry(response="old answer")
        cache.set("key", entry)

        # Fast-forward time
        with patch("caching.response_cache.time") as mock_time:
            # Set: time.time() returns current time
            mock_time.time.return_value = time.time() + 2  # 2 seconds later
            result = cache.get("key")

        assert result is None

    def test_non_expired_entry_returns(self):
        cache = ResponseCache(ttl=3600)
        entry = CacheEntry(response="fresh answer")
        cache.set("key", entry)

        result = cache.get("key")
        assert result is not None
        assert result.response == "fresh answer"

    def test_expired_entry_is_evicted(self):
        cache = ResponseCache(ttl=1)
        entry = CacheEntry(response="old", course_ids=["CS101"])
        cache.set("key", entry)

        with patch("caching.response_cache.time") as mock_time:
            mock_time.time.return_value = time.time() + 2
            cache.get("key")  # triggers eviction

        assert cache.size == 0


# ---------------------------------------------------------------------------
# Course Invalidation Tests
# ---------------------------------------------------------------------------


class TestCourseInvalidation:
    def test_invalidate_removes_entries(self):
        cache = ResponseCache()
        cache.set("k1", CacheEntry(response="a", course_ids=["CS101"]))
        cache.set("k2", CacheEntry(response="b", course_ids=["CS101"]))
        cache.set("k3", CacheEntry(response="c", course_ids=["CS201"]))

        count = cache.invalidate_course("CS101")
        assert count == 2
        assert cache.get("k1") is None
        assert cache.get("k2") is None
        assert cache.get("k3") is not None

    def test_invalidate_nonexistent_course(self):
        cache = ResponseCache()
        count = cache.invalidate_course("FAKE999")
        assert count == 0


# ---------------------------------------------------------------------------
# Flush Tests
# ---------------------------------------------------------------------------


class TestFlush:
    def test_flush_clears_all(self):
        cache = ResponseCache()
        cache.set("k1", CacheEntry(response="a"))
        cache.set("k2", CacheEntry(response="b"))

        count = cache.flush()
        assert count == 2
        assert cache.size == 0

    def test_flush_empty_cache(self):
        cache = ResponseCache()
        count = cache.flush()
        assert count == 0


# ---------------------------------------------------------------------------
# Cacheability Tests
# ---------------------------------------------------------------------------


class TestIsCacheable:
    def test_deterministic_is_cacheable(self):
        assert is_cacheable("deterministic") is True

    def test_tutoring_not_cacheable(self):
        assert is_cacheable("tutoring") is False

    def test_planning_not_cacheable(self):
        assert is_cacheable("planning") is False

    def test_unknown_type_is_cacheable(self):
        assert is_cacheable("other") is True
