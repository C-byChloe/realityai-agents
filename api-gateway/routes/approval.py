"""HiTL approval endpoint for instructors to approve/reject flagged operations."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.jwt import TokenData, get_current_user
from routes.sse import (
    get_approval_request,
    push_event,
    resolve_approval_request,
)

approval_router = APIRouter()


class ApprovalAction(BaseModel):
    action: str  # "approve" or "reject"
    reason: str = ""


class ApprovalResponse(BaseModel):
    request_id: str
    status: str
    message: str


@approval_router.post("/{request_id}", response_model=ApprovalResponse)
async def handle_approval(
    request_id: str,
    body: ApprovalAction,
    user: Annotated[TokenData, Depends(get_current_user)],
):
    """Approve or reject a flagged operation."""
    if body.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Action must be 'approve' or 'reject'")

    request = get_approval_request(request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Approval request not found")

    if request["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Request already {request['status']}")

    resolved = resolve_approval_request(request_id, body.action)

    # Notify both the student and instructor via SSE
    event_data = {
        "request_id": request_id,
        "action": body.action,
        "reason": body.reason,
        "resolved_by": user.user_id,
    }

    await push_event(resolved["user_id"], "approval_result", event_data)
    await push_event(resolved["instructor_id"], "approval_result", event_data)

    verb = "approved" if body.action == "approve" else "rejected"
    return ApprovalResponse(
        request_id=request_id,
        status=body.action,
        message=f"Operation {verb} by {user.user_id}",
    )
