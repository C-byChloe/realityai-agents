## ADDED Requirements

### Requirement: Student chat UI with streaming responses
The web dashboard SHALL provide a student-facing chat interface that displays streaming LLM responses via SSE, maintains conversation history, and shows typing indicators.

#### Scenario: Student sends a message and receives streaming response
- **WHEN** a student types a message and submits it
- **THEN** the chat UI streams the agent's response in real-time via SSE with a typing indicator

#### Scenario: Conversation history is maintained
- **WHEN** a student returns to the chat interface
- **THEN** the previous conversation history is displayed

### Requirement: Instructor approval dashboard
The web dashboard SHALL provide an instructor-facing approval queue showing pending HiTL requests with full agent context (what was attempted, why it was flagged). Approve and reject buttons SHALL be wired to the approval endpoint.

#### Scenario: Instructor sees pending approval request
- **WHEN** an agent operation is flagged for HiTL review
- **THEN** a new entry appears in the instructor's approval queue via SSE with the operation details and flagging reason

#### Scenario: Instructor approves a request
- **WHEN** an instructor clicks "Approve" on a pending request
- **THEN** the approval is sent to POST /approval/{request_id} and the queue entry is updated to show "Approved"

#### Scenario: Instructor rejects a request
- **WHEN** an instructor clicks "Reject" on a pending request
- **THEN** the rejection is sent to POST /approval/{request_id} and the queue entry is updated to show "Rejected"

### Requirement: Admin panel with metrics and traces
The web dashboard SHALL provide an admin panel displaying cache hit rate, agent success rate, latency P50/P95, and a LangSmith trace viewer.

#### Scenario: Admin views system metrics
- **WHEN** an admin navigates to the admin panel
- **THEN** current cache hit rate, agent success rate, and latency P50/P95 are displayed

### Requirement: React + TypeScript + Tailwind tech stack
The web dashboard SHALL be built with React, TypeScript, and Tailwind CSS using Vite as the build tool. It SHALL be mobile-responsive.

#### Scenario: Dashboard is accessible on mobile
- **WHEN** a user accesses the dashboard from a mobile device
- **THEN** the layout adapts responsively to the smaller screen
