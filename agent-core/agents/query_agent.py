"""Query Agent — read operations and tutoring.

Two dispatch flows, **one shared data layer**:

1. `run_query_agent(state, llm)` — standalone path used by the intent
   router for simple one-shot questions ("What time does CS101 meet?").
   A 3-node compiled subgraph (route → execute → format) where the LLM
   picks a single `QuerySource` and the response is formatted as a
   string for the user. Cheap: 1 LLM call, no DAG.

2. `run_query_step(step, step_outputs)` — plan-driven path called by
   the planning DAG executor. Receives a typed `PlanStep` (with
   `query_source` + `query_params` already locked in by `make_plan`)
   and returns the raw typed Pydantic object so downstream reasoning /
   solver steps can operate on it. Used for multi-step requests.

Both paths share:
  - The same `QuerySource` enum (canvas / degree_db / catalog_db / syllabus_rag)
  - The same `_SOURCE_HANDLERS` registry
  - The same typed mock data (`_MOCK_TRANSCRIPTS`, `_MOCK_DEGREE_PROGRAMS`,
    `_MOCK_CATALOG`)

Dispatch flow differs by complexity (single-shot vs DAG), but the data
contract is consistent. See ADR notes in docs/architecture.md.
"""

from __future__ import annotations

import json
from datetime import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from agents.subgraph_states import (
    QUERY_INTERNAL_ONLY_KEYS,
    QueryAgentInput,
    QueryAgentInternalState,
    QueryAgentOutput,
)
from prompts import load_prompt
from schemas.plan import PlanStep, QuerySource
from schemas.query_outputs import (
    CourseSection,
    DegreeProgram,
    MeetingTime,
    RequirementNode,
    StudentTranscript,
    SyllabusChunk,
    TranscriptEntry,
)
from state import AgentState, ToolCall

# ---------------------------------------------------------------------------
# System Prompt (standalone path)
# ---------------------------------------------------------------------------

# Sourced from prompts/query_agent.md — see frontmatter for version,
# benchmark binding, and audit status. Do not inline edits here; edit the
# prompt file and bump its version.
QUERY_AGENT_SYSTEM_PROMPT = load_prompt("query_agent")


# ---------------------------------------------------------------------------
# Typed mock data stores (backing the per-source query handlers)
# ---------------------------------------------------------------------------


_MOCK_TRANSCRIPTS: dict[str, StudentTranscript] = {
    "u1": StudentTranscript(
        user_id="u1",
        entries=[
            TranscriptEntry(course_code="CS101", grade="A", credits=3, term="F24", is_passed=True),
            TranscriptEntry(course_code="CS201", grade="B+", credits=3, term="S25", is_passed=True),
            TranscriptEntry(course_code="MATH200", grade="A-", credits=4, term="F24", is_passed=True),
        ],
    ),
    "u2": StudentTranscript(
        user_id="u2",
        entries=[
            TranscriptEntry(course_code="CS101", grade="C", credits=3, term="F24", is_passed=True),
            TranscriptEntry(course_code="ENG101", grade="A", credits=3, term="F24", is_passed=True),
        ],
    ),
}


_MOCK_DEGREE_PROGRAMS: dict[tuple[str, str | None, str], DegreeProgram] = {
    ("CS", "AI", "2024-2027"): DegreeProgram(
        major="CS",
        track="AI",
        cohort="2024-2027",
        root=RequirementNode(
            requirement_id="root",
            kind="and",
            children=[
                RequirementNode(
                    requirement_id="ai_electives",
                    kind="leaf",
                    name="AI track electives",
                    pool=["CS401", "CS402", "CS403", "CS404"],
                    need=3,
                ),
                RequirementNode(
                    requirement_id="math_foundation",
                    kind="leaf",
                    name="Math foundation",
                    pool=["MATH200", "MATH210"],
                    need=1,
                ),
            ],
        ),
    ),
}


