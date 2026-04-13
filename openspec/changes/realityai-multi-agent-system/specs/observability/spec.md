## ADDED Requirements

### Requirement: LangSmith tracing across all agent chains
The system SHALL integrate LangSmith tracing with callback handlers across all agent execution chains. Trace spans SHALL cover each state machine node: intent classification, agent routing, safety check, execution, and response generation.

#### Scenario: Full request trace is captured
- **WHEN** a user request is processed through the agent pipeline
- **THEN** LangSmith captures a complete trace with spans for each state machine node, including latency per step

#### Scenario: Token usage is logged per request
- **WHEN** an LLM call is made during agent execution
- **THEN** LangSmith records the input tokens, output tokens, and cached tokens for that call

### Requirement: Non-blocking tracing
LangSmith tracing SHALL be non-blocking. Tracing failures SHALL NOT affect the request processing path.

#### Scenario: Tracing failure does not impact request
- **WHEN** the LangSmith service is unavailable
- **THEN** the agent request is processed normally and the tracing failure is logged locally

### Requirement: Tool call success/failure logging
The system SHALL log tool call outcomes (success, failure, error details) to LangSmith for each agent execution.

#### Scenario: Failed tool call is logged
- **WHEN** a gRPC tool call fails
- **THEN** LangSmith records the tool name, error type, and error message in the trace
