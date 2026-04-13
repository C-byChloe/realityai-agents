## ADDED Requirements

### Requirement: FastAPI gateway as single entry point
The system SHALL expose a FastAPI gateway on port 8000 as the single entry point for all client requests. The gateway SHALL route requests to the agent core.

#### Scenario: Chat request is routed to agent core
- **WHEN** a client sends POST /chat with a user message
- **THEN** the gateway routes the request to the LangGraph agent core and returns the response

### Requirement: JWT authentication
The gateway SHALL authenticate all requests using JWT tokens. Unauthenticated requests SHALL be rejected with HTTP 401.

#### Scenario: Valid JWT is accepted
- **WHEN** a request includes a valid JWT token in the Authorization header
- **THEN** the request is processed and the user context is extracted from the token

#### Scenario: Missing or invalid JWT is rejected
- **WHEN** a request has no JWT or an expired/invalid JWT
- **THEN** the gateway returns HTTP 401 Unauthorized

### Requirement: Per-user rate limiting
The gateway SHALL enforce per-user rate limiting to prevent abuse. Rate limits SHALL be configurable.

#### Scenario: Rate limit exceeded
- **WHEN** a user exceeds the configured rate limit
- **THEN** the gateway returns HTTP 429 Too Many Requests

### Requirement: SSE endpoint for real-time notifications
The gateway SHALL provide an SSE (Server-Sent Events) endpoint for real-time push notifications. Event types SHALL include: agent_action, approval_request, approval_result, and escalation.

#### Scenario: Instructor receives approval request via SSE
- **WHEN** an agent operation is flagged for HiTL review
- **THEN** an approval_request event is pushed to the instructor's SSE stream

#### Scenario: Student receives streaming response via SSE
- **WHEN** the agent generates a response
- **THEN** the response is streamed to the student's SSE connection

### Requirement: HiTL approval endpoint
The gateway SHALL provide a POST /approval/{request_id} endpoint for instructors to approve or reject flagged operations.

#### Scenario: Instructor approves a flagged operation
- **WHEN** an instructor sends POST /approval/{request_id} with action=approve
- **THEN** the agent state machine resumes execution and the result is pushed via SSE to both instructor and student

#### Scenario: Instructor rejects a flagged operation
- **WHEN** an instructor sends POST /approval/{request_id} with action=reject
- **THEN** the agent returns a denial message and the result is pushed via SSE

### Requirement: Health check endpoint
The gateway SHALL provide a GET /health endpoint that returns the status of all dependent services.

#### Scenario: All services healthy
- **WHEN** GET /health is called and all services are reachable
- **THEN** the endpoint returns HTTP 200 with status of each service