def _mt(days: list[str], h1: int, m1: int, h2: int, m2: int) -> MeetingTime:
    return MeetingTime(days=days, start=time(h1, m1), end=time(h2, m2))


_MOCK_CATALOG: list[CourseSection] = [
    # F25 sections — current-semester catalog (used by tests asking
    # about CS101 / CS201 / MATH200, which previously lived in the
    # legacy @tool internal mocks)
    CourseSection(course_code="CS101", section="001", term="F25", credits=3,
                  instructor="Dr. Smith",
                  meetings=[_mt(["M", "W", "F"], 10, 0, 10, 50)]),
    CourseSection(course_code="CS201", section="001", term="F25", credits=3,
                  instructor="Dr. Johnson",
                  meetings=[_mt(["T", "R"], 14, 0, 15, 15)]),
    CourseSection(course_code="MATH200", section="001", term="F25", credits=4,
                  instructor="Dr. Lee",
                  meetings=[_mt(["M", "W"], 13, 0, 14, 15)]),

    # S26 sections — next-semester offerings (used by avoid-Fridays plan)
    CourseSection(course_code="CS401", section="001", term="S26", credits=3,
                  instructor="Dr. Lee", meetings=[_mt(["T", "R"], 11, 40, 12, 55)]),
    CourseSection(course_code="CS402", section="001", term="S26", credits=3,
                  instructor="Dr. Park", meetings=[_mt(["F"], 10, 0, 12, 30)]),
    CourseSection(course_code="CS403", section="001", term="S26", credits=3,
                  instructor="Dr. Chen", meetings=[_mt(["M", "W"], 13, 0, 14, 15)]),
    CourseSection(course_code="CS404", section="001", term="S26", credits=3,
                  instructor="Dr. Singh", meetings=[_mt(["T", "R"], 14, 30, 15, 45)]),
    CourseSection(course_code="MATH210", section="001", term="S26", credits=4,
                  instructor="Dr. Davis", meetings=[_mt(["M", "W", "F"], 9, 0, 9, 50)]),
]


# ---------------------------------------------------------------------------
# Per-source query handlers (plan-driven path)
# ---------------------------------------------------------------------------


def _query_canvas(params: dict) -> StudentTranscript:
    user_id = params.get("user_id")
    if not user_id:
        raise ValueError("canvas query requires user_id")
    transcript = _MOCK_TRANSCRIPTS.get(user_id)
    if transcript is None:
        return StudentTranscript(user_id=user_id, entries=[])
    return transcript


def _query_degree_db(params: dict) -> DegreeProgram:
    major = params.get("major")
    track = params.get("track")
    cohort = params.get("cohort")
    if not major or not cohort:
        raise ValueError("degree_db query requires major + cohort")
    program = _MOCK_DEGREE_PROGRAMS.get((major, track, cohort))
    if program is None:
        raise KeyError(f"no degree program for ({major}, {track}, {cohort})")
    return program


def _query_catalog_db(params: dict) -> list[CourseSection]:
    term = params.get("term")
    course_codes = params.get("course_codes")  # optional filter
    days_excluded = params.get("days_excluded") or []

    sections = list(_MOCK_CATALOG)
    if term:
        sections = [s for s in sections if s.term == term]
    if course_codes:
        wanted = set(course_codes)
        sections = [s for s in sections if s.course_code in wanted]
    if days_excluded:
        sections = [s for s in sections if not s.has_meeting_on_days(days_excluded)]
    return sections


