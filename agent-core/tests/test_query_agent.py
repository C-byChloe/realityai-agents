"""Unit tests for the standalone Query Agent.

Verifies that the standalone path (intent-router-driven, single-shot LLM)
uses the **same `QuerySource` vocabulary and `_SOURCE_HANDLERS`** as the
plan-driven path. Two dispatch flows, one shared data layer.
"""

import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agents.query_agent import (
    _SOURCE_HANDLERS,
    _format_typed_result,
    run_query_agent,
)
from schemas.plan import QuerySource
from schemas.query_outputs import (
    CourseSection,
    DegreeProgram,
    MeetingTime,
    RequirementNode,
    StudentTranscript,
    SyllabusChunk,
    TranscriptEntry,
)
from state import AgentState


def _make_state(user_msg: str, **overrides) -> AgentState:
    base = {
        "messages": [HumanMessage(content=user_msg)],
        "intent": "query",
        "intent_confidence": 0.9,
        "selected_agent": "query_agent",
        "user_role": "student",
        "outer_safety_result": None,
        "tool_calls": [],
        "response": "",
        "user_id": "test-user",
        "session_id": "test-session",
        "requires_approval": False,
        "approval_status": None,
    }
    base.update(overrides)
    return base


def _mock_llm(content: str) -> AsyncMock:
    mock = AsyncMock()
    mock.ainvoke.return_value = AIMessage(content=content)
    return mock


# ---------------------------------------------------------------------------
# Source registry — vocabulary alignment with plan path
# ---------------------------------------------------------------------------


class TestSourceRegistry:
    """The standalone path uses the same _SOURCE_HANDLERS as the plan path."""

    def test_all_four_sources_registered(self):
        registered = set(_SOURCE_HANDLERS.keys())
        assert registered == {
            QuerySource.CANVAS,
            QuerySource.DEGREE_DB,
            QuerySource.CATALOG_DB,
            QuerySource.SYLLABUS_RAG,
        }

    def test_source_handlers_are_callable(self):
        for handler in _SOURCE_HANDLERS.values():
            assert callable(handler)


# ---------------------------------------------------------------------------
# Standalone agent execution — LLM picks one source
# ---------------------------------------------------------------------------


class TestRunQueryAgentDispatch:
    async def test_routes_to_canvas(self):
        llm_response = json.dumps({
            "source": "canvas",
            "params": {"user_id": "u1"},
            "query_type": "deterministic",
        })
        state = _make_state("Show me my transcript")
        result = await run_query_agent(state, _mock_llm(llm_response))

        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0].tool_name == "query/canvas"
        assert result["tool_calls"][0].success is True
        # Response should be human-readable transcript
        assert "Transcript" in result["response"]
        assert "CS101" in result["response"]  # u1's mocked transcript has CS101

    async def test_routes_to_catalog_db(self):
        llm_response = json.dumps({
            "source": "catalog_db",
            "params": {"term": "S26"},
            "query_type": "deterministic",
        })
        state = _make_state("What courses are offered next semester?")
        result = await run_query_agent(state, _mock_llm(llm_response))

        assert result["tool_calls"][0].tool_name == "query/catalog_db"
        assert "section" in result["response"].lower()
        assert "CS401" in result["response"]  # mocked catalog has CS401

    async def test_routes_to_syllabus_rag(self):
        llm_response = json.dumps({
            "source": "syllabus_rag",
            "params": {"course_id": "CS101"},
            "query_type": "tutoring",
        })
        state = _make_state("Tell me about CS101")
        result = await run_query_agent(state, _mock_llm(llm_response))

        assert result["tool_calls"][0].tool_name == "query/syllabus_rag"
        assert result["tool_calls"][0].success is True

    async def test_routes_to_degree_db(self):
        llm_response = json.dumps({
            "source": "degree_db",
            "params": {"major": "CS", "track": "AI", "cohort": "2024-2027"},
            "query_type": "deterministic",
        })
        state = _make_state("What does the AI track require?")
        result = await run_query_agent(state, _mock_llm(llm_response))

        assert result["tool_calls"][0].tool_name == "query/degree_db"
        assert result["tool_calls"][0].success is True
        assert "Degree program" in result["response"]


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


class TestRunQueryAgentFailures:
    async def test_handles_unknown_source(self):
        llm_response = json.dumps({
            "source": "made_up_source",
            "params": {},
            "query_type": "deterministic",
        })
        state = _make_state("Do something weird")
        result = await run_query_agent(state, _mock_llm(llm_response))

        assert result["tool_calls"][0].success is False
        assert "Unknown data source" in result["response"]

    async def test_handles_missing_required_params(self):
        """canvas handler raises when user_id absent."""
        llm_response = json.dumps({
            "source": "canvas",
            "params": {},  # missing user_id
            "query_type": "deterministic",
        })
        state = _make_state("Show me my grades")
        result = await run_query_agent(state, _mock_llm(llm_response))

        assert result["tool_calls"][0].success is False
        assert "user_id" in result["response"]

    async def test_handles_malformed_response(self):
        state = _make_state("What is CS101?")
        result = await run_query_agent(state, _mock_llm("Just a plain text answer"))

        # Non-JSON LLM output: standalone subgraph passes the raw text
        # through as the response and emits no tool_call trace
        assert result["response"] == "Just a plain text answer"
        assert result["tool_calls"] == []


