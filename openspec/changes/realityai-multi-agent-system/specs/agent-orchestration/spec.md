## ADDED Requirements

### Requirement: LangGraph state machine with defined states
The system SHALL implement a LangGraph state machine with the following sequential states: intent_classification → agent_routing → safety_check → execution → response_generation. The state schema SHALL be defined as an AgentState TypedDict.

#### Scenario: State machine processes a user request through all states
- **WHEN** a user request enters the agent core
- **THEN** the state machine transitions through intent_classification, agent_routing, safety_check, execution, and response_generation in order

#### Scenario: State is persisted between transitions
- **WHEN** a state transition occurs
- **THEN** the AgentState TypedDict is updated with the current state data including intent, selected agent, safety result, and response

### Requirement: LLM-based intent classifier routes to correct agent
The system SHALL classify user intent using an LLM-based classifier that routes requests to one of three agents: action (write operations), query (read operations/tutoring), or planning (multi-step reasoning).

#### Scenario: Write operation routes to Action Agent
- **WHEN** a user submits "Change my enrollment to CS201"
- **THEN** the intent classifier routes the request to the Action Agent

#### Scenario: Read operation routes to Query Agent
- **WHEN** a user submits "What time does CS101 meet?"
- **THEN** the intent classifier routes the request to the Query Agent

#### Scenario: Multi-step reasoning routes to Planning Agent
- **WHEN** a user submits "Plan my next semester avoiding Friday classes"
- **THEN** the intent classifier routes the request to the Planning Agent

### Requirement: State machine supports HiTL interrupt and resume
The system SHALL support interrupting the state machine at the safety_check node when an operation is flagged, persisting state, and resuming execution upon instructor approval or rejection.

#### Scenario: Flagged operation interrupts the state machine
- **WHEN** the safety_check node flags an operation as high-risk
- **THEN** the state machine interrupts execution, persists the current state, and pushes an approval request to the instructor

#### Scenario: Approved operation resumes execution
- **WHEN** an instructor approves a flagged operation
- **THEN** the state machine resumes from the execution state with the original context

#### Scenario: Rejected operation returns denial
- **WHEN** an instructor rejects a flagged operation
- **THEN** the state machine transitions to response_generation with a denial message
