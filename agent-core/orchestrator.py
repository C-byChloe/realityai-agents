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
from prompts import load_prompt
from safety.outer.node import outer_safety_check
from safety.outer.schemas import SafetyDecision
from safety.output_filter import filter_response
from state import AgentState

import logging

_logger = logging.getLogger(__name__)

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

# Sourced from prompts/intent_classifier.md — see frontmatter for version,
# benchmark binding, and audit status. Do not inline edits here; edit the
# prompt file and bump its version.
INTENT_SYSTEM_PROMPT = load_prompt("intent_classifier")


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
    """Wrap `outer_safety_check` to inject the production PromptGuard.

    Tier 1 (RBAC) and Tier 2 (static rules) don't need a model; only
    Tier 3 (injection guard) does. We pass `prompt_guard=None` so the
    composer resolves the process-default via `get_default_prompt_guard`
    — which uses LocalPromptGuardClient in production (transformers
    installed) and HeuristicPromptGuardClient in dev/CI. Tests inject
    their own client by calling `outer_safety_check` directly with a
    `prompt_guard=` kwarg.
    """
    return await outer_safety_check(state, prompt_guard=None)


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
# Node: Output Filter (Phase 8.2 — defense-in-depth)
# ---------------------------------------------------------------------------

async def output_filter_node(state: AgentState) -> dict:
    """Scrub the final response before it reaches the user.

    Runs `safety.output_filter.filter_response` on `state["response"]`
    and overwrites it with the redacted version. Findings (hash-only,
    never leaked text) land on `state["output_filter_result"]` for
    audit / observability.

    Architectural placement: every path through the graph eventually
    flows through `response_generation`, so this single chokepoint
    catches outputs from the allow / reject / hitl-rejected branches
    uniformly. Tier 1-3 + inner safety + content isolation all defend
    against bad things HAPPENING; this layer ensures evidence of bad
    things doesn't surface to the user even if they did.
    """
    response = state.get("response", "") or ""
    if not response:
        return {}

    result = filter_response(response)
    update: dict = {"output_filter_result": result}

    if result.redactions_count > 0:
        # Log per-category counts for dashboards / alerting. The
        # response itself is overwritten with the redacted version.
        category_counts: dict[str, int] = {}
        for f in result.findings:
            category_counts[f.category.value] = (
                category_counts.get(f.category.value, 0) + 1
            )
        _logger.warning(
            "output_filter redacted %d match(es): %s",
            result.redactions_count,
            category_counts,
        )
        update["response"] = result.redacted_text

    return update


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
    # Phase 8.2 — defense-in-depth output filter as the final node.
    # Every code path funnels through `response_generation`, so a single
    # output_filter node downstream catches all responses (allow path,
    # reject path, hitl-rejected path) before they reach the user.
    graph.add_node("output_filter", output_filter_node)
    graph.add_edge("response_generation", "output_filter")
    graph.add_edge("output_filter", END)

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
