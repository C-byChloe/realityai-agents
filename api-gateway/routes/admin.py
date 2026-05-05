"""Admin endpoints for cache management."""

import os
import sys
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.jwt import TokenData, get_current_user

AGENT_CORE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "agent-core")
if AGENT_CORE_PATH not in sys.path:
    sys.path.insert(0, os.path.abspath(AGENT_CORE_PATH))

admin_router = APIRouter()


class FlushResponse(BaseModel):
    flushed: bool
    message: str


class InvalidateResponse(BaseModel):
    course_id: str
    entries_invalidated: int


@admin_router.post("/cache/flush", response_model=FlushResponse)
async def flush_cache(
    user: Annotated[TokenData, Depends(get_current_user)],
):
    """Flush all cached responses (admin only)."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    from caching.invalidation import flush_all
    flush_all()
    return FlushResponse(flushed=True, message="All cache entries removed")


@admin_router.post("/cache/invalidate/{course_id}", response_model=InvalidateResponse)
async def invalidate_course(
    course_id: str,
    user: Annotated[TokenData, Depends(get_current_user)],
):
    """Invalidate cache for a specific course (admin/instructor)."""
    if user.role not in ("admin", "instructor"):
        raise HTTPException(status_code=403, detail="Admin or instructor access required")

    from caching.invalidation import on_course_update
    result = on_course_update(course_id)
    return InvalidateResponse(
        course_id=course_id,
        entries_invalidated=result["entries_invalidated"],
    )
