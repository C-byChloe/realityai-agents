"""Redis-backed response cache for deterministic queries.

Cache key: hash(normalized_query + course_ids + semester)
Default TTL: 1 hour (3600 seconds)
Non-deterministic queries (tutoring, planning) bypass caching.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field


@dataclass
class CacheEntry:
    """A cached response."""

    response: str
    sources: list[str] = field(default_factory=list)
    timestamp: float = 0.0
    course_ids: list[str] = field(default_factory=list)


class ResponseCache:
    """In-memory response cache with TTL expiration.

    Uses an in-memory dict as a stand-in for Redis. The interface
    (get/set/invalidate/flush) mirrors what the Redis implementation
    would look like, making the swap straightforward.
    """

    DEFAULT_TTL = 3600  # 1 hour in seconds

    def __init__(self, ttl: int = DEFAULT_TTL):
        self._store: dict[str, tuple[CacheEntry, float]] = {}  # key → (entry, expires_at)
        self._ttl = ttl
        # Secondary index: course_id → set of cache keys
        self._course_index: dict[str, set[str]] = {}

    @staticmethod
    def make_key(query: str, course_ids: list[str] | None = None,
                 semester: str | None = None) -> str:
        """Generate a cache key from normalized query + metadata.

        Key = SHA-256(normalized_query + sorted_course_ids + semester)
        """
        normalized = query.strip().lower()
        courses = ",".join(sorted(course_ids)) if course_ids else ""
        sem = semester or ""
        raw = f"{normalized}|{courses}|{sem}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def get(self, key: str) -> CacheEntry | None:
        """Look up a cache entry. Returns None on miss or expiry."""
        if key not in self._store:
            return None

        entry, expires_at = self._store[key]
        if time.time() > expires_at:
            # Expired — evict and return miss
            self._evict(key, entry)
            return None

        return entry

    def set(self, key: str, entry: CacheEntry) -> None:
        """Store a cache entry with TTL."""
        expires_at = time.time() + self._ttl
        self._store[key] = (entry, expires_at)

        # Update course index
        for cid in entry.course_ids:
            if cid not in self._course_index:
                self._course_index[cid] = set()
            self._course_index[cid].add(key)

    def invalidate_course(self, course_id: str) -> int:
        """Invalidate all cache entries for a given course_id.

        Returns the number of entries invalidated.
        """
        keys = self._course_index.pop(course_id, set())
        count = 0
        for key in keys:
            if key in self._store:
                del self._store[key]
                count += 1
        return count

    def flush(self) -> int:
        """Flush all cache entries. Returns count of entries cleared."""
        count = len(self._store)
        self._store.clear()
        self._course_index.clear()
        return count

    def _evict(self, key: str, entry: CacheEntry) -> None:
        """Remove an expired entry and clean up indices."""
        del self._store[key]
        for cid in entry.course_ids:
            if cid in self._course_index:
                self._course_index[cid].discard(key)

    @property
    def size(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# Query type classification for caching decisions
# ---------------------------------------------------------------------------

# Non-deterministic query types bypass caching
NON_CACHEABLE_TYPES = {"tutoring", "planning"}


def is_cacheable(query_type: str) -> bool:
    """Determine if a query type is eligible for response caching.

    Deterministic queries (course info, schedules) are cacheable.
    Non-deterministic queries (tutoring, planning) are not.
    """
    return query_type not in NON_CACHEABLE_TYPES
