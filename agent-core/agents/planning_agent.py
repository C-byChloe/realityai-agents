"""Planning Agent — handles multi-step reasoning and task decomposition.

Responsible for complex requests that require chain-of-thought decomposition
and coordination of Action/Query agent tools across multiple sub-steps.
"""

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from agents.action_agent import ACTION_TOOLS
from agents.query_agent import QUERY_TOOLS
from state import AgentState, ToolCall

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

PLANNING_AGENT_SYSTEM_PROMPT = """\
You are the Planning Agent for a university course management system.

## Identity
You handle COMPLEX, MULTI-STEP requests that require reasoning and coordination.
You decompose requests into sub-steps, then execute them using tools from the
Action Agent (write operations) and Query Agent (read operations).

You do NOT handle simple lookups — that is the Query Agent's job.
You do NOT handle single write operations — that is the Action Agent's job.
You handle tasks that require MULTIPLE steps or REASONING across data.

## Chain-of-Thought Decomposition
For each request:
1. Analyze what the user wants to achieve
2. Break it down into numbered sub-steps
3. Identify which tool each sub-step requires
4. Execute sub-steps in order, using results from earlier steps

## Available Tools (from Action and Query agents)
Read tools: course_lookup, schedule_query, syllabus_retrieve
Write tools: grade_update, enrollment_modify, assignment_create

## Few-Shot Examples

### Example 1: Semester Planning
User: "Plan my next semester avoiding Friday classes"
Decomposition:
1. Query available courses → schedule_query for each
2. Filter out courses with Friday meetings
3. Check prerequisites for remaining courses → course_lookup
4. Propose a balanced schedule

### Example 2: Prerequisite Analysis
User: "What do I need before I can take CS201?"
Decomposition:
1. Look up CS201 details → course_lookup("CS201")
2. Retrieve prerequisite information → syllabus_retrieve("CS201")
3. Summarize prerequisites and recommendations

### Example 3: Multi-step Enrollment
User: "Drop CS101 and enroll me in CS201 instead"
Decomposition:
1. Look up CS201 to verify it exists → course_lookup("CS201")
2. Check CS201 schedule → schedule_query("CS201")
3. Drop CS101 → enrollment_modify(action="drop")
4. Enroll in CS201 → enrollment_modify(action="add")

## Output Format
Respond with a JSON object:
{
  "plan": [
    {
      "step": 1,
      "description": "<what this step does>",
      "tool": "<tool_name>",
      "arguments": { ... }
    },
    ...
  ],
  "reasoning": "<brief explanation of your approach>"
}
"""


# Combined tool registry (Planning Agent can use tools from both agents)
PLANNING_TOOLS = {**QUERY_TOOLS, **ACTION_TOOLS}


# ---------------------------------------------------------------------------
# Agent Execution
# ---------------------------------------------------------------------------


async def run_planning_agent(state: AgentState, llm) -> dict:
    """Execute the Planning Agent: decompose the task and execute sub-steps.

    Returns updated state fields: response, tool_calls.
    """
    messages = [
        SystemMessage(content=PLANNING_AGENT_SYSTEM_PROMPT),
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

    plan = result.get("plan", [])
    reasoning = result.get("reasoning", "")

    if not plan:
        return {
            "response": reasoning or "I couldn't create a plan for this request.",
            "tool_calls": [],
        }

    # Execute each step in the plan
    all_tool_calls = []
    step_results = []

    for step in plan:
        tool_name = step.get("tool", "")
        arguments = step.get("arguments", {})
        description = step.get("description", "")

        tool_func = PLANNING_TOOLS.get(tool_name)
        if not tool_func:
            tc = ToolCall(tool_name=tool_name, arguments=arguments,
                          success=False, error=f"Unknown tool: {tool_name}")
            all_tool_calls.append(tc)
            step_results.append(f"Step {step.get('step', '?')}: {description} — FAILED (unknown tool)")
            continue

        try:
            tool_result = tool_func.invoke(arguments)
            tc = ToolCall(tool_name=tool_name, arguments=arguments,
                          result=tool_result, success=True)
            all_tool_calls.append(tc)
            step_results.append(
                f"Step {step.get('step', '?')}: {description} — OK"
            )
        except Exception as e:
            tc = ToolCall(tool_name=tool_name, arguments=arguments,
                          success=False, error=str(e))
            all_tool_calls.append(tc)
            step_results.append(
                f"Step {step.get('step', '?')}: {description} — FAILED ({e})"
            )

    # Format response
    response_parts = [f"**Plan:** {reasoning}", ""]
    response_parts.extend(step_results)

    return {
        "response": "\n".join(response_parts),
        "tool_calls": all_tool_calls,
    }