def _query_syllabus_rag(params: dict) -> list[SyllabusChunk]:
    """Retrieve syllabus chunks for a course.

    Honors Layer 2 reformulation when present:
      - `_semantic_query` (set by run_query_step from step.semantic_query):
        used as the retrieval query instead of the default
        "<course_id> syllabus <topic>" string.
      - `_query_expansion` (set from step.query_expansion): each paraphrase
        runs as a separate retrieval; results are deduped by chunk_id with
        the highest score per duplicate kept.

    Falls back to the legacy default-query behavior when neither is set,
    so the standalone `syllabus_retrieve` tool path is unaffected.
    """
    from retrieval.hybrid import hybrid_retrieve

    course_id = params.get("course_id", "")
    topic = params.get("topic", "")
    course_filter = course_id.upper() if course_id else None

    primary_query = (
        params.get("_semantic_query")
        or f"{course_id} syllabus {topic}".strip()
    )
    expansion = params.get("_query_expansion") or []

    queries_to_run: list[str] = [primary_query, *expansion]

    # Run each query, then dedupe by doc_id (keep highest score).
    by_id: dict[str, tuple[Any, float]] = {}
    for q in queries_to_run:
        for d in hybrid_retrieve(q, course_id=course_filter, top_n=5):
            doc_id = getattr(d, "doc_id", None) or id(d)
            score = float(getattr(d, "score", 0.0))
            existing = by_id.get(doc_id)
            if existing is None or score > existing[1]:
                by_id[doc_id] = (d, score)

    deduped = sorted(by_id.values(), key=lambda kv: kv[1], reverse=True)
    return [
        SyllabusChunk(
            chunk_id=getattr(d, "doc_id", f"chunk-{i}"),
            course_id=course_id.upper() if course_id else "",
            content=d.content,
            score=score,
        )
        for i, (d, score) in enumerate(deduped)
    ]


_SOURCE_HANDLERS = {
    QuerySource.CANVAS: _query_canvas,
    QuerySource.DEGREE_DB: _query_degree_db,
    QuerySource.CATALOG_DB: _query_catalog_db,
    QuerySource.SYLLABUS_RAG: _query_syllabus_rag,
}


def run_query_step(step: PlanStep, step_outputs: dict[int, Any]) -> Any:
    """Execute a typed query step from a plan.

    Resolves `<from_step_N>` placeholders in `query_params` against
    upstream typed outputs before dispatching to the source handler.
    Returns the typed Pydantic object (or list of Pydantic objects).
    """
    if step.query_source is None or step.query_params is None:
        raise ValueError(f"step {step.step_id}: missing query_source/params")

    handler = _SOURCE_HANDLERS.get(step.query_source)
    if handler is None:
        raise ValueError(f"step {step.step_id}: unknown query_source {step.query_source}")

    params = _resolve_step_refs(step.query_params, step_outputs)

    # Layer 2 reformulation — only forward semantic_query / query_expansion
    # to the syllabus_rag handler. Other sources ignore these fields by
    # design (see ADR: Decision C in the query rewrite plan).
    if step.query_source is QuerySource.SYLLABUS_RAG:
        if step.semantic_query is not None:
            params["_semantic_query"] = step.semantic_query
        if step.query_expansion is not None:
            params["_query_expansion"] = step.query_expansion

    return handler(params)


def _resolve_step_refs(params: dict, step_outputs: dict[int, Any]) -> dict:
    """Replace `<from_step_N>` placeholders with typed upstream values."""
    resolved = {}
    for k, v in params.items():
        if isinstance(v, str) and v.startswith("<from_step_") and v.endswith(">"):
            try:
                ref = int(v[len("<from_step_"):-1])
            except ValueError:
                resolved[k] = v
                continue
            if ref not in step_outputs:
                raise KeyError(f"step ref {v} not yet computed")
            resolved[k] = step_outputs[ref]
        else:
            resolved[k] = v
    return resolved


# ---------------------------------------------------------------------------
# Compiled subgraph — 3 internal nodes, internal state hidden at boundary
# ---------------------------------------------------------------------------
# The subgraph dispatches to the SAME _SOURCE_HANDLERS used by the
# plan-driven path. Difference vs plan path: standalone is single-shot
# (LLM picks ONE source and returns a string to the user), plan is DAG
# (multiple steps feed each other typed Pydantic objects).


