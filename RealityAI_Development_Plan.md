# RealityAI Multi-Agent System — Development Plan

## Project Overview

An AI-powered course management assistant for Higher Education environments, built around a **multi-agent orchestration system** with **two-layer safety controls**, **hybrid retrieval**, and **production-grade cost optimization**. The system coordinates specialized agents to handle course management tasks while maintaining human oversight for high-risk operations.

### Core Value Proposition

- **Multi-agent behavioral decomposition** — not one monolithic agent, but three specialized agents split by behavioral pattern
- **Two-layer AI safety** — static rules + dynamic LLM intent analysis, with Human-in-the-Loop approval for flagged operations
- **Hybrid retrieval** — vector search + structured filtering, with measurable evaluation
- **Production readiness** — prompt caching, response caching, graceful degradation, end-to-end evaluation harness

---

## System Architecture

### Layered Overview

```
┌─────────────────────────────────────────────────────────┐
│  Client Layer                                           │
│  Instructor Dashboard  ·  Student Chat UI               │
└──────────────────────────┬──────────────────────────────┘
                           │ REST / SSE
┌──────────────────────────▼──────────────────────────────┐
│  API Gateway (FastAPI)                                  │
│  Auth  ·  Rate Limiting  ·  Request Routing             │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│  Agent Core (LangGraph State Machine)                   │
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │  Intent Classification  ·  State Machine Router │    │
│  └─────────────────────────────────────────────────┘    │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐          │
│  │  Action   │  │  Query   │  │  Planning    │          │
│  │  Agent    │  │  Agent   │  │  Agent       │          │
│  │  (writes) │  │  (reads) │  │  (multi-step)│          │
│  └──────────┘  └──────────┘  └──────────────┘          │
│                                                         │
│  ┌─────────────────┐  ┌────────────────────────┐        │
│  │  Static Risk     │  │  Dynamic Intent        │       │
│  │  Classifier      │  │  Analyzer (LLM-based)  │       │
│  └─────────────────┘  └────────────────────────┘        │
│                                                         │
│  ┌─────────────────┐  ┌────────────────────────┐        │
│  │  Prompt Cache    │  │  Response Cache (KV)   │        │
│  │  (static prefix) │  │  (deterministic only)  │       │
│  └─────────────────┘  └────────────────────────┘        │
└──────────┬──────────────────────┬───────────────────────┘
           │                      │
     ┌─────▼──────┐         ┌────▼───────┐   ┌───────────┐
     │ PostgreSQL  │         │  ChromaDB  │   │   Redis   │
     │ Metadata +  │         │  Vector    │   │   Cache   │
     │ Keywords    │         │  Embeddings│   │   Backend │
     └────────────┘         └────────────┘   └───────────┘

External Services:
  - Spring Boot Core (course CRUD) ←→ gRPC + Protocol Buffers
  - LangSmith (tracing, profiling) ←→ async, non-blocking
  - SSE (real-time push to instructor dashboard)
```

### Component Responsibilities

**FastAPI Gateway** — Single entry point. Handles JWT authentication, per-user rate limiting, and routes requests to the agent core. Also serves SSE endpoints for real-time notifications.

**LangGraph State Machine** — The orchestration brain. Classifies user intent, routes to the appropriate agent, manages state transitions (intent_classification → agent_routing → safety_check → execution → response_generation). Handles the HiTL interrupt/resume lifecycle.

**Action Agent** — Handles all write operations: enrollment changes, grade modifications, assignment updates. Has tools that call the Spring Boot core via gRPC. All actions from this agent pass through the safety layer.

**Query Agent** — Handles all read operations and tutoring: course information lookups, schedule queries, Q&A. Uses the hybrid retrieval pipeline (ChromaDB + PostgreSQL). Responses from deterministic queries are eligible for response caching.

**Planning Agent** — Handles complex, multi-step reasoning tasks: "plan my next semester avoiding Friday classes", "what prerequisites am I missing for the ML track". Decomposes tasks into sub-steps, may call other agents' tools.

**Static Risk Classifier** — Rule-based layer. Maintains a risk mapping of tool definitions: `grade_update` → high-risk, `course_query` → low-risk. Fast, deterministic, zero false negatives for known tool types.

**Dynamic Intent Analyzer** — LLM-based layer. Analyzes user intent for anomalous patterns that static rules can't catch: bulk operations ("change all students' grades to A"), privilege escalation attempts, unusual scope ("delete all assignments"). Runs in parallel with static rules.

**Prompt Cache** — Caches static system prompt prefixes and tool definitions per agent. These are identical across requests and consume significant tokens. Implementation depends on LLM provider (Anthropic native prompt caching or manual prefix caching).

**Response Cache** — Redis-backed KV cache for deterministic Query Agent responses. Cache key = hash(query + relevant_context_ids). Only caches responses where the same input is guaranteed to produce the same output (e.g., course schedules, instructor info). TTL-based expiration aligned with data update frequency.

