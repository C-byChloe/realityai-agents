## ADDED Requirements

### Requirement: Retry with simplified prompt on first failure
The system SHALL retry agent execution with a simplified prompt (strip few-shot examples) on the first failure. Failure types include: timeout (>30s), LLM refusal, hallucination (references to entities not in retrieval context), and tool call failure (gRPC error).

#### Scenario: First failure triggers retry with simplified prompt
- **WHEN** the agent fails on the first attempt (timeout, refusal, or tool error)
- **THEN** the system retries with a simplified prompt that removes few-shot examples

### Requirement: Instructor escalation on consecutive failures
The system SHALL escalate to the instructor on the second consecutive failure (configurable, default=2). The escalation SHALL include the original user query, agent type, tools attempted, error details, and conversation context.

#### Scenario: Second failure triggers escalation
- **WHEN** the agent fails on the retry attempt
- **THEN** an escalation ticket is created and pushed to the instructor dashboard via SSE

#### Scenario: User sees fallback message
- **WHEN** the system escalates to the instructor
- **THEN** the user receives the message "I've forwarded your request to [instructor name]"

### Requirement: Failure detection covers multiple failure modes
The system SHALL detect the following failure modes: timeout (agent does not respond within 30s), LLM refusal (response contains refusal patterns), hallucination guard (response references courses/students not in retrieval context), and tool call failure (gRPC returns error).

#### Scenario: Timeout detection
- **WHEN** the agent does not respond within 30 seconds
- **THEN** the system classifies this as a timeout failure

#### Scenario: Hallucination detection
- **WHEN** the agent response references a course_id not present in the retrieval context
- **THEN** the system classifies this as a hallucination failure