# ---------------------------------------------------------------------------
# Typed-result formatting — each shape renders cleanly
# ---------------------------------------------------------------------------


class TestFormatTypedResult:
    def test_format_student_transcript(self):
        t = StudentTranscript(
            user_id="u1",
            entries=[
                TranscriptEntry(course_code="CS101", grade="A", credits=3,
                                term="F24", is_passed=True),
                TranscriptEntry(course_code="CS102", grade="F", credits=3,
                                term="F24", is_passed=False),
            ],
        )
        out = _format_typed_result(t)
        assert "CS101" in out and "A" in out
        assert "CS102" in out  # both pass and fail listed for trace
        assert "u1" in out

    def test_format_empty_transcript(self):
        out = _format_typed_result(StudentTranscript(user_id="u_unknown", entries=[]))
        assert "No transcript" in out

    def test_format_degree_program(self):
        p = DegreeProgram(
            major="CS", track="AI", cohort="2024-2027",
            root=RequirementNode(
                requirement_id="root", kind="and", name="AI track",
                children=[
                    RequirementNode(
                        requirement_id="electives", kind="leaf",
                        name="AI electives",
                        pool=["CS401", "CS402"], need=2,
                    ),
                ],
            ),
        )
        out = _format_typed_result(p)
        assert "CS" in out and "AI" in out
        assert "ALL OF" in out
        assert "CS401" in out and "CS402" in out

    def test_format_course_section_list(self):
        from datetime import time
        sections = [
            CourseSection(
                course_code="CS401", section="001", term="S26", credits=3,
                instructor="Dr. Lee",
                meetings=[MeetingTime(days=["T", "R"],
                                       start=time(11, 40), end=time(12, 55))],
            ),
        ]
        out = _format_typed_result(sections)
        assert "CS401" in out
        assert "Dr. Lee" in out
        assert "11:40" in out

    def test_format_syllabus_chunks(self):
        chunks = [
            SyllabusChunk(
                chunk_id="DOC001", course_id="CS101",
                content="CS101 covers programming fundamentals",
                score=0.9,
            ),
        ]
        out = _format_typed_result(chunks)
        assert "CS101" in out
        assert "programming fundamentals" in out

    def test_syllabus_chunks_are_wrapped_in_data_markers(self):
        """Phase 8.1 indirect-injection defense: every retrieved chunk
        is wrapped in `[BEGIN-DATA:nonce]` / `[END-DATA:nonce]`
        spotlighting markers so downstream LLM consumers (instructed
        by the system prompt) treat the content as data, not directives.
        Pins the formatter wire-in against accidental regression.
        """
        import re

        chunks = [
            SyllabusChunk(
                chunk_id="DOC001", course_id="CS101",
                content="Topics include arrays and lists.",
                score=0.9,
            ),
            SyllabusChunk(
                chunk_id="DOC002", course_id="CS101",
                content="Prerequisites: MATH200.",
                score=0.85,
            ),
        ]
        out = _format_typed_result(chunks)

        # Both chunks wrapped — one BEGIN/END pair per chunk.
        begins = re.findall(r"\[BEGIN-DATA:[0-9a-f]+\]", out)
        ends = re.findall(r"\[END-DATA:[0-9a-f]+\]", out)
        assert len(begins) == 2
        assert len(ends) == 2
        # Markers in one formatter call share a single nonce.
        nonces_seen = {m[len("[BEGIN-DATA:"):-1] for m in begins}
        assert len(nonces_seen) == 1, (
            f"expected single per-call nonce, got {nonces_seen}"
        )

    def test_two_separate_format_calls_use_distinct_nonces(self):
        """Each formatter call generates a fresh nonce. Without this an
        attacker observing one request's nonce could plant matching
        sentinel text for the next request to escape its boundary.
        """
        import re

        chunks = [
            SyllabusChunk(
                chunk_id="DOC001", course_id="CS101",
                content="data structures intro",
                score=0.9,
            ),
        ]
        out_a = _format_typed_result(chunks)
        out_b = _format_typed_result(chunks)
        nonce_a = re.search(r"\[BEGIN-DATA:([0-9a-f]+)\]", out_a).group(1)
        nonce_b = re.search(r"\[BEGIN-DATA:([0-9a-f]+)\]", out_b).group(1)
        assert nonce_a != nonce_b

    def test_format_empty_list(self):
        assert "No results" in _format_typed_result([])