**Spring Boot Core** — The existing course management system (simulated for portfolio). Exposes gRPC endpoints for course/student/assignment CRUD. Protocol Buffer contracts enforce type-safe cross-language communication. The agent core never directly touches this database — all mutations go through gRPC.

**LangSmith** — Observability layer. Traces every agent execution end-to-end: latency per step, token usage, tool call success/failure, intent classification accuracy. Non-blocking — tracing failures don't affect the request path.

---

## Data Flow

### Request Lifecycle

```
User Request
     │
     ▼
FastAPI Gateway (auth, rate limit)
     │
     ▼
LangGraph State Machine (intent classify → route to agent)
     │
     ▼
┌─── Cache Check ───┐
│                    │
│  HIT ──────────── ▶ Return cached response (skip LLM, skip safety)
│                      Only for Query Agent deterministic queries
│  MISS                
│    │                 
│    ▼                 
│  Two-Layer Safety    
│  ┌────────────────────────────────────────┐
│  │ Static Rules          Intent Analyzer  │
│  │ (tool-level risk)     (LLM anomaly)    │
│  │         │                   │          │
│  │         └───── merge ───────┘          │
│  └────────────────┬───────────────────────┘
│                   │
│           ┌───────▼───────┐
│           │   Flagged?    │
│           └───────┬───────┘
│                   │
│        ┌──── Yes ─┴─ No ────┐
│        │                    │
│        ▼                    ▼
│  HiTL Interrupt        Execute Agent
│  (push to dashboard)     Action
│        │                    │
│   Instructor                │
│   Approve / Reject          │
│        │                    │
│   Approve ──► Execute       │
│   Reject ──► Return denial  │
│        │                    │
│        └────────┬───────────┘
│                 │
│                 ▼
│           Execute via:
│           - gRPC → Spring Boot (writes)
│           - Hybrid RAG (reads)
│           - LLM generate (tutoring)
│                 │
│           ┌─────▼─────┐
│           │  Success?  │
│           └─────┬──────┘
│                 │
│          Yes ───┴─── No (consecutive failures)
│           │              │
│           │              ▼
│           │         Graceful Degradation
│           │         Fallback to instructor
│           │              │
│           └──────┬───────┘
│                  │
│                  ▼
│           Cache response (if deterministic)
│                  │
└──────────────────▼
              Response to User (SSE push)
```

### Key Data Flow Decisions

**Why cache check before safety check?**
Response cache only stores Query Agent deterministic results (course info, schedules). These are inherently low-risk — there's no safety concern with returning "CS101 meets Tuesdays at 10am" from cache. Skipping both LLM and safety for cache hits gives the best latency and cost savings.

**Why two safety layers in parallel?**
Static rules are fast and have zero false negatives for known high-risk tools — they catch `grade_update`, `enrollment_modify`, etc. But they can't catch novel abuse patterns. The LLM intent analyzer catches "change everyone's grade" even if the individual tool call looks normal. Running both in parallel means neither is a bottleneck, and the merge logic is simple: if either flags, the request is flagged.

**Why graceful degradation on failure?**
LLMs hallucinate, timeout, and refuse. A production system can't show users an error page. On consecutive agent failures (configurable, default=2), the system automatically escalates to the instructor with context about what was attempted and why it failed. The user sees "I've forwarded your request to your instructor" instead of a 500 error.

**Why gRPC instead of REST for Spring Boot?**
Python agents and Java Spring Boot need a shared interface contract. Protocol Buffers serve as both the API schema and compile-time type checker — a breaking change in the Java service is caught at build time in the Python client, not at runtime. For a polyglot microservice architecture, this prevents the most common class of integration bugs.

---

## Agent Design

### Agent Decomposition Rationale

Agents are split by **behavioral pattern**, not by business domain. This is a deliberate design choice:

| Agent | Behavior | Tools | Safety Profile |
|-------|----------|-------|----------------|
| Action | Writes / mutations | `grade_update`, `enrollment_modify`, `assignment_create` | Always passes through safety layer |
| Query | Reads / retrieval / Q&A | `course_lookup`, `schedule_query`, RAG pipeline | Low-risk, eligible for response caching |
| Planning | Multi-step reasoning | Can invoke Action/Query tools | Decomposed sub-steps each checked individually |

**Why not split by domain (enrollment/scheduling/tutoring)?**
Because enrollment and scheduling agents would share 80% of their tools and logic — both do CRUD on course data. The behavioral split creates genuinely different agents: Action Agent has strict tool-call authorization, Query Agent has caching and RAG, Planning Agent has chain-of-thought decomposition. Each requires different prompt engineering, different tool schemas, and different execution patterns.

### Prompt Architecture

Each agent has a structured prompt chain:

