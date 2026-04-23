"""Tests for health check endpoint."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from routes.health import health_router, _check_tcp

from fastapi import FastAPI

app = FastAPI()
app.include_router(health_router)
client = TestClient(app)


class TestCheckTcp:
    def test_unreachable_returns_false(self):
        assert _check_tcp("localhost", 19999, timeout=0.5) is False


class TestHealthEndpoint:
    def test_returns_health_response(self):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "services" in data
        assert len(data["services"]) == 3

    def test_all_services_listed(self):
        response = client.get("/health")
        names = {s["name"] for s in response.json()["services"]}
        assert "core-service-grpc" in names
        assert "postgresql" in names
        assert "redis" in names

    @patch("routes.health._check_tcp", return_value=True)
    def test_all_healthy(self, mock_tcp):
        response = client.get("/health")
        data = response.json()
        assert data["status"] == "healthy"

    @patch("routes.health._check_tcp", return_value=False)
    def test_degraded_when_services_down(self, mock_tcp):
        response = client.get("/health")
        data = response.json()
        assert data["status"] == "degraded"
