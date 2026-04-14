## Context

This is a greenfield project building an AI-powered course management assistant for Higher Education. The system coordinates three specialized agents (Action, Query, Planning) through a LangGraph state machine, with two-layer safety controls and hybrid retrieval. The tech stack spans Python (FastAPI + LangGraph), Java (Spring Boot + gRPC), React/TypeScript (frontend), and three data stores (PostgreSQL, ChromaDB, Redis).

There is no existing codebase. The repository structure follows a polyglot microservice pattern with four top-level service directories: `agent-core/`, `api-gateway/`, `core-service/`, and `web-dashboard/`, plus shared `proto/` definitions.

Key constraints:
- SSH remote: `git@github.com:CbyChloe/realityai-agents.git` (do NOT switch to HTTPS)
- `gh` CLI is authenticated and available for PR creation (do NOT run `gh auth login`)
- Branch strategy: all feature PRs target `main` directly (no `dev` integration branch)
- PR workflow: `git checkout -b <branch>` → commit → `git push origin <branch>` → `gh pr create --base main`
- PRs follow conventional commit naming: `type(scope): description`
- PR descriptions use ## What, ## Why, ## How, ## Testing sections
- After each phase milestone, tag the release (e.g., `git tag v0.1.0 && git push origin v0.1.0`)
- 31 PRs across 5 phases, each reviewable in under 30 minutes (~400 lines of logic max)

## Goals / Non-Goals

**Goals:**
- Three agents running with correct behavioral decomposition (action/query/planning) and LangGraph state machine routing
- Two-layer safety system with zero false negatives for known high-risk tools and LLM-based anomaly detection for novel patterns
- Hybrid retrieval achieving ~75% context precision (up from ~50% vector-only baseline)
- Dual caching reducing LLM calls: prompt caching for static prefixes, response caching for deterministic queries
- Full-stack integration: FastAPI ↔ LangGraph ↔ gRPC ↔ Spring Boot, with SSE real-time push
- HiTL approval flow working end-to-end: agent interrupt → SSE push → instructor approve/reject → agent resume
- One-click Docker Compose startup and CI/CD via GitHub Actions
- Portfolio-ready with measurable metrics and comprehensive README

**Non-Goals:**
- Production-scale deployment or load testing (this is a portfolio/demo system)
- Real university data or integration with actual LMS systems (Canvas, Blackboard)
- Multi-tenant architecture or user management beyond JWT auth
- Mobile-native clients (web-responsive only)
- Fine-tuning or custom model training
- Real-time collaboration between multiple instructors

## Decisions

### 1. Agent decomposition by behavioral pattern, not domain

**Decision**: Three agents split by behavior — Action (writes), Query (reads/RAG), Planning (multi-step) — rather than by domain (enrollment, scheduling, tutoring).

**Rationale**: Domain-split agents would share 80% of tools and logic. Behavioral split creates genuinely different agents: Action has strict tool-call authorization, Query has caching and RAG, Planning has chain-of-thought decomposition. Each requires different prompt engineering, tool schemas, and execution patterns.

**Alternative considered**: Domain-based agents — rejected because of excessive tool/logic overlap and inconsistent safety profiles within each domain agent.

### 2. LangGraph for orchestration

**Decision**: Use LangGraph state machine with explicit states: intent_classification → agent_routing → safety_check → execution → response_generation.

**Rationale**: LangGraph provides first-class support for HiTL interrupt/resume, state persistence, and conditional routing. The state machine model maps directly to the request lifecycle and makes the safety checkpoint an explicit, non-bypassable node.

**Alternative considered**: Custom orchestration with plain LangChain — rejected because HiTL interrupt/resume and state persistence would require significant custom code.

### 3. Two-layer safety running in parallel

**Decision**: Static risk classifier and dynamic LLM intent analyzer run in parallel. Either layer flagging triggers HiTL review. Merge policy is OR (conservative).

**Rationale**: Static rules are fast (<1ms) and have zero false negatives for known tool types. Dynamic analyzer catches novel abuse patterns (bulk operations, privilege escalation) that rules can't express. Parallel execution means neither is a bottleneck. Conservative merge (OR) accepts false positives (instructor approves quickly) but prevents false negatives.

