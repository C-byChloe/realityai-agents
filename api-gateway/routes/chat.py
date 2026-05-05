"""Chat endpoint — routes user messages to the LangGraph agent core."""

import os
import sys
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from auth.jwt import TokenData, get_current_user

# Add agent-core to path so we can import the orchestrator
AGENT_CORE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "agent-core")
if AGENT_CORE_PATH not in sys.path:
    sys.path.insert(0, os.path.abspath(AGENT_CORE_PATH))

chat_router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    intent: str
    selected_agent: str
    session_id: str


@chat_router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user: Annotated[TokenData, Depends(get_current_user)],
):
    """Process a chat message through the agent core."""
    from langchain_core.messages import HumanMessage

    from orchestrator import build_graph

    session_id = request.session_id or f"{user.user_id}-default"

    graph = build_graph()
    compiled = graph.compile()

    initial_state = {
        "messages": [HumanMessage(content=request.message)],
        "intent": "",
        "intent_confidence": 0.0,
        "selected_agent": "",
        "safety_result": None,
        "tool_calls": [],
        "response": "",
        "user_id": user.user_id,
        "session_id": session_id,
        "requires_approval": False,
        "approval_status": None,
    }

    result = await compiled.ainvoke(initial_state)

    return ChatResponse(
        response=result.get("response", ""),
        intent=result.get("intent", ""),
        selected_agent=result.get("selected_agent", ""),
        session_id=session_id,
    )
