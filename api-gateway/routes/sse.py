"""SSE (Server-Sent Events) endpoint for real-time push notifications.

Event types:
- agent_action: Agent is performing an action
- approval_request: Operation flagged for instructor review
- approval_result: Instructor approved/rejected an operation
- escalation: Request escalated to instructor due to failures
"""

import asyncio
import json
import uuid
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from auth.jwt import TokenData, get_current_user

sse_router = APIRouter()

# In-memory store for SSE connections and approval requests
_connections: dict[str, asyncio.Queue] = {}
_approval_requests: dict[str, dict] = {}


def get_connection(user_id: str) -> asyncio.Queue:
    """Get or create an SSE connection queue for a user."""
    if user_id not in _connections:
        _connections[user_id] = asyncio.Queue()
    return _connections[user_id]


def remove_connection(user_id: str) -> None:
    """Remove an SSE connection when the client disconnects."""
    _connections.pop(user_id, None)


async def push_event(user_id: str, event_type: str, data: dict) -> None:
    """Push an event to a user's SSE stream."""
    queue = _connections.get(user_id)
    if queue:
        await queue.put({"event": event_type, "data": json.dumps(data)})


async def create_approval_request(
    user_id: str,
    instructor_id: str,
    context: dict,
) -> str:
    """Create an approval request and notify the instructor via SSE."""
    request_id = str(uuid.uuid4())
    _approval_requests[request_id] = {
        "request_id": request_id,
        "user_id": user_id,
        "instructor_id": instructor_id,
        "context": context,
        "status": "pending",
    }

    await push_event(instructor_id, "approval_request", {
        "request_id": request_id,
        "user_id": user_id,
        "context": context,
    })

    return request_id


def get_approval_request(request_id: str) -> dict | None:
    """Get an approval request by ID."""
    return _approval_requests.get(request_id)


def resolve_approval_request(request_id: str, action: str) -> dict | None:
    """Resolve an approval request with approve/reject."""
    request = _approval_requests.get(request_id)
    if request is None:
        return None
    request["status"] = action
    return request


@sse_router.get("/events")
async def sse_events(
    user: Annotated[TokenData, Depends(get_current_user)],
):
    """SSE endpoint for real-time event streaming."""
    queue = get_connection(user.user_id)

    async def event_generator():
        try:
            while True:
                event = await queue.get()
                yield event
        except asyncio.CancelledError:
            remove_connection(user.user_id)

    return EventSourceResponse(event_generator())
