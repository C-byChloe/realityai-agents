"""Tests for SSE and HiTL approval flow."""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.jwt import create_access_token
from routes.approval import approval_router
from routes.sse import (
    _approval_requests,
    _connections,
    create_approval_request,
    get_approval_request,
    get_connection,
    push_event,
    remove_connection,
    resolve_approval_request,
    sse_router,
)

app = FastAPI()
app.include_router(sse_router, prefix="/sse")
app.include_router(approval_router, prefix="/approval")
client = TestClient(app)


def _auth_header(user_id: str = "instructor1", role: str = "instructor") -> dict:
    token = create_access_token(user_id, role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clear_state():
    """Clear global state before each test."""
    _connections.clear()
    _approval_requests.clear()
    yield
    _connections.clear()
    _approval_requests.clear()


class TestConnectionManagement:
    def test_get_connection_creates_queue(self):
        queue = get_connection("user1")
        assert isinstance(queue, asyncio.Queue)
        assert "user1" in _connections

    def test_get_connection_returns_same_queue(self):
        q1 = get_connection("user1")
        q2 = get_connection("user1")
        assert q1 is q2

    def test_remove_connection(self):
        get_connection("user1")
        remove_connection("user1")
        assert "user1" not in _connections

    def test_remove_nonexistent_connection(self):
        remove_connection("nonexistent")  # Should not raise


class TestPushEvent:
    @pytest.mark.asyncio
    async def test_push_to_connected_user(self):
        queue = get_connection("user1")
        await push_event("user1", "test_event", {"key": "value"})
        event = await queue.get()
        assert event["event"] == "test_event"
        assert json.loads(event["data"]) == {"key": "value"}

    @pytest.mark.asyncio
    async def test_push_to_disconnected_user(self):
        # Should not raise
        await push_event("nonexistent", "test_event", {"key": "value"})


class TestApprovalRequests:
    @pytest.mark.asyncio
    async def test_create_approval_request(self):
        get_connection("instructor1")  # Create connection to receive SSE
        request_id = await create_approval_request(
            user_id="student1",
            instructor_id="instructor1",
            context={"tool": "grade_update", "reason": "high risk"},
        )
        assert request_id is not None
        req = get_approval_request(request_id)
        assert req["status"] == "pending"
        assert req["user_id"] == "student1"

    def test_get_nonexistent_request(self):
        assert get_approval_request("nonexistent") is None

    def test_resolve_approve(self):
        _approval_requests["req1"] = {
            "request_id": "req1",
            "user_id": "student1",
            "instructor_id": "instructor1",
            "context": {},
            "status": "pending",
        }
        result = resolve_approval_request("req1", "approve")
        assert result["status"] == "approve"

    def test_resolve_reject(self):
        _approval_requests["req1"] = {
            "request_id": "req1",
            "user_id": "student1",
            "instructor_id": "instructor1",
            "context": {},
            "status": "pending",
        }
        result = resolve_approval_request("req1", "reject")
        assert result["status"] == "reject"


class TestApprovalEndpoint:
    def test_approve_request(self):
        _approval_requests["req1"] = {
            "request_id": "req1",
            "user_id": "student1",
            "instructor_id": "instructor1",
            "context": {},
            "status": "pending",
        }
        response = client.post(
            "/approval/req1",
            json={"action": "approve", "reason": "Looks good"},
            headers=_auth_header(),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "approve"
        assert "approved" in data["message"]

    def test_reject_request(self):
        _approval_requests["req2"] = {
            "request_id": "req2",
            "user_id": "student1",
            "instructor_id": "instructor1",
            "context": {},
            "status": "pending",
        }
        response = client.post(
            "/approval/req2",
            json={"action": "reject", "reason": "Not authorized"},
            headers=_auth_header(),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "reject"

    def test_not_found(self):
        response = client.post(
            "/approval/nonexistent",
            json={"action": "approve"},
            headers=_auth_header(),
        )
        assert response.status_code == 404

    def test_already_resolved(self):
        _approval_requests["req3"] = {
            "request_id": "req3",
            "user_id": "student1",
            "instructor_id": "instructor1",
            "context": {},
            "status": "approve",
        }
        response = client.post(
            "/approval/req3",
            json={"action": "reject"},
            headers=_auth_header(),
        )
        assert response.status_code == 409

    def test_invalid_action(self):
        _approval_requests["req4"] = {
            "request_id": "req4",
            "user_id": "student1",
            "instructor_id": "instructor1",
            "context": {},
            "status": "pending",
        }
        response = client.post(
            "/approval/req4",
            json={"action": "invalid"},
            headers=_auth_header(),
        )
        assert response.status_code == 400

    def test_unauthenticated_rejected(self):
        response = client.post(
            "/approval/req1",
            json={"action": "approve"},
        )
        assert response.status_code in (401, 403)
