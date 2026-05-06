"""LangGraph state machine orchestrator for multi-agent routing."""

import os

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

from agents.action_agent import run_action_agent
from agents.planning_agent import run_planning_agent
from agents.query_agent import run_query_agent
from preprocessing.coref_resolver import make_coref_resolver_node
from safety.merge import run_safety_check
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
# Node: Safety Check (two-layer: static + dynamic, OR merge)
# ---------------------------------------------------------------------------

async def safety_check(state: AgentState) -> dict:
    """Run two-layer safety check in parallel and merge with OR policy.

    Static risk classifier + dynamic LLM intent analyzer.
    If either flags, requires_approval is set to True.
    """
    user_msg = state["messages"][-1].content if state.get("messages") else ""

    # Determine tool name from intent (pre-execution heuristic)
    tool_name = _infer_tool_from_intent(state.get("intent", "query"))

    llm = _get_llm()
    result = await run_safety_check(user_msg, tool_name, llm)

    return {
        "safety_result": result,
        "requires_approval": result.flagged,
    }


def _infer_tool_from_intent(intent: str) -> str | None:
    """Map intent to a representative tool for static risk classification.

    Action intents map to a high-risk tool; query/planning map to None
    (low-risk, handled by dynamic analyzer).
    """
    if intent == "action":
        return "grade_update"  # Representative high-risk tool
    return None


# ---------------------------------------------------------------------------
# Node: HiTL Approval (interrupt → await → resume/reject)
# ---------------------------------------------------------------------------

async def hitl_approval(state: AgentState) -> dict:
    """Pause the graph and wait for an external approval decision.

    Resume protocol (matches the architecture diagram and interview script):

      1. The graph is compiled with `interrupt_before=["hitl_approval"]`
         + a checkpointer (MemorySaver in-process; PostgresSaver in prod).
      2. When safety flags a request, the conditional edge routes here.
      3. LangGraph pauses BEFORE this node runs. `app.ainvoke(...)` returns
         and the caller (API gateway) pushes an approval card to the user
         via SSE.
      4. When the user clicks Approve/Reject, the gateway resumes via
            app.ainvoke(Command(resume={"approved": True}), config)
         where `config = {"configurable": {"thread_id": ...}}`.
      5. `interrupt()` in this node returns the resume payload. The node
         updates `approval_status` and the conditional edge routes:
            approved → coref_resolver → execution → response_generation
            rejected → response_generation (with rejection message)

    The Python process is not blocked while paused — checkpointed state
    lives in the saver, the API server stays stateless, and any worker
    can pick up the resume call.
    """
    safety = state.get("safety_result")
    reason = safety.reason if safety else "Operation flagged for review"

    decision = interrupt(
        {
            "type": "approval_request",
            "reason": reason,
            "session_id": state.get("session_id", ""),
            "user_id": state.get("user_id", ""),
        }
    )

    approved = bool(decision and decision.get("approved"))
    if approved:
        return {"approval_status": "approved"}

    return {
        "approval_status": "rejected",
        "response": (
            f"Operation rejected by reviewer.\nReason: {reason}"
        ),
    }


def _route_after_hitl(state: AgentState) -> str:
    """After hitl_approval resumes, branch on the decision."""
    return "approved" if state.get("approval_status") == "approved" else "rejected"


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

    if agent == "planning_agent":
        return await run_planning_agent(state, llm)

    # Placeholder for unknown agents
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

def build_graph(*, coref_llm=None) -> StateGraph:
    """Build and compile the LangGraph state machine.

    Flow: intent_classification → agent_routing → safety_check
          → conditional:
              execute → coref_resolver → execution
              awaiting_approval → hitl_approval
          → response_generation → END

    `coref_resolver` runs ONLY on the execute branch — the awaiting_approval
    branch sees the raw user message. This ordering keeps the safety
    pipeline operating on raw input (see preprocessing/coref_resolver.py).

    `coref_llm` defaults to None (uses the production ChatAnthropic client);
    tests can pass a mock LLM via `build_graph(coref_llm=mock)`.
    """
    graph = StateGraph(AgentState)

    coref_resolver = make_coref_resolver_node(llm=coref_llm)

    # Add nodes
    graph.add_node("intent_classification", classify_intent)
    graph.add_node("agent_routing", route_to_agent)
    graph.add_node("safety_check", safety_check)
    graph.add_node("coref_resolver", coref_resolver)
    graph.add_node("hitl_approval", hitl_approval)
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
            "execute": "coref_resolver",
            "awaiting_approval": "hitl_approval",
        },
    )
    graph.add_edge("coref_resolver", "execution")
    graph.add_conditional_edges(
        "hitl_approval",
        _route_after_hitl,
        {
            "approved": "coref_resolver",
            "rejected": "response_generation",
        },
    )
    graph.add_edge("execution", "response_generation")
    graph.add_edge("response_generation", END)

    return graph


def create_app(*, checkpointer=None, coref_llm=None):
    """Create and compile the state machine graph.

    The graph is compiled with `interrupt_before=["hitl_approval"]` and
    a checkpointer so the safety-flagged path can pause and be resumed
    by an external `Command(resume={"approved": bool})`.

    `checkpointer` defaults to an in-process `MemorySaver` — fine for
    unit tests and single-process deployments. For production, pass
    a `PostgresSaver` (from `langgraph-checkpoint-postgres`) so state
    survives process restarts and the API server stays stateless across
    pause/resume.

    `coref_llm` is forwarded to `build_graph` for tests that inject a
    mock LLM into the coref_resolver node.
    """
    if checkpointer is None:
        checkpointer = MemorySaver()
    graph = build_graph(coref_llm=coref_llm)
    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["hitl_approval"],
    )
