"""Per-user rate limiting middleware.

Uses an in-memory sliding window counter. In production, replace
with Redis-backed rate limiting for multi-instance deployments.
"""

import time
from collections import defaultdict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding window rate limiter keyed by user (from JWT sub) or IP."""

    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.rpm = requests_per_minute
        self.window = 60  # seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _get_key(self, request: Request) -> str:
        """Extract rate limit key from JWT token or fall back to client IP."""
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            # Use a hash of the token as key (avoids decoding overhead)
            return f"token:{hash(auth)}"
        return f"ip:{request.client.host if request.client else 'unknown'}"

    def _is_rate_limited(self, key: str) -> bool:
        """Check if the key has exceeded the rate limit."""
        now = time.time()
        cutoff = now - self.window

        # Prune old entries
        self._requests[key] = [t for t in self._requests[key] if t > cutoff]

        if len(self._requests[key]) >= self.rpm:
            return True

        self._requests[key].append(now)
        return False

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip rate limiting for health checks
        if request.url.path in ("/health", "/"):
            return await call_next(request)

        key = self._get_key(request)
        if self._is_rate_limited(key):
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
            )

        response = await call_next(request)
        return response
