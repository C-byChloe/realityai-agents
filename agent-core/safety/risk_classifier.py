"""Static risk classifier — maps tools to risk levels.

Fast (<1ms) dictionary-based classification. Unknown tools default to high-risk.
"""

from dataclasses import dataclass


@dataclass
class RiskResult:
    """Result from the static risk classifier."""

    risk_level: str  # "high" or "low"
    reason: str | None = None


# Tool → risk level mapping
# Write operations are high-risk; read operations are low-risk.
RISK_MAP: dict[str, str] = {
    # Action Agent tools (write operations) → high
    "grade_update": "high",
    "enrollment_modify": "high",
    "assignment_create": "high",
    # Query Agent tools (read operations) → low
    "course_lookup": "low",
    "schedule_query": "low",
    "syllabus_retrieve": "low",
}


def classify_risk(tool_name: str) -> RiskResult:
    """Classify a tool's risk level. Unknown tools default to high.

    This function uses a simple dictionary lookup and completes in <1ms.
    """
    risk_level = RISK_MAP.get(tool_name)

    if risk_level is None:
        return RiskResult(
            risk_level="high",
            reason=f"Unknown tool '{tool_name}' defaults to high-risk",
        )

    if risk_level == "high":
        return RiskResult(
            risk_level="high",
            reason=f"Tool '{tool_name}' is classified as high-risk",
        )

    return RiskResult(risk_level="low", reason=None)