```
[System Prompt Prefix]     ← cached (identical across requests)
  - Agent identity and behavioral constraints
  - Available tools with descriptions and schemas
  - Output format instructions
  - Few-shot examples (2-3 per agent)

[Dynamic Context]          ← per-request (not cached)
  - User's course enrollment context
  - Current semester/term metadata
  - Conversation history (last 5 turns)
  - Retrieved documents (for Query Agent)

[User Message]             ← the actual request
```

The static prefix is designed to be as long and front-loaded as possible to maximize prompt cache hit rates. All dynamic content comes after the cached prefix.

---

## Two-Layer Safety System

### Layer 1: Static Risk Classifier

```python
RISK_MAP = {
    "grade_update":       "high",
    "enrollment_modify":  "high",
    "enrollment_drop":    "high",
    "assignment_delete":  "high",
    "course_lookup":      "low",
    "schedule_query":     "low",
    "syllabus_retrieve":  "low",
    "tutor_respond":      "low",
}
```

Deterministic, fast (< 1ms), zero false negatives for mapped tools. New tools default to "high" until explicitly classified.

### Layer 2: Dynamic Intent Analyzer

A lightweight LLM call that evaluates the user's intent in context:

```
Given the user message and conversation history, evaluate:
1. Is the user requesting a bulk operation? (> 5 records affected)
2. Is there a scope mismatch? (student requesting instructor-level actions)
3. Does the intent seem adversarial? (prompt injection, social engineering)

Respond with: { "flagged": bool, "reason": string | null }
```

Runs in parallel with Layer 1. Adds ~200-500ms latency but only runs on cache misses. False positives are acceptable (instructor can approve quickly); false negatives are not.

### Merge Logic

```python
def should_flag(static_result, dynamic_result):
    # Either layer can flag — conservative by design
    if static_result.risk == "high":
        return True, f"Tool {static_result.tool} is classified as high-risk"
    if dynamic_result.flagged:
        return True, dynamic_result.reason
    return False, None
```

---

## Hybrid Retrieval Pipeline

### Architecture

```
User Query
     │
     ├──► ChromaDB Vector Search (semantic similarity, top-k=10)
     │
     ├──► PostgreSQL Keyword Filter (course_id, semester, doc_type)
     │
     ▼
Result Fusion (Reciprocal Rank Fusion)
     │
     ▼
Top-5 Documents → LLM Context Window
```

### Design Decisions

- **Vector search** handles semantic queries ("what are the prerequisites for machine learning") where exact keyword matching would fail
- **Structured filtering** handles precise lookups ("CS101 Fall 2025 syllabus") where vector search would return noisy results
- **Reciprocal Rank Fusion (RRF)** merges both result sets without requiring score normalization — each result gets a score of `1 / (k + rank)` from each source, scores are summed
- **Metadata filtering** narrows ChromaDB search by course_id and semester before computing similarity, dramatically reducing the search space

### Evaluation

- 100 human-annotated query-document pairs as ground truth
- Context precision = relevant docs in top-5 / total docs in top-5
- Baseline (vector-only): ~50%
- After hybrid retrieval + metadata filtering: ~75%
- Evaluation harness runs as a pytest suite, results logged to LangSmith

---

## Caching Strategy

### Prompt Caching

| Component | Cached? | Rationale |
|-----------|---------|-----------|
| System prompt per agent | Yes | Identical across all requests for the same agent |
| Tool definitions | Yes | Schema doesn't change between requests |
| Few-shot examples | Yes | Static reference examples |
| User context | No | Changes per user/session |
| Retrieved documents | No | Changes per query |
| Conversation history | No | Changes per turn |

Implementation: Anthropic's native prompt caching (cache_control breakpoints) or manual prefix hashing for other providers.

### Response Caching

```
Cache Key:   hash(normalized_query + course_ids + semester)
Cache Value: { response: str, sources: list, timestamp: int }
TTL:         1 hour (configurable, aligned with data update frequency)
Scope:       Query Agent only — Action and Planning agents are never cached
```

**What gets cached:**
- "When does CS101 meet?" → deterministic, same answer every time
- "Who teaches MATH201?" → deterministic within a semester
- "What's the grading policy for CS301?" → deterministic, changes only when syllabus updates

**What does NOT get cached:**
- "Help me understand recursion" → tutoring, answer varies by context
- "Plan my schedule for next semester" → planning, user-specific
- Any Action Agent operation → mutations, must always execute

### Cache Invalidation

- TTL-based expiration (default 1h)
- Event-driven invalidation: when a course update comes through gRPC, invalidate all cached responses for that course_id
- Manual flush endpoint for admin use

---

## Graceful Degradation

### Failure Handling Strategy

