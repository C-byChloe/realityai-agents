"""Query Agent — handles all read operations and tutoring.

Responsible for course lookups, schedule queries, syllabus retrieval,
and Q&A tutoring. Uses basic vector retrieval initially; upgraded to
hybrid retrieval pipeline in Phase 2.
"""

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from state import AgentState, ToolCall

# ---------------------------------------------------------------------------
# System Prompt
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
# Mock Tool Implementations (replaced with hybrid retrieval in Phase 2)
# ---------------------------------------------------------------------------


@tool
def course_lookup(course_id: str) -> dict:
    """Look up course information by course ID."""
    # Mock data — replaced with real DB/retrieval in Phase 2
    mock_courses = {
        "CS101": {
            "course_id": "CS101",
            "title": "Introduction to Computer Science",
            "instructor": "Dr. Smith",
            "credits": 3,
            "description": "Fundamentals of programming and computational thinking.",
        },
        "CS201": {
            "course_id": "CS201",
            "title": "Data Structures and Algorithms",
            "instructor": "Dr. Johnson",
            "credits": 3,
            "description": "Arrays, linked lists, trees, graphs, sorting, and searching.",
        },
        "MATH200": {
            "course_id": "MATH200",
            "title": "Linear Algebra",
            "instructor": "Dr. Lee",
            "credits": 4,
            "description": "Vectors, matrices, linear transformations, eigenvalues.",
        },
    }
    course = mock_courses.get(course_id.upper())
    if course:
        return {"success": True, **course}
    return {"success": False, "message": f"Course {course_id} not found."}


@tool
def schedule_query(course_id: str) -> dict:
    """Query class schedule for a course."""
    mock_schedules = {
        "CS101": {
            "course_id": "CS101",
            "days": "Mon/Wed/Fri",
            "time": "10:00-10:50 AM",
            "room": "Science Hall 201",
        },
        "CS201": {
            "course_id": "CS201",
            "days": "Tue/Thu",
            "time": "2:00-3:15 PM",
            "room": "Engineering 305",
        },
        "MATH200": {
            "course_id": "MATH200",
            "days": "Mon/Wed",
            "time": "1:00-2:15 PM",
            "room": "Math Building 110",
        },
    }
    schedule = mock_schedules.get(course_id.upper())
    if schedule:
        return {"success": True, **schedule}
    return {"success": False, "message": f"Schedule for {course_id} not found."}


@tool
def syllabus_retrieve(course_id: str, topic: str = "") -> dict:
    """Retrieve syllabus and course materials. Optionally filter by topic."""
    mock_syllabi = {
        "CS101": {
            "course_id": "CS101",
            "topics": [
                "Week 1-2: Variables, types, and operators",
                "Week 3-4: Control flow (if/else, loops)",
                "Week 5-6: Functions and scope",
                "Week 7-8: Data structures (lists, dicts)",
                "Week 9-10: Object-oriented programming",
                "Week 11-12: File I/O and exceptions",
                "Week 13-14: Recursion and algorithms intro",
            ],
            "materials": "Textbook: Think Python 3rd Ed.",
        },
    }
    syllabus = mock_syllabi.get(course_id.upper())
    if syllabus:
        return {"success": True, **syllabus}
    return {"success": False, "message": f"Syllabus for {course_id} not found."}


# Tool registry
QUERY_TOOLS = {
    "course_lookup": course_lookup,
    "schedule_query": schedule_query,
    "syllabus_retrieve": syllabus_retrieve,
}


# ---------------------------------------------------------------------------
# Agent Execution
# ---------------------------------------------------------------------------


async def run_query_agent(state: AgentState, llm) -> dict:
    """Execute the Query Agent: determine the tool, retrieve data, format response.

    Returns updated state fields: response, tool_calls.
    """
    messages = [
        SystemMessage(content=QUERY_AGENT_SYSTEM_PROMPT),
        HumanMessage(content=state["messages"][-1].content),
    ]
    ai_response = await llm.ainvoke(messages)

    # Parse the LLM response
    try:
        result = json.loads(ai_response.content)
    except (json.JSONDecodeError, AttributeError):
        return {
            "response": ai_response.content if hasattr(ai_response, "content") else str(ai_response),
            "tool_calls": [],
        }

    tool_name = result.get("tool", "")
    arguments = result.get("arguments", {})

    tool_func = QUERY_TOOLS.get(tool_name)
    if not tool_func:
        return {
            "response": f"Unknown tool: {tool_name}",
            "tool_calls": [ToolCall(tool_name=tool_name, arguments=arguments, success=False,
                                    error=f"Unknown tool: {tool_name}")],
        }

    try:
        tool_result = tool_func.invoke(arguments)

        # Format a readable response from tool result
        if tool_result.get("success"):
            response = _format_tool_result(tool_name, tool_result)
        else:
            response = tool_result.get("message", "No results found.")

        return {
            "response": response,
            "tool_calls": [ToolCall(tool_name=tool_name, arguments=arguments,
                                    result=tool_result, success=True)],
        }
    except Exception as e:
        return {
            "response": f"Retrieval failed: {e}",
            "tool_calls": [ToolCall(tool_name=tool_name, arguments=arguments,
                                    success=False, error=str(e))],
        }


def _format_tool_result(tool_name: str, result: dict) -> str:
    """Format tool result into a human-readable response."""
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