**Alternative considered**: Sequential (static first, dynamic only if static passes) — rejected because it adds latency on the critical path for high-risk operations.

### 4. gRPC + Protocol Buffers for Python-Java communication

**Decision**: Agent core communicates with Spring Boot via gRPC with shared .proto definitions.

**Rationale**: Proto contracts serve as both API schema and compile-time type checker. A breaking change in the Java service is caught at build time in the Python client, not at runtime. This prevents the most common class of polyglot integration bugs.

**Alternative considered**: REST + OpenAPI — rejected because runtime-only validation in a polyglot setup leads to subtle integration bugs caught late.

### 5. Reciprocal Rank Fusion for hybrid retrieval

**Decision**: Merge ChromaDB vector results and PostgreSQL keyword results using RRF: `score = sum(1 / (k + rank))` from each source.

**Rationale**: RRF doesn't require score normalization between heterogeneous sources (vector similarity scores and SQL relevance scores are on different scales). Simple, well-understood, and effective.

**Alternative considered**: Weighted linear combination — rejected because it requires calibrating score scales between vector and keyword sources.

### 6. Cache check before safety check

**Decision**: Response cache is checked before the safety layer. Cache hits skip both LLM and safety.

**Rationale**: Response cache only stores Query Agent deterministic results (course info, schedules). These are inherently low-risk — there's no safety concern with returning cached factual data. Skipping both LLM and safety for cache hits maximizes latency and cost savings.

### 7. SSE for real-time push (not WebSocket)

**Decision**: Use Server-Sent Events for real-time notifications (agent actions, approval requests, streaming responses).

**Rationale**: SSE is simpler than WebSocket for server-to-client push, works over standard HTTP, and is sufficient since the client-to-server path uses REST. No need for bidirectional streaming.

**Alternative considered**: WebSocket — rejected as over-engineered for unidirectional server push.

## Risks / Trade-offs

- **Prompt engineering iteration is manual and time-consuming** → Mitigate by starting with simple prompts, measuring with evaluation harness, and iterating based on metrics rather than intuition.

- **Hybrid retrieval tuning requires manual annotation** → Mitigate by seeding 100 annotated query-document pairs early (Phase 2) and using them as ground truth for precision measurement.

- **gRPC + protobuf compilation across Python and Java adds build complexity** → Mitigate by having a FastAPI mock service as fallback if Java setup takes too long; swap to Spring Boot later using the same proto interface.

- **LLM-based intent analyzer adds 200-500ms latency** → Acceptable because it only runs on cache misses and runs in parallel with static rules. False positives (extra HiTL reviews) are preferred over false negatives (missed abuse).

- **ChromaDB may not scale for production workloads** → Acceptable for portfolio scope. Document as a known limitation; note Pinecone/Weaviate as production alternatives.

- **Redis cache invalidation on course updates requires event propagation** → Mitigate with TTL-based expiration as baseline; event-driven invalidation as optimization. Stale cache for up to 1 hour is acceptable for course metadata.

## Migration Plan

Not applicable — greenfield project. Deployment follows a phased approach:

1. **Phase 1-2**: Local development only, CLI test harness
2. **Phase 3**: Docker Compose for multi-service local stack
3. **Phase 4**: Full-stack with frontend
4. **Phase 5**: Docker Compose one-click startup, optional AWS deployment

Rollback strategy: Each phase is tagged (v0.1.0 through v1.0.0). Rolling back means reverting to a previous tag on `main`.

## Open Questions

- **LLM provider lock-in**: Design currently assumes Anthropic for prompt caching (cache_control breakpoints). Should we abstract the LLM provider interface for portability? Decision: Start with Anthropic, abstract only if needed.
- **ChromaDB persistence strategy**: Embedded vs client-server mode for local development? Decision: Start embedded, switch to client-server in Docker Compose.
- **Evaluation dataset quality**: 100 annotated pairs may be insufficient for robust precision measurement. Monitor during Phase 2 and expand if metrics are noisy.
