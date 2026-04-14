"""LangGraph state machine orchestrator for multi-agent routing."""

import os

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from agents.action_agent import run_action_agent
from agents.query_agent import run_query_agent
from state import AgentState, SafetyResult

# ---------------------------------------------------------------------------
# LLM setup
# ---------------------------------------------------------------------------

def _get_llm() -> ChatAnthropic:
    return ChatAnthropic(
        model="claude-sonnet-4-20250514",
        api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        temperature=0,
    )


# ---------------------------------------------------------------------------
# Node: Intent Classification
# ---------------------------------------------------------------------------

INTENT_SYSTEM_PROMPT = """\
You are an intent classifier for a university course management system.
Given a user message, classify the intent into exactly one of:
- "action" — the user wants to CREATE, UPDATE, or DELETE something (grades, enrollment, assignments)
- "query" — the user wants to READ information or get tutoring help (course info, schedules, Q&A)
- "planning" — the user wants multi-step reasoning (semester planning, prerequisite analysis, course recommendations)

Respond with ONLY a JSON object: {"intent": "<action|query|planning>", "confidence": <0.0-1.0>}
No other text."""


async def classify_intent(state: AgentState) -> dict:
    """Classify user intent and return the classification result."""
    llm = _get_llm()
    messages = [
        SystemMessage(content=INTENT_SYSTEM_PROMPT),
        HumanMessage(content=state["messages"][-1].content),
    ]
    response = await llm.ainvoke(messages)

    import json

    try:
        result = json.loads(response.content)
        intent = result.get("intent", "query")
        confidence = result.get("confidence", 0.5)
    except (json.JSONDecodeError, AttributeError):
        intent = "query"
        confidence = 0.5

    return {
        "intent": intent,
        "intent_confidence": confidence,
    }


# ---------------------------------------------------------------------------
# Node: Agent Routing
# ---------------------------------------------------------------------------

AGENT_MAP = {
    "action": "action_agent",
    "query": "query_agent",
    "planning": "planning_agent",
}


async def route_to_agent(state: AgentState) -> dict:
    """Route to the appropriate agent based on classified intent."""
    intent = state.get("intent", "query")
    selected = AGENT_MAP.get(intent, "query_agent")
    return {"selected_agent": selected}


# ---------------------------------------------------------------------------
# Node: Safety Check (placeholder — implemented in Phase 2)
# ---------------------------------------------------------------------------

async def safety_check(state: AgentState) -> dict:
    """Placeholder safety check. Returns safe by default.

    Full two-layer safety (static + dynamic) is implemented in Phase 2.
    """
    return {
        "safety_result": SafetyResult(flagged=False),
        "requires_approval": False,
    }


# ---------------------------------------------------------------------------
# Node: Execution (placeholder — agents implemented in separate PRs)
# ---------------------------------------------------------------------------

async def execute_agent(state: AgentState) -> dict:
    """Execute the selected agent. Routes to real agent or placeholder."""
    agent = state.get("selected_agent", "unknown")

    llm = _get_llm()

    if agent == "action_agent":
        return await run_action_agent(state, llm)

    if agent == "query_agent":
        return await run_query_agent(state, llm)

    # Placeholder for agents not yet implemented
    user_msg = state["messages"][-1].content if state.get("messages") else ""
    return {
        "response": f"[{agent}] Received: {user_msg}",
        "tool_calls": [],
    }


# ---------------------------------------------------------------------------
# Node: Response Generation
# ---------------------------------------------------------------------------

async def generate_response(state: AgentState) -> dict:
    """Format and return the final response."""
    return {"response": state.get("response", "I'm not sure how to help with that.")}


# ---------------------------------------------------------------------------
# Conditional edge: route by agent type
# ---------------------------------------------------------------------------

def _route_by_agent(state: AgentState) -> str:
    """Conditional edge to route to the correct execution path."""
    if state.get("requires_approval") and state.get("safety_result"):
        sr = state["safety_result"]
        if isinstance(sr, SafetyResult) and sr.flagged:
            return "awaiting_approval"
    return "execute"


# ---------------------------------------------------------------------------
# Graph Construction
# ---------------------------------------------------------------------------

def build_graph() -> StateGraph:
    """Build and compile the LangGraph state machine.

    Flow: intent_classification → agent_routing → safety_check
          → (execute | awaiting_approval) → response_generation → END
    """
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("intent_classification", classify_intent)
    graph.add_node("agent_routing", route_to_agent)
    graph.add_node("safety_check", safety_check)
    graph.add_node("execution", execute_agent)
    graph.add_node("response_generation", generate_response)

    # Add edges
    graph.set_entry_point("intent_classification")
    graph.add_edge("intent_classification", "agent_routing")
    graph.add_edge("agent_routing", "safety_check")
    graph.add_conditional_edges(
        "safety_check",
        _route_by_agent,
        {
            "execute": "execution",
            "awaiting_approval": "response_generation",  # HiTL interrupt in Phase 2
        },
    )
    graph.add_edge("execution", "response_generation")
    graph.add_edge("response_generation", END)

    return graph


def create_app():
    """Create and compile the state machine graph."""
    graph = build_graph()
    return graph.compile()