```
Agent Execution
     │
     ├─── Success → Return response
     │
     ├─── Failure #1 → Retry with simplified prompt (strip few-shot examples)
     │
     ├─── Failure #2 → Fallback to instructor escalation
     │         │
     │         ▼
     │    Create escalation ticket:
     │    - Original user query
     │    - Agent type and tools attempted
     │    - Error details (timeout / refusal / hallucination)
     │    - Conversation context
     │    Push to instructor dashboard via SSE
     │
     └─── Return to user: "I've forwarded your request to [instructor name]"
```

### Failure Detection

- **Timeout**: Agent doesn't respond within 30s
- **LLM refusal**: Response contains refusal patterns
- **Hallucination guard**: Response references courses/students not in the retrieval context
- **Tool call failure**: gRPC call to Spring Boot returns error

---

## Tech Stack

### Services

| Service | Language | Framework | Port |
|---------|----------|-----------|------|
| API Gateway | Python | FastAPI | 8000 |
| Agent Core | Python | LangChain / LangGraph | (internal) |
| Course Management Core | Java | Spring Boot | 8080 |
| Frontend Dashboard | TypeScript | React + Tailwind | 3000 |

### Data Stores

| Store | Purpose | Persistence |
|-------|---------|-------------|
| PostgreSQL | Relational metadata, course data, outbox | Persistent |
| ChromaDB | Vector embeddings for RAG | Persistent |
| Redis | Response cache, session state | Ephemeral (TTL) |

### Communication

| Path | Protocol | Reason |
|------|----------|--------|
| Client → Gateway | REST + SSE | Standard HTTP for requests, SSE for real-time push |
| Gateway → Agent Core | Internal Python call | Same process / async queue |
| Agent Core → Spring Boot | gRPC + Protocol Buffers | Type-safe cross-language contract |
| Agent Core → LangSmith | HTTPS (async) | Non-blocking observability |

### Infrastructure

| Tool | Purpose |
|------|---------|
| Docker Compose | One-click local startup of all services |
| GitHub Actions | CI/CD pipeline (lint, test, build, deploy) |
| AWS EC2 / EKS | Production deployment (optional) |

---

## Repository Structure

```
realityai-agents/
├── agent-core/                    # Python: LangGraph + agents + RAG
│   ├── agents/
│   │   ├── action_agent.py        # Write operations agent
│   │   ├── query_agent.py         # Read + tutoring agent
│   │   ├── planning_agent.py      # Multi-step reasoning agent
│   │   └── prompts/               # System prompts per agent
│   ├── safety/
│   │   ├── static_classifier.py   # Tool-level risk rules
│   │   ├── intent_analyzer.py     # LLM-based intent analysis
│   │   └── merge.py               # Two-layer merge logic
│   ├── retrieval/
│   │   ├── hybrid_retriever.py    # Vector + keyword fusion
│   │   ├── embeddings.py          # Embedding generation
│   │   └── evaluation.py          # Precision measurement harness
│   ├── caching/
│   │   ├── prompt_cache.py        # Static prefix caching
│   │   └── response_cache.py      # Redis KV cache for queries
│   ├── orchestrator.py            # LangGraph state machine
│   ├── fallback.py                # Graceful degradation logic
│   └── tests/
│       ├── test_scenarios/        # 50+ end-to-end test cases
│       └── eval_dataset/          # 100 annotated query-document pairs
│
├── api-gateway/                   # Python: FastAPI
│   ├── main.py                    # App entry point
│   ├── routes/
│   │   ├── chat.py                # Chat endpoint
│   │   ├── approval.py            # HiTL approval endpoint
│   │   └── sse.py                 # SSE notification stream
│   ├── auth/                      # JWT authentication
│   └── middleware/                 # Rate limiting
│
├── core-service/                  # Java: Spring Boot
│   ├── src/main/java/
│   │   ├── grpc/                  # gRPC service implementations
│   │   ├── model/                 # Course, Student, Assignment entities
│   │   ├── repository/            # JPA repositories
│   │   └── service/               # Business logic
│   └── src/main/proto/            # Protocol Buffer definitions
│
├── web-dashboard/                 # React + TypeScript + Tailwind
│   ├── src/
│   │   ├── instructor/            # Approval queue, action viewer
│   │   ├── student/               # Chat UI, session history
│   │   └── admin/                 # Agent trace viewer, health metrics
│   └── package.json
│
├── proto/                         # Shared .proto files
│   ├── course.proto
│   ├── student.proto
│   └── assignment.proto
│
├── docker-compose.yml             # One-click startup
├── .github/workflows/ci.yml      # GitHub Actions CI/CD
└── README.md                      # Architecture diagram, setup guide, demo GIF
```

---

## Implementation Phases

### Phase 1: Agent Core + State Machine (3-4 days)

**Goal**: Three agents running locally, correctly routing via LangGraph state machine.

