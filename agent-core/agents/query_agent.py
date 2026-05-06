"""Query Agent — read operations and tutoring.

Two entry points:

1. `run_query_agent(state, llm)` — adapter that invokes a compiled
   subgraph (3-node internal flow: route → execute → format). Internal
   state is hidden behind `QueryAgentOutput` at the boundary. Used by
   the intent router for one-shot questions.

2. `run_query_step(step, step_outputs)` — plan-driven path called by the
   planning DAG executor. Receives a typed `PlanStep` (with `query_source`
   + `query_params` already locked in by make_plan) and returns a typed
   Pydantic object — `StudentTranscript`, `DegreeProgram`,
   `list[CourseSection]`, or `list[SyllabusChunk]`. Talking Point 2: the
   output schema collapses re-parse hallucinations.
"""

from __future__ import annotations

import json
from datetime import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph

from agents.subgraph_states import (
    QUERY_INTERNAL_ONLY_KEYS,
    QueryAgentInput,
    QueryAgentInternalState,
    QueryAgentOutput,
)
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

QUERY_AGENT_SYSTEM_PROMPT = """\
You are the Query Agent for a university course management system.

## Identity
You handle all READ operations: looking up course information, schedules, syllabi,
and answering academic questions. You also provide tutoring help using course materials.
You do NOT modify any data — that is the Action Agent's job.
You do NOT plan multi-step workflows — that is the Planning Agent's job.

## Retrieval Instructions
- Always use the appropriate tool to look up information before answering.
- If the query is about a specific course, use course_lookup with the course ID.
- If the query is about schedules or timing, use schedule_query.
- If the query is about course content or materials, use syllabus_retrieve.
- For tutoring questions, use syllabus_retrieve to get relevant materials, then
  generate an educational response based on the retrieved context.

## Available Tools
- course_lookup: Look up course information (title, instructor, credits, description)
- schedule_query: Query class schedules (meeting times, rooms, days)
- syllabus_retrieve: Retrieve syllabus and course materials for a course

## Output Format
Respond with a JSON object:
{
  "tool": "<tool_name>",
  "arguments": { ... },
  "query_type": "deterministic" | "tutoring"
}

The query_type field indicates whether the response can be cached:
- "deterministic": factual lookups (course info, schedules) — cacheable
- "tutoring": educational explanations, Q&A — not cacheable
"""


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
# Standalone tools (LLM-driven path, kept for back-compat with intent router)
# ---------------------------------------------------------------------------


@tool
def course_lookup(course_id: str) -> dict:
    """Look up course information by course ID."""
    catalog_lookup = {c.course_code: c for c in _MOCK_CATALOG}
    legacy = {
        "CS101": {"title": "Introduction to Computer Science", "instructor": "Dr. Smith",
                  "credits": 3, "description": "Fundamentals of programming and computational thinking."},
        "CS201": {"title": "Data Structures and Algorithms", "instructor": "Dr. Johnson",
                  "credits": 3, "description": "Arrays, linked lists, trees, graphs, sorting, and searching."},
        "MATH200": {"title": "Linear Algebra", "instructor": "Dr. Lee",
                    "credits": 4, "description": "Vectors, matrices, linear transformations, eigenvalues."},
    }
    cid = course_id.upper()
    if cid in legacy:
        return {"success": True, "course_id": cid, **legacy[cid]}
    if cid in catalog_lookup:
        c = catalog_lookup[cid]
        return {
            "success": True, "course_id": cid,
            "title": cid, "instructor": c.instructor, "credits": c.credits,
            "description": f"{cid} (term {c.term})",
        }
    return {"success": False, "message": f"Course {course_id} not found."}


@tool
def schedule_query(course_id: str) -> dict:
    """Query class schedule for a course."""
    legacy = {
        "CS101": {"days": "Mon/Wed/Fri", "time": "10:00-10:50 AM", "room": "Science Hall 201"},
        "CS201": {"days": "Tue/Thu", "time": "2:00-3:15 PM", "room": "Engineering 305"},
        "MATH200": {"days": "Mon/Wed", "time": "1:00-2:15 PM", "room": "Math Building 110"},
    }
    cid = course_id.upper()
    if cid in legacy:
        return {"success": True, "course_id": cid, **legacy[cid]}
    catalog_lookup = {c.course_code: c for c in _MOCK_CATALOG}
    if cid in catalog_lookup:
        c = catalog_lookup[cid]
        m = c.meetings[0] if c.meetings else None
        if m:
            return {
                "success": True, "course_id": cid,
                "days": "/".join(m.days),
                "time": f"{m.start.strftime('%H:%M')}-{m.end.strftime('%H:%M')}",
                "room": "TBD",
            }
    return {"success": False, "message": f"Schedule for {course_id} not found."}