def _make_route_node(llm):
    async def route_query(state: QueryAgentInternalState) -> dict:
        messages = [
            SystemMessage(content=QUERY_AGENT_SYSTEM_PROMPT),
            HumanMessage(content=state["user_message"]),
        ]
        ai_response = await llm.ainvoke(messages)
        raw = ai_response.content if hasattr(ai_response, "content") else str(ai_response)
        try:
            decision = json.loads(raw)
        except (json.JSONDecodeError, AttributeError):
            # Non-JSON output — short-circuit with the raw text as the response.
            return {
                "route_decision": {},
                "selected_source": "",
                "source_params": {},
                "query_type": "",
                "response_text": raw,
                "success": True,
            }
        return {
            "route_decision": decision,
            "selected_source": decision.get("source", ""),
            "source_params": decision.get("params", {}) or {},
            "query_type": decision.get("query_type", ""),
        }
    return route_query


async def _execute_query_tool(state: QueryAgentInternalState) -> dict:
    """Dispatch via _SOURCE_HANDLERS — same registry the plan path uses."""
    # Already short-circuited by route_query (non-JSON LLM output)?
    if state.get("response_text") and not state.get("selected_source"):
        return {}

    source_name = state.get("selected_source", "")
    params = state.get("source_params", {}) or {}

    try:
        source_enum = QuerySource(source_name)
    except ValueError:
        return {
            "execution_error": f"Unknown source: {source_name}",
            "success": False,
            "response_text": f"Unknown data source: {source_name}",
        }

    handler = _SOURCE_HANDLERS[source_enum]
    try:
        typed_result = handler(params)
        return {"raw_typed_result": typed_result, "success": True}
    except Exception as e:
        return {
            "execution_error": str(e),
            "success": False,
            "response_text": f"Query failed: {e}",
        }


async def _format_query_response(state: QueryAgentInternalState) -> dict:
    """Format the typed Pydantic result into a user-facing string."""
    if state.get("response_text"):
        return {}
    typed = state.get("raw_typed_result")
    if typed is None:
        return {"response_text": "No results found."}
    return {"response_text": _format_typed_result(typed)}


def _format_typed_result(result: Any) -> str:
    """Render any of the 4 typed source outputs as a human-readable string."""
    if isinstance(result, StudentTranscript):
        if not result.entries:
            return "No transcript entries on file."
        lines = [f"Transcript for {result.user_id} ({result.total_credits} credits earned):"]
        for e in result.entries:
            status = "✓" if e.is_passed else "✗"
            lines.append(f"  {status} {e.course_code}  grade={e.grade}  credits={e.credits}  ({e.term})")
        return "\n".join(lines)

    if isinstance(result, DegreeProgram):
        track = f", track={result.track}" if result.track else ""
        header = f"Degree program: {result.major}{track}, cohort {result.cohort}"
        body = _format_requirement_node(result.root, indent=0)
        return f"{header}\n{body}"

    if isinstance(result, list):
        if not result:
            return "No results found."
        first = result[0]
        if isinstance(first, CourseSection):
            lines = [f"Found {len(result)} section(s):"]
            for c in result:
                meets = "; ".join(
                    f"{'/'.join(m.days)} {m.start.strftime('%H:%M')}-{m.end.strftime('%H:%M')}"
                    for m in c.meetings
                ) or "TBA"
                lines.append(
                    f"  {c.course_code} sec {c.section} ({c.term}) — "
                    f"{c.credits}cr, {c.instructor or 'TBA'}, meets {meets}"
                )
            return "\n".join(lines)
        if isinstance(first, SyllabusChunk):
            # Indirect-injection defense (spotlighting, Hines et al. 2024):
            # wrap retrieved syllabus content in BEGIN-DATA / END-DATA
            # markers carrying a per-call nonce. Downstream LLMs that
            # consume conversation_history (Tier 4 judges, future
            # summarization templates) are instructed via system prompt
            # to treat marker-bounded content as data, never as
            # directives. The nonce makes the boundary unforgeable —
            # an attacker who plants `[END-DATA:abc]` in their syllabus
            # cannot predict the request's actual nonce.
            from safety.content_isolation import new_nonce, wrap_untrusted

            nonce = new_nonce()
            lines = [f"Retrieved {len(result)} syllabus excerpt(s):"]
            for ch in result:
                wrapped = wrap_untrusted(
                    f"[{ch.course_id}] {ch.content}", nonce=nonce
                )
                lines.append(wrapped)
            return "\n".join(lines)

    # Fallback — should not happen if handlers stay aligned with formatters
    return json.dumps(result, default=str, indent=2)