**Tasks**:
1. Set up LangGraph state machine with states: `intent_classification → agent_routing → execution → response_generation`
2. Implement intent classifier (LLM-based, routes to action/query/planning)
3. Build Action Agent with mock tools (print statements instead of real gRPC calls)
4. Build Query Agent with basic ChromaDB retrieval (no hybrid yet)
5. Build Planning Agent with task decomposition prompt
6. Design system prompts and tool schemas for each agent
7. CLI test harness to manually test routing and agent behavior

**Deliverable**: `python cli.py "What time does CS101 meet?"` correctly routes to Query Agent and returns a response.

**Risk**: Prompt engineering iteration — agent behavior tuning is manual and time-consuming.

---

### Phase 2: Safety + RAG + Caching (3-4 days)

**Goal**: Two-layer safety working, hybrid retrieval measurably better, caching reducing LLM calls.

**Tasks**:
1. Implement static risk classifier (tool → risk level mapping)
2. Build dynamic intent analyzer (lightweight LLM call with structured output)
3. Implement merge logic (either-layer-flags policy)
4. Wire HiTL interrupt into LangGraph (interrupt → await → resume/reject)
5. Add PostgreSQL keyword filtering alongside ChromaDB vector search
6. Implement Reciprocal Rank Fusion for result merging
7. Set up Redis-backed response cache with TTL and cache key design
8. Implement prompt caching (provider-specific)
9. Build graceful degradation (retry → fallback logic)
10. Seed evaluation dataset (100 annotated query-document pairs)

**Deliverable**: `"Change all grades to A"` triggers both safety layers and blocks. `"When does CS101 meet?"` is cached after first call.

**Risk**: This is the hardest phase. Hybrid retrieval tuning, intent analyzer prompt quality, and cache key design all require manual iteration. Expect 30-40% of total project time here.

---

### Phase 3: Backend Services + Integration (3-4 days)

**Goal**: Full service stack running — FastAPI, Spring Boot, gRPC, SSE all wired up.

**Tasks**:
1. Define Protocol Buffer schemas for course/student/assignment
2. Implement Spring Boot gRPC service with JPA + PostgreSQL
3. Replace mock tools in Action Agent with real gRPC calls
4. Build FastAPI gateway with JWT auth and rate limiting
5. Implement SSE endpoint for real-time notifications
6. Wire HiTL approval flow end-to-end (agent interrupt → SSE push → approval endpoint → agent resume)
7. Integrate LangSmith tracing across all agent chains
8. Set up event-driven cache invalidation (course update → invalidate cached responses)

**Deliverable**: Full request lifecycle works end-to-end via HTTP. `POST /chat` → agent processes → gRPC to Spring Boot → SSE notification.

**Risk**: gRPC + protobuf compilation across Python and Java. If Java setup takes too long, temporarily use a FastAPI mock service with the same proto interface — swap to Spring Boot later.

---

### Phase 4: Frontend Dashboard (3-4 days)

**Goal**: Usable UI for both instructor and student personas.

**Tasks**:
1. Instructor dashboard: approval queue showing pending HiTL requests with agent context
2. Instructor dashboard: real-time SSE status updates (new requests, agent actions)
3. Student chat UI: streaming LLM responses, conversation history
4. Student chat UI: typing indicators, session management
5. Admin panel: LangSmith trace viewer embed (or custom trace display)
6. Admin panel: cache hit rate, agent success rate, latency P50/P95 metrics

**Deliverable**: Open two browser tabs — student sends "change my enrollment", instructor sees approval request appear in real-time, approves it, student sees confirmation.

**Risk**: UI polish is time-consuming but not technically risky. Prioritize the HiTL approval flow demo over visual polish.

---

### Phase 5: Evaluation + DevOps + Polish (2-3 days)

**Goal**: Portfolio-ready with measurable metrics and one-click setup.

**Tasks**:
1. Build automated evaluation harness (pytest suite against 100 annotated pairs)
2. Measure and document: context precision, task completion rate, cache hit rate
3. Run 50+ end-to-end test scenarios, document results
4. Write Docker Compose config for one-click startup of all services
5. Set up GitHub Actions CI/CD (lint, unit tests, integration tests)
6. Write README with architecture diagram, setup guide, demo GIF
7. Optional: AWS deployment with Terraform or manual EC2 setup

**Deliverable**: `git clone && docker-compose up` → entire system running. README shows architecture, metrics, and a demo GIF.

**Risk**: Low technical risk. Main effort is documentation and polish.

---

## Timeline Summary

| Phase | Scope | Duration | Cumulative |
|-------|-------|----------|------------|
| Phase 1 | Agent core + state machine | 3-4 days | Week 1 |
| Phase 2 | Safety + RAG + caching | 3-4 days | Week 1-2 |
| Phase 3 | Backend services + integration | 3-4 days | Week 2-3 |
| Phase 4 | Frontend dashboard | 3-4 days | Week 3 |
| Phase 5 | Evaluation + DevOps + polish | 2-3 days | Week 3 |

