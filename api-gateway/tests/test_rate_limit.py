"""Tests for rate limiting middleware."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.rate_limit import RateLimitMiddleware


def _create_app(rpm: int = 5) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, requests_per_minute=rpm)

    @app.get("/test")
    async def test_endpoint():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    return app


class TestRateLimit:
    def test_allows_requests_under_limit(self):
        client = TestClient(_create_app(rpm=5))
        for _ in range(5):
            response = client.get("/test")
            assert response.status_code == 200

    def test_blocks_requests_over_limit(self):
        client = TestClient(_create_app(rpm=3))
        for _ in range(3):
            client.get("/test")
        response = client.get("/test")
        assert response.status_code == 429
        assert "Rate limit" in response.json()["detail"]

    def test_health_endpoint_bypasses_rate_limit(self):
        client = TestClient(_create_app(rpm=1))
        client.get("/test")  # Uses the limit
        # Health should still work
        response = client.get("/health")
        assert response.status_code == 200
