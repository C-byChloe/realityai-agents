"""CLI test harness for manual testing of the multi-agent system.

Usage:
    python cli.py "What time does CS101 meet?"
    python cli.py  # interactive conversation loop
"""

import asyncio
import json
import sys

from langchain_core.messages import HumanMessage

from orchestrator import create_app


def _format_output(result: dict) -> str:
    """Format the agent result for terminal display."""
    lines = []

    # Header
    intent = result.get("intent", "unknown")
    confidence = result.get("intent_confidence", 0.0)
    agent = result.get("selected_agent", "unknown")
    lines.append(f"\n{'='*60}")
    lines.append(f"  Intent:  {intent} (confidence: {confidence:.2f})")
    lines.append(f"  Agent:   {agent}")

    # Safety
    safety = result.get("safety_result")
    if safety:
        status = "FLAGGED" if safety.flagged else "SAFE"
        lines.append(f"  Safety:  {status}")
        if safety.flagged and safety.reason:
            lines.append(f"  Reason:  {safety.reason}")

    # Tool calls
    tool_calls = result.get("tool_calls", [])
    if tool_calls:
        lines.append(f"\n  Tools Called ({len(tool_calls)}):")
        for tc in tool_calls:
            status = "OK" if tc.success else f"FAILED: {tc.error}"
            lines.append(f"    - {tc.tool_name}({json.dumps(tc.arguments)}) → {status}")

    # Response
    lines.append(f"\n  Response:")
    for line in result.get("response", "").split("\n"):
        lines.append(f"    {line}")

    lines.append(f"{'='*60}\n")
    return "\n".join(lines)


async def run_single(query: str) -> None:
    """Run a single query through the agent system."""
    app = create_app()
    state = {
        "messages": [HumanMessage(content=query)],
        "intent": "",
        "intent_confidence": 0.0,
        "selected_agent": "",
        "safety_result": None,
        "tool_calls": [],
        "response": "",
        "user_id": "cli-user",
        "session_id": "cli-session",
        "requires_approval": False,
        "approval_status": None,
    }
    result = await app.ainvoke(state)
    print(_format_output(result))


async def run_interactive() -> None:
    """Run an interactive conversation loop."""
    app = create_app()
    print("\nRealityAI Agent System — Interactive Mode")
    print("Type 'quit' or 'exit' to stop.\n")

    while True:
        try:
            query = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not query:
            continue
        if query.lower() in ("quit", "exit"):
            print("Goodbye!")
            break

        state = {
            "messages": [HumanMessage(content=query)],
            "intent": "",
            "intent_confidence": 0.0,
            "selected_agent": "",
            "safety_result": None,
            "tool_calls": [],
            "response": "",
            "user_id": "cli-user",
            "session_id": "cli-session",
            "requires_approval": False,
            "approval_status": None,
        }
        result = await app.ainvoke(state)
        print(_format_output(result))


def main():
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        asyncio.run(run_single(query))
    else:
        asyncio.run(run_interactive())


if __name__ == "__main__":
    main()