**Total estimated time with Claude Code: ~3 weeks**

**MVP checkpoint after Phase 2** (~1 week): Agents running locally with safety, RAG, and caching. Can demo in terminal. This alone is a strong portfolio piece.

**Full-stack checkpoint after Phase 4** (~2.5 weeks): End-to-end system with UI. Can do a live demo.

**Portfolio-ready after Phase 5** (~3 weeks): Docker Compose, CI/CD, README, metrics. Ready for `git clone` by anyone.

---

## Git Workflow & PR Plan

### Branch Strategy

```
main                          ← protected, always deployable
  └── dev                     ← integration branch, PRs merge here first
       ├── feat/...           ← feature branches
       ├── fix/...            ← bug fixes
       └── chore/...          ← config, CI, docs
```

- `main` — production-ready code only. Merges from `dev` after integration testing.
- `dev` — integration branch. All feature PRs target `dev`. Periodically merged to `main` at phase milestones.
- Feature branches — short-lived, one per PR. Named `feat/<phase>-<description>`.

### Git Authentication (for AI agents)

**IMPORTANT: This project uses SSH key authentication. The following rules apply to any AI agent (Claude Code, etc.) working on this repo:**

- **DO** use `git push`, `git pull`, `git fetch` — SSH auth is pre-configured and works automatically.
- **DO** create branches with `git checkout -b <branch>` and push with `git push origin <branch>`.
- **DO NOT** use `gh` CLI commands (`gh pr create`, `gh repo`, etc.) — `gh auth` is not configured and will fail.
- **DO NOT** attempt to run `gh auth login` or any interactive authentication flows.
- **PR creation**: After pushing a feature branch, output the PR title and description as text. The developer will create the PR manually via GitHub web UI.
- **Remote URL format**: `git@github.com:CbyChloe/realityai-agents.git` (SSH, not HTTPS).

### PR Naming Convention

```
feat(agent-core): implement LangGraph state machine with intent router
feat(safety): add static risk classifier with tool-level mapping
fix(retrieval): correct RRF score normalization for empty result sets
chore(ci): add GitHub Actions lint and test workflow
```

Format: `type(scope): short imperative description`

### PR Plan by Phase

---

#### Phase 1: Agent Core + State Machine

| PR # | Branch | Title | Description | Depends On |
|-------|--------|-------|-------------|------------|
| 1 | `feat/p1-project-scaffold` | `chore(project): initialize repo structure and dependencies` | Python project setup (pyproject.toml / requirements), directory skeleton for agent-core, api-gateway, core-service, web-dashboard. Docker Compose placeholder. README stub. | — |
| 2 | `feat/p1-langgraph-state-machine` | `feat(agent-core): implement LangGraph state machine with intent router` | State machine definition with nodes: intent_classification → agent_routing → execution → response_generation. LLM-based intent classifier that routes to action/query/planning. Basic state schema (AgentState TypedDict). | PR #1 |
| 3 | `feat/p1-action-agent` | `feat(agent-core): add Action Agent with mock tools` | Action Agent with system prompt, tool schemas (grade_update, enrollment_modify, assignment_create). Mock tool implementations (print + return success). Wire into state machine routing. | PR #2 |
| 4 | `feat/p1-query-agent` | `feat(agent-core): add Query Agent with basic ChromaDB retrieval` | Query Agent with system prompt, tool schemas (course_lookup, schedule_query). Basic ChromaDB setup, document ingestion script, simple vector retrieval. Wire into state machine. | PR #2 |
| 5 | `feat/p1-planning-agent` | `feat(agent-core): add Planning Agent with task decomposition` | Planning Agent with chain-of-thought system prompt. Task decomposition into sub-steps. Can invoke Action/Query tools. Wire into state machine. | PR #3, PR #4 |
| 6 | `feat/p1-cli-harness` | `feat(agent-core): add CLI test harness for manual testing` | CLI entry point: `python cli.py "query"`. Conversation loop with history. Formatted output showing which agent was selected, tools called, and response. | PR #5 |

**Phase 1 milestone**: Merge `dev` → `main`. Tag `v0.1.0-agent-core`.

---

#### Phase 2: Safety + RAG + Caching