@tool
def syllabus_retrieve(course_id: str, topic: str = "") -> dict:
    """Retrieve syllabus and course materials using hybrid retrieval."""
    chunks = _query_syllabus_rag({"course_id": course_id, "topic": topic})
    if not chunks:
        return {"success": False, "message": f"Syllabus for {course_id} not found."}
    return {
        "success": True,
        "course_id": course_id.upper(),
        "topics": [ch.content for ch in chunks],
        "materials": f"Retrieved {len(chunks)} documents via hybrid retrieval.",
    }


QUERY_TOOLS = {
    "course_lookup": course_lookup,
    "schedule_query": schedule_query,
    "syllabus_retrieve": syllabus_retrieve,
}


# ---------------------------------------------------------------------------
# Compiled subgraph — 3 internal nodes, internal state hidden at boundary
# ---------------------------------------------------------------------------


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
            # No structured plan — short-circuit with the raw text as response.
            return {
                "route_decision": {},
                "selected_tool": "",
                "tool_arguments": {},
                "query_type": "",
                "response_text": raw,
                "success": True,
            }
        return {
            "route_decision": decision,
            "selected_tool": decision.get("tool", ""),
            "tool_arguments": decision.get("arguments", {}),
            "query_type": decision.get("query_type", ""),
        }
    return route_query


async def _execute_query_tool(state: QueryAgentInternalState) -> dict:
    # Already short-circuited by route?
    if state.get("response_text") and not state.get("selected_tool"):
        return {}

    tool_name = state.get("selected_tool", "")
    args = state.get("tool_arguments", {})
    tool_func = QUERY_TOOLS.get(tool_name)

    if tool_func is None:
        return {
            "raw_tool_result": {"success": False, "message": f"Unknown tool: {tool_name}"},
            "execution_error": f"Unknown tool: {tool_name}",
            "success": False,
            "response_text": f"Unknown tool: {tool_name}",
        }

    try:
        result = tool_func.invoke(args)
        return {"raw_tool_result": result, "success": bool(result.get("success", False))}
    except Exception as e:
        return {
            "raw_tool_result": {"success": False, "message": str(e)},
            "execution_error": str(e),
            "success": False,
            "response_text": f"Retrieval failed: {e}",
        }


async def _format_query_response(state: QueryAgentInternalState) -> dict:
    # Already produced by an earlier node?
    if state.get("response_text"):
        return {}
    raw = state.get("raw_tool_result", {})
    if raw.get("success"):
        return {"response_text": _format_tool_result(state.get("selected_tool", ""), raw)}
    return {"response_text": raw.get("message", "No results found.")}


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
        selected_tool=final.get("selected_tool", "") or "",
        query_type=final.get("query_type", "") or "",
    )


# ---------------------------------------------------------------------------
# Orchestrator-facing adapter — preserves the old return shape
# ---------------------------------------------------------------------------


async def run_query_agent(state: AgentState, llm) -> dict:
    """Adapter: invokes the compiled subgraph and reshapes for AgentState.

    The orchestrator (and existing tests) expect `{response, tool_calls}`.
    This wrapper rebuilds that shape from the typed `QueryAgentOutput`,
    using the adapter as the single source of truth for the boundary.
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

    if not output.selected_tool:
        # No tool was invoked (LLM returned non-JSON). Synthesize an empty trace.
        return {"response": output.response, "tool_calls": []}

    # Rebuild a ToolCall trace from the boundary output. We re-run nothing —
    # the subgraph already executed; this just reconstructs the visible record.
    # raw_tool_result is internal-only, so we synthesize a success/failure
    # marker from the typed boundary fields.
    return {
        "response": output.response,
        "tool_calls": [ToolCall(
            tool_name=output.selected_tool,
            arguments={},
            result=None,
            success=output.success,
            error=None if output.success else "Unknown tool" if "Unknown tool" in output.response else None,
        )],
    }


def _format_tool_result(tool_name: str, result: dict) -> str:
    if tool_name == "course_lookup":
        return (f"{result['title']} ({result['course_id']})\n"
                f"Instructor: {result['instructor']}\n"
                f"Credits: {result['credits']}\n"
                f"{result['description']}")
    elif tool_name == "schedule_query":
        return (f"{result['course_id']} meets {result['days']} at {result['time']}\n"
                f"Room: {result['room']}")
    elif tool_name == "syllabus_retrieve":
        topics = "\n".join(f"  - {t}" for t in result.get("topics", []))
        return f"{result['course_id']} Syllabus:\n{topics}\n{result.get('materials', '')}"
    return json.dumps(result, indent=2)
