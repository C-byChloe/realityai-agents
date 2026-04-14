## ADDED Requirements

### Requirement: Action Agent handles all write operations
The Action Agent SHALL handle all mutation operations including grade updates, enrollment modifications, and assignment creation. It SHALL have a system prompt defining its identity, behavioral constraints, and available tools.

#### Scenario: Grade update via Action Agent
- **WHEN** a user requests a grade change
- **THEN** the Action Agent invokes the grade_update tool with the appropriate parameters

#### Scenario: Enrollment modification via Action Agent
- **WHEN** a user requests to add or drop a course
- **THEN** the Action Agent invokes the enrollment_modify tool with the course and student details

### Requirement: Action Agent tools call Spring Boot via gRPC
The Action Agent's tools SHALL call the Spring Boot core service via gRPC using Protocol Buffer contracts. Initially, tools SHALL use mock implementations (print + return success) that are replaced with real gRPC calls in Phase 3.

#### Scenario: Tool calls Spring Boot gRPC service
- **WHEN** the Action Agent invokes a tool (e.g., grade_update)
- **THEN** the tool sends a gRPC request to the Spring Boot core service and returns the response

#### Scenario: Mock tool returns success during Phase 1
- **WHEN** the Action Agent invokes a tool before gRPC integration
- **THEN** the mock tool prints the operation details and returns a success response

### Requirement: All Action Agent operations pass through safety layer
Every tool call from the Action Agent SHALL pass through the two-layer safety system before execution. No Action Agent operation SHALL bypass safety checks.

#### Scenario: Action Agent tool call triggers safety check
- **WHEN** the Action Agent prepares a tool call
- **THEN** the tool call is evaluated by both the static risk classifier and the dynamic intent analyzer before execution
