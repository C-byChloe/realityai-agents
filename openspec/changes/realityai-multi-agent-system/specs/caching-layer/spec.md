## ADDED Requirements

### Requirement: Prompt caching for static system prompt prefixes
The system SHALL cache static system prompt prefixes per agent, including agent identity, behavioral constraints, tool definitions, output format instructions, and few-shot examples. Implementation SHALL use Anthropic's native prompt caching (cache_control breakpoints).

#### Scenario: Static prefix is cached across requests
- **WHEN** multiple requests are processed by the same agent type
- **THEN** the static system prompt prefix is served from cache, reducing token usage

#### Scenario: Dynamic context is not cached
- **WHEN** a request includes user-specific context (enrollment data, conversation history, retrieved documents)
- **THEN** only the dynamic portion is sent as new tokens; the static prefix remains cached

### Requirement: Redis-backed response cache for deterministic queries
The system SHALL maintain a Redis-backed KV cache for deterministic Query Agent responses. Cache key SHALL be `hash(normalized_query + course_ids + semester)`. Cache value SHALL include response text, source references, and timestamp. Default TTL SHALL be 1 hour.

#### Scenario: Cache hit returns stored response
- **WHEN** a user asks a question that matches an existing cache entry within TTL
- **THEN** the cached response is returned without invoking the LLM or safety layer

#### Scenario: Cache miss proceeds to normal pipeline
- **WHEN** a user asks a question with no matching cache entry
- **THEN** the request proceeds through the normal agent pipeline and the response is stored in cache

#### Scenario: Non-deterministic queries are not cached
- **WHEN** a tutoring or planning response is generated
- **THEN** the response is NOT stored in the response cache

### Requirement: TTL-based cache expiration
Response cache entries SHALL expire after the configured TTL (default 1 hour). Expired entries SHALL be treated as cache misses.

#### Scenario: Expired cache entry triggers cache miss
- **WHEN** a cached entry's TTL has elapsed
- **THEN** the next request for that query proceeds through the normal pipeline

### Requirement: Event-driven cache invalidation on course updates
The system SHALL invalidate all cached responses for a course_id when a course update is received via gRPC. A manual flush endpoint SHALL be available for admin use.

#### Scenario: Course update invalidates related cache entries
- **WHEN** a course update for CS101 is processed via gRPC
- **THEN** all response cache entries with course_id=CS101 are invalidated

#### Scenario: Admin manual cache flush
- **WHEN** an admin calls the manual flush endpoint
- **THEN** all response cache entries are cleared