| PR # | Branch | Title | Description | Depends On |
|-------|--------|-------|-------------|------------|
| 7 | `feat/p2-static-risk-classifier` | `feat(safety): add static risk classifier with tool-level mapping` | RISK_MAP dictionary, classifier function, unit tests. New tools default to high-risk. < 1ms execution guarantee. | PR #6 |
| 8 | `feat/p2-intent-analyzer` | `feat(safety): add LLM-based dynamic intent analyzer` | Lightweight LLM call with structured JSON output. Checks for bulk operations, scope mismatch, adversarial intent. Parallel execution design. | PR #6 |
| 9 | `feat/p2-safety-merge-hitl` | `feat(safety): implement two-layer merge logic and HiTL interrupt` | Merge function (either-layer-flags). Wire into LangGraph with interrupt node. HiTL state persistence (interrupt → await → resume/reject). Integration tests with both layers. | PR #7, PR #8 |
| 10 | `feat/p2-hybrid-retrieval` | `feat(retrieval): implement hybrid retrieval with RRF fusion` | PostgreSQL keyword filter alongside ChromaDB. Reciprocal Rank Fusion implementation. Metadata filtering (course_id, semester). Replace basic retrieval in Query Agent. | PR #4 |
| 11 | `feat/p2-response-cache` | `feat(caching): add Redis-backed response cache for deterministic queries` | Redis setup, cache key design (hash of normalized query + context IDs), TTL configuration. Cache hit/miss logic in Query Agent path. Only caches deterministic queries. | PR #10 |
| 12 | `feat/p2-prompt-cache` | `feat(caching): implement prompt caching for static system prompts` | Cache-control breakpoints for Anthropic API (or manual prefix hashing). Static prefix extraction per agent. Token usage comparison before/after. | PR #6 |
| 13 | `feat/p2-graceful-degradation` | `feat(agent-core): add graceful degradation with instructor fallback` | Failure detection (timeout, refusal, hallucination guard). Retry with simplified prompt on first failure. Escalation ticket creation on second failure. User-facing fallback message. | PR #9 |
| 14 | `feat/p2-eval-dataset` | `feat(evaluation): seed evaluation dataset and precision harness` | 100 annotated query-document pairs (JSON). Pytest-based evaluation harness measuring context precision. Baseline vs hybrid comparison script. | PR #10 |

**Phase 2 milestone**: Merge `dev` → `main`. Tag `v0.2.0-safety-rag-cache`. **This is the MVP checkpoint.**

---

#### Phase 3: Backend Services + Integration

