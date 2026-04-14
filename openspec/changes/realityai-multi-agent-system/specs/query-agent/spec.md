## ADDED Requirements

### Requirement: Query Agent handles all read operations and tutoring
The Query Agent SHALL handle all read operations including course information lookups, schedule queries, and Q&A tutoring. It SHALL use the hybrid retrieval pipeline for document-backed answers.

#### Scenario: Course information lookup
- **WHEN** a user asks "What time does CS101 meet?"
- **THEN** the Query Agent retrieves the answer using hybrid retrieval and returns the course schedule

#### Scenario: Tutoring question
- **WHEN** a user asks "Help me understand recursion"
- **THEN** the Query Agent generates an educational response using retrieved course materials as context

### Requirement: Query Agent responses are eligible for response caching
Deterministic Query Agent responses (course info, schedules, instructor info) SHALL be eligible for response caching. Tutoring and context-dependent responses SHALL NOT be cached.

#### Scenario: Deterministic query is cached
- **WHEN** a user asks "Who teaches MATH201?" and no cache entry exists
- **THEN** the response is stored in the response cache with the appropriate TTL

#### Scenario: Tutoring response is not cached
- **WHEN** a user asks "Explain the difference between stacks and queues"
- **THEN** the response is NOT stored in the response cache

### Requirement: Query Agent uses hybrid retrieval pipeline
The Query Agent SHALL use the hybrid retrieval pipeline (ChromaDB + PostgreSQL with RRF fusion) for all document-based lookups instead of simple vector-only search.

#### Scenario: Query uses both vector and keyword search
- **WHEN** the Query Agent processes a document-backed query
- **THEN** it retrieves results from both ChromaDB vector search and PostgreSQL keyword filter, merging them via RRF
