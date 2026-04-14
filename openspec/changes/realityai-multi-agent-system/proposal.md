## Why

Higher Education course management needs an AI assistant that is both autonomous and safe. Students need instant answers to course queries and intelligent planning support, but write operations (grade changes, enrollment modifications) require instructor oversight. No existing solution provides multi-agent behavioral decomposition with two-layer safety controls, hybrid retrieval, and production-grade cost optimization in a single system.

## What Changes

- Introduce a **multi-agent orchestration system** powered by LangGraph, with three specialized agents split by behavioral pattern: Action (writes), Query (reads/RAG), and Planning (multi-step reasoning)
- Implement a **two-layer AI safety system**: static rule-based risk classifier + dynamic LLM-based intent analyzer, with Human-in-the-Loop approval for flagged operations
- Build a **hybrid retrieval pipeline**: ChromaDB vector search + PostgreSQL keyword filtering, merged via Reciprocal Rank Fusion (RRF)
- Add **dual caching layers**: prompt caching for static system prefixes and Redis-backed response caching for deterministic queries
- Create a **FastAPI API gateway** with JWT auth, rate limiting, and SSE real-time notifications
- Build a **Spring Boot gRPC core service** for course/student/assignment CRUD with Protocol Buffer contracts
- Develop a **React + TypeScript frontend dashboard** with student chat UI, instructor approval queue, and admin metrics panel
- Implement **graceful degradation** with retry logic and automatic instructor escalation on consecutive failures
- Set up **LangSmith observability** for end-to-end tracing, token usage, and latency profiling
- Provide **Docker Compose** one-click startup and **GitHub Actions** CI/CD pipeline

## Capabilities

### New Capabilities
- `agent-orchestration`: LangGraph state machine with intent classification, agent routing (action/query/planning), and state transitions
- `action-agent`: Write operations agent with tools for grade_update, enrollment_modify, assignment_create via gRPC to Spring Boot
- `query-agent`: Read operations and tutoring agent with hybrid RAG retrieval and response caching eligibility
- `planning-agent`: Multi-step reasoning agent with task decomposition that can invoke Action/Query tools
- `safety-system`: Two-layer safety with static risk classifier, dynamic LLM intent analyzer, merge logic, and HiTL interrupt/resume lifecycle
- `hybrid-retrieval`: ChromaDB vector search + PostgreSQL keyword filtering with RRF fusion and metadata filtering
- `caching-layer`: Prompt caching for static prefixes and Redis-backed response cache with TTL and event-driven invalidation
- `api-gateway`: FastAPI gateway with JWT authentication, per-user rate limiting, request routing, and SSE endpoints
- `core-service`: Spring Boot gRPC service with JPA entities, Protocol Buffer schemas, and PostgreSQL persistence
- `web-dashboard`: React + TypeScript + Tailwind frontend with student chat, instructor approval queue, and admin panel
- `graceful-degradation`: Failure detection, retry with simplified prompts, and automatic instructor escalation
- `observability`: LangSmith tracing integration across all agent chains with non-blocking telemetry
- `devops-infra`: Docker Compose multi-service orchestration and GitHub Actions CI/CD pipeline

### Modified Capabilities
<!-- No existing capabilities to modify - this is a greenfield project -->

## Impact

- **New services**: agent-core (Python), api-gateway (Python/FastAPI), core-service (Java/Spring Boot), web-dashboard (React/TypeScript)
- **New data stores**: PostgreSQL (relational metadata + keywords), ChromaDB (vector embeddings), Redis (response cache + session state)
- **Communication protocols**: REST + SSE (client-facing), gRPC + Protocol Buffers (inter-service), HTTPS async (LangSmith)
- **Dependencies**: LangChain/LangGraph, Anthropic SDK, ChromaDB, Redis, Spring Boot, gRPC/protobuf, React + Tailwind
- **Infrastructure**: Docker Compose for local dev, GitHub Actions for CI/CD, optional AWS EC2/EKS for production
- **API surface**: POST /chat, POST /approval/{request_id}, GET /sse/stream, gRPC course/student/assignment services