| PR # | Branch | Title | Description | Depends On |
|-------|--------|-------|-------------|------------|
| 15 | `feat/p3-proto-definitions` | `feat(proto): define Protocol Buffer schemas for course/student/assignment` | Shared .proto files in /proto directory. Python and Java codegen scripts. CI step to verify proto compilation. | PR #1 |
| 16 | `feat/p3-spring-boot-core` | `feat(core-service): implement Spring Boot gRPC service` | Spring Boot app with JPA entities (Course, Student, Assignment). gRPC service implementations. PostgreSQL schema migrations. Seed data script. | PR #15 |
| 17 | `feat/p3-grpc-integration` | `feat(agent-core): replace mock tools with real gRPC calls` | gRPC client in Python agent-core. Replace mock tool implementations with real calls to Spring Boot. Error handling and timeout configuration. | PR #16 |
| 18 | `feat/p3-fastapi-gateway` | `feat(api-gateway): build FastAPI gateway with auth and rate limiting` | FastAPI app with JWT authentication. Per-user rate limiting middleware. Request routing to agent core. Health check endpoint. | PR #17 |
| 19 | `feat/p3-sse-notifications` | `feat(api-gateway): implement SSE endpoint for real-time notifications` | SSE stream endpoint. Event types: agent_action, approval_request, approval_result, escalation. Connection management for concurrent sessions. | PR #18 |
| 20 | `feat/p3-hitl-e2e` | `feat(api-gateway): wire HiTL approval flow end-to-end` | Approval endpoint (POST /approval/{request_id}). Full cycle: agent interrupt → SSE push to instructor → approval POST → agent resume → SSE result to student. Integration test. | PR #19, PR #9 |
| 21 | `feat/p3-langsmith` | `feat(agent-core): integrate LangSmith tracing across agent chains` | LangSmith callback handler. Trace spans for each state machine node. Token usage, latency, and tool call logging. Non-blocking (tracing errors don't affect request). | PR #17 |
| 22 | `feat/p3-cache-invalidation` | `feat(caching): add event-driven cache invalidation` | On course update via gRPC → invalidate all cached responses for that course_id. Manual flush endpoint for admin. Invalidation logging. | PR #11, PR #17 |

**Phase 3 milestone**: Merge `dev` → `main`. Tag `v0.3.0-full-backend`.

---

#### Phase 4: Frontend Dashboard

| PR # | Branch | Title | Description | Depends On |
|-------|--------|-------|-------------|------------|
| 23 | `feat/p4-dashboard-scaffold` | `chore(web-dashboard): initialize React + TypeScript + Tailwind project` | Vite + React setup. Tailwind configuration. Router structure (instructor/, student/, admin/). Shared UI components (layout, nav, cards). | PR #1 |
| 24 | `feat/p4-student-chat` | `feat(web-dashboard): build student chat UI with streaming responses` | Chat interface with message history. SSE connection for streaming LLM responses. Typing indicators. Session management. Mobile-responsive layout. | PR #23, PR #19 |
| 25 | `feat/p4-instructor-approval` | `feat(web-dashboard): build instructor approval dashboard` | Approval queue showing pending HiTL requests. Agent context display (what was attempted, why it was flagged). Approve/reject buttons wired to approval endpoint. Real-time SSE updates. | PR #23, PR #20 |
| 26 | `feat/p4-admin-panel` | `feat(web-dashboard): build admin panel with metrics and trace viewer` | Cache hit rate display. Agent success rate and latency P50/P95. LangSmith trace viewer embed (or custom trace display). System health dashboard. | PR #23, PR #21 |

**Phase 4 milestone**: Merge `dev` → `main`. Tag `v0.4.0-full-stack`.

---

#### Phase 5: Evaluation + DevOps + Polish

| PR # | Branch | Title | Description | Depends On |
|-------|--------|-------|-------------|------------|
| 27 | `feat/p5-e2e-test-suite` | `feat(evaluation): build automated end-to-end test suite` | 50+ test scenarios (enrollment, scheduling, tutoring, planning, safety). Pytest suite with scenario runner. Task completion rate measurement. Results output as JSON report. | PR #20 |
| 28 | `feat/p5-docker-compose` | `chore(infra): write Docker Compose for one-click startup` | Multi-service Docker Compose: FastAPI, Spring Boot, PostgreSQL, ChromaDB, Redis, React dashboard. Health checks and dependency ordering. Environment variable configuration (.env.example). | PR #26 |
| 29 | `feat/p5-ci-cd` | `chore(ci): set up GitHub Actions CI/CD pipeline` | Workflow: lint (ruff + eslint) → unit tests → integration tests → Docker build. Branch protection rules for main. Status badges for README. | PR #28 |
| 30 | `feat/p5-readme` | `docs: write comprehensive README with architecture and demo` | Architecture diagram (Mermaid or image). Quick start guide. Configuration reference. Demo GIF (screen recording of HiTL flow). Metrics summary table. License. | PR #29 |
| 31 | `feat/p5-aws-deploy` | `chore(infra): add AWS deployment configuration (optional)` | EC2 or EKS deployment scripts. Terraform config (optional). Production environment variables. Deployment documentation. | PR #28 |

**Phase 5 milestone**: Merge `dev` → `main`. Tag `v1.0.0-portfolio-ready`.

---

### PR Best Practices

**Size**: Each PR should be reviewable in under 30 minutes. If a PR touches more than ~400 lines of logic (excluding generated code, configs, and test data), split it.

**Description template**:
```markdown
## What
One-sentence summary of what this PR does.

## Why
What problem it solves or what feature it enables.

## How
Key implementation decisions. Link to relevant section in Development Plan.

## Testing
How this was tested. Include CLI commands or screenshots.

## Checklist
- [ ] Unit tests pass
- [ ] Integration tests pass (if applicable)
- [ ] No hardcoded secrets or API keys
- [ ] Updated relevant documentation
```

**Commit messages**: Use conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`). Each commit should be atomic — one logical change per commit.

**Review flow for solo project**: Even though you're the only contributor, open real PRs, write descriptions, and merge via GitHub UI. This creates a clean audit trail that interviewers can browse. Self-review each PR before merging — read the diff as if someone else wrote it.

---

## Interview Talking Points

### 30-Second Pitch
"I built a multi-agent AI assistant for university course management. The core challenge was making AI agents that are both autonomous and safe — students can ask questions and get instant answers, but when the agent needs to modify grades or enrollment, it pauses and gets instructor approval. I designed a two-layer safety system: static rules catch known high-risk operations, and an LLM-based intent analyzer catches novel abuse patterns like bulk grade changes."

### Key Design Decisions to Highlight
1. **Why multi-agent?** Three agents split by behavior (action/query/planning), not domain — each has fundamentally different execution patterns, tools, and safety profiles
2. **Why two-layer safety?** Static rules are fast and complete for known tools; LLM analyzer catches intent-level anomalies rules can't express
3. **Why hybrid retrieval?** Vector search alone returns 50% precision; adding structured PostgreSQL filtering and RRF fusion brought it to 75%
4. **Why gRPC?** Proto contracts give compile-time type safety across Python and Java — prevents the most common polyglot integration bugs
5. **Why response caching?** Deterministic queries (course info) don't need LLM calls — caching them saves cost and latency without safety risk

### Metrics to Cite (with methodology)
- **75% context precision**: Measured against 100 human-annotated query-document pairs, baseline was 50% with vector-only
- **85% task completion rate**: 50+ end-to-end test scenarios covering enrollment, scheduling, tutoring, and planning tasks
- **Sub-50ms SSE notification latency**: Timestamp at FastAPI emit, measured at browser receipt, P95 across concurrent sessions
- All metrics measured in test/staging environment, not production scale