def _format_requirement_node(node: RequirementNode, indent: int) -> str:
    pad = "  " * indent
    if node.kind == "leaf":
        pool = ", ".join(node.pool)
        return f"{pad}- {node.name or node.requirement_id}: need {node.need} of [{pool}]"
    op = "ALL OF" if node.kind == "and" else "ANY OF"
    lines = [f"{pad}{op} ({node.name or node.requirement_id}):"]
    for child in node.children:
        lines.append(_format_requirement_node(child, indent + 1))
    return "\n".join(lines)


def compile_query_agent(llm):
    """Compile the Query Agent subgraph.

    LangGraph nodes: route_query → execute_query_tool → format_query_response.
    Each node mutates only `QueryAgentInternalState`. The orchestrator
    sees only `QueryAgentOutput` after the adapter strips internal keys.
    """
    g = StateGraph(QueryAgentInternalState)
    g.add_node("route_query", _make_route_node(llm))
    g.add_node("execute_query_tool", _execute_query_tool)
    g.add_node("format_query_response", _format_query_response)
    g.add_edge(START, "route_query")
    g.add_edge("route_query", "execute_query_tool")
    g.add_edge("execute_query_tool", "format_query_response")
    g.add_edge("format_query_response", END)
    return g.compile()


async def invoke_query_subgraph(inp: QueryAgentInput, llm) -> QueryAgentOutput:
    """Run the subgraph and project internal state down to the typed Output.

    This is the boundary that enforces internal-state isolation —
    callers only ever see `QueryAgentOutput` fields.
    """
    subgraph = compile_query_agent(llm)
    final = await subgraph.ainvoke({"user_message": inp.user_message})
    return QueryAgentOutput(
        response=final.get("response_text", ""),
        success=bool(final.get("success", False)),
        selected_source=final.get("selected_source", "") or "",
        query_type=final.get("query_type", "") or "",
    )


# ---------------------------------------------------------------------------
# Orchestrator-facing adapter — preserves the {response, tool_calls} shape
# expected by AgentState consumers
# ---------------------------------------------------------------------------


async def run_query_agent(state: AgentState, llm) -> dict:
    """Adapter: invokes the compiled subgraph and reshapes for AgentState.

    Trace fidelity: `tool_calls` carries one `ToolCall` named after the
    selected `QuerySource` (e.g., `query/canvas`) so downstream trace
    consumers see a uniform shape with the plan-path's `_step_label`.
    """
    user_msg = state["messages"][-1].content if state.get("messages") else ""
    output = await invoke_query_subgraph(
        QueryAgentInput(
            user_message=user_msg,
            user_id=state.get("user_id", ""),
            session_id=state.get("session_id", ""),
        ),
        llm,
    )

    if not output.selected_source:
        # LLM emitted non-JSON — no source dispatched. Synthesize an empty trace.
        return {"response": output.response, "tool_calls": []}

    return {
        "response": output.response,
        "tool_calls": [ToolCall(
            tool_name=f"query/{output.selected_source}",
            arguments={},
            result=None,
            success=output.success,
            error=None if output.success else output.response,
        )],
    }
