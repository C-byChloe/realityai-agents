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
from safety.outer.node import outer_safety_check
from safety.outer.schemas import SafetyDecision
from state import AgentState

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

async def outer_safety_node(state: AgentState) -> dict:
    """Wrap `outer_safety_check` to inject the production LLM client.

    Tier 1 (RBAC) and Tier 2 (static rules) don't need an LLM; only
    Tier 3 (intent analyzer) does. The LLM is constructed via
    `_get_llm()` at request time so tests that monkeypatch `_get_llm`
    can swap it out.
    """
    return await outer_safety_check(state, _get_llm())


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
    outer = state.get("outer_safety_result")
    reason = (
        outer.final_reason_human
        if outer and outer.final_reason_human
        else "Operation flagged for review"
    )

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
# Node: Reject (terminal node when outer safety returns DENY)
# ---------------------------------------------------------------------------

async def reject_node(state: AgentState) -> dict:
    """Materialize the rejection response from outer_safety_result.

    Reached only via `_route_after_outer_safety("deny")` — i.e., when
    one of the three safety tiers returned DENY. Writes the
    `final_reason_human` directly into `response`; the graph then flows
    to `response_generation` and ends.
    """
    outer = state.get("outer_safety_result")
    reason = (
        outer.final_reason_human
        if outer and outer.final_reason_human
        else "Request denied by safety policy."
    )
    return {"response": reason}


# ---------------------------------------------------------------------------
# Conditional edge: route by outer safety verdict
# ---------------------------------------------------------------------------

def _route_after_outer_safety(state: AgentState) -> str:
    """Outer safety produces a tri-state verdict; map it to a graph branch.

    ALLOW           → coref_resolver → execution
    DENY            → reject_node (terminal)
    FLAG_FOR_REVIEW → hitl_approval (LangGraph interrupt → resume)
    """
    outer = state.get("outer_safety_result")
    if outer is None:
        # Defensive — should not happen if outer_safety_node ran.
        return "deny"
    if outer.final_decision == SafetyDecision.ALLOW:
        return "allow"
    if outer.final_decision == SafetyDecision.DENY:
        return "deny"
    return "flag"


# ---------------------------------------------------------------------------
# Graph Construction
# ---------------------------------------------------------------------------

def build_graph(*, coref_llm=None) -> StateGraph:
    """Build and compile the LangGraph state machine.

    Flow: intent_classification → agent_routing → outer_safety_check
          → conditional (3-way):
              allow → coref_resolver → execution
              deny  → reject_node (terminal)
              flag  → hitl_approval (LangGraph interrupt → resume)
          → response_generation → END

    Outer safety runs BEFORE coref_resolver (ADR 005 D7) — coref is an
    LLM rewrite and must not sit between user signal and the safety
    pipeline.

    `coref_llm` defaults to None (uses the production ChatAnthropic
    client); tests can pass a mock LLM via `build_graph(coref_llm=mock)`.
    """
    graph = StateGraph(AgentState)

    coref_resolver = make_coref_resolver_node(llm=coref_llm)

    # Add nodes
    graph.add_node("intent_classification", classify_intent)
    graph.add_node("agent_routing", route_to_agent)
    graph.add_node("outer_safety_check", outer_safety_node)
    graph.add_node("coref_resolver", coref_resolver)
    graph.add_node("hitl_approval", hitl_approval)
    graph.add_node("reject_node", reject_node)
    graph.add_node("execution", execute_agent)
    graph.add_node("response_generation", generate_response)

    # Add edges
    graph.set_entry_point("intent_classification")
    graph.add_edge("intent_classification", "agent_routing")
    graph.add_edge("agent_routing", "outer_safety_check")
    graph.add_conditional_edges(
        "outer_safety_check",
        _route_after_outer_safety,
        {
            "allow": "coref_resolver",
            "deny": "reject_node",
            "flag": "hitl_approval",
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
    graph.add_edge("reject_node", "response_generation")
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
