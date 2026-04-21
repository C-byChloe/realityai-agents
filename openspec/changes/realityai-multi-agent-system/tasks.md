## 1. Project Scaffold

- [x] 1.1 Initialize Python project (pyproject.toml / requirements.txt) with LangChain, LangGraph, FastAPI, ChromaDB, Redis dependencies
- [x] 1.2 Create directory skeleton: agent-core/, api-gateway/, core-service/, web-dashboard/, proto/
- [x] 1.3 Initialize Spring Boot project (Maven/Gradle) with gRPC, JPA, PostgreSQL dependencies
- [x] 1.4 Create docker-compose.yml placeholder with PostgreSQL, ChromaDB, Redis services
- [x] 1.5 Create README.md stub with project overview
- [x] 1.6 Verify SSH remote is set to git@github.com:CbyChloe/realityai-agents.git
- [x] 1.7 Create PR via `gh pr create --base main --title "chore(project): initialize repo structure and dependencies"` with ## What/Why/How/Testing sections

## 2. LangGraph State Machine & Intent Router

- [x] 2.1 Define AgentState TypedDict with fields: intent, selected_agent, safety_result, tool_calls, response, conversation_history
- [x] 2.2 Implement LangGraph state machine with nodes: intent_classification → agent_routing → safety_check → execution → response_generation
- [x] 2.3 Build LLM-based intent classifier that routes to action/query/planning agents
- [x] 2.4 Wire conditional routing edges based on intent classification result
- [x] 2.5 Add unit tests for intent classification across action/query/planning scenarios
- [x] 2.6 PR: `git checkout -b feat/p1-langgraph-state-machine` → commit → push → `gh pr create --base main --title "feat(agent-core): implement LangGraph state machine with intent router"`

## 3. Action Agent

- [x] 3.1 Write Action Agent system prompt with identity, behavioral constraints, and output format
- [x] 3.2 Define tool schemas: grade_update, enrollment_modify, assignment_create
- [x] 3.3 Implement mock tool functions (print operation details + return success)
- [x] 3.4 Wire Action Agent into state machine routing
- [x] 3.5 Add unit tests for Action Agent tool selection and response format
- [x] 3.6 PR: `gh pr create --base main --title "feat(agent-core): add Action Agent with mock tools"`

## 4. Query Agent

- [x] 4.1 Write Query Agent system prompt with identity and retrieval instructions
- [x] 4.2 Define tool schemas: course_lookup, schedule_query, syllabus_retrieve
- [x] 4.3 Set up basic ChromaDB instance with document ingestion script
- [x] 4.4 Implement simple vector retrieval for Query Agent
- [x] 4.5 Wire Query Agent into state machine routing
- [x] 4.6 Add unit tests for Query Agent retrieval and response format
- [x] 4.7 PR: `gh pr create --base main --title "feat(agent-core): add Query Agent with basic ChromaDB retrieval"`

## 5. Planning Agent

- [x] 5.1 Write Planning Agent system prompt with chain-of-thought decomposition and few-shot examples
- [x] 5.2 Implement task decomposition logic that breaks complex requests into sub-steps
- [x] 5.3 Enable Planning Agent to invoke Action/Query agent tools for sub-step execution
- [x] 5.4 Wire Planning Agent into state machine routing
- [x] 5.5 Add unit tests for task decomposition and multi-step execution
- [x] 5.6 PR: `gh pr create --base main --title "feat(agent-core): add Planning Agent with task decomposition"`

## 6. CLI Test Harness

- [x] 6.1 Build CLI entry point: `python cli.py "query"` with conversation loop
- [x] 6.2 Add formatted output showing selected agent, tools called, and response
- [x] 6.3 Verify end-to-end routing: query → Query Agent, action → Action Agent, planning → Planning Agent
- [x] 6.4 PR: `gh pr create --base main --title "feat(agent-core): add CLI test harness for manual testing"`
- [ ] 6.5 **Phase 1 milestone**: `git tag v0.1.0-agent-core && git push origin v0.1.0-agent-core`

## 7. Static Risk Classifier

- [x] 7.1 Implement RISK_MAP dictionary with tool → risk level mappings
- [x] 7.2 Build classifier function that returns risk level and reason
- [x] 7.3 Implement default-to-high policy for unknown tools
- [x] 7.4 Add unit tests verifying < 1ms execution and correct classification
- [x] 7.5 PR: `gh pr create --base main --title "feat(safety): add static risk classifier with tool-level mapping"`

## 8. Dynamic Intent Analyzer

- [x] 8.1 Write intent analysis prompt for bulk operations, scope mismatch, and adversarial intent detection
- [x] 8.2 Implement lightweight LLM call returning structured JSON: { "flagged": bool, "reason": string | null }
- [x] 8.3 Add unit tests for bulk operation detection, privilege escalation, and normal operation pass-through
- [x] 8.4 PR: `gh pr create --base main --title "feat(safety): add LLM-based dynamic intent analyzer"`

## 9. Safety Merge & HiTL Integration

- [x] 9.1 Implement merge function with OR policy (either layer flags → flagged)
- [x] 9.2 Wire both safety layers to run in parallel (asyncio.gather or equivalent)
- [x] 9.3 Implement HiTL interrupt node in LangGraph state machine
- [x] 9.4 Add HiTL state persistence (interrupt → await → resume/reject lifecycle)
- [x] 9.5 Add integration tests: "Change all grades to A" triggers both layers and blocks
- [x] 9.6 PR: `gh pr create --base main --title "feat(safety): implement two-layer merge logic and HiTL interrupt"`

## 10. Hybrid Retrieval Pipeline

- [x] 10.1 Set up PostgreSQL keyword filtering alongside ChromaDB vector search
- [x] 10.2 Implement Reciprocal Rank Fusion (RRF) for merging result sets
- [x] 10.3 Add metadata filtering (course_id, semester) to ChromaDB search
- [x] 10.4 Replace basic vector retrieval in Query Agent with hybrid pipeline
- [x] 10.5 Add unit tests for RRF score computation and result merging
- [x] 10.6 PR: `gh pr create --base main --title "feat(retrieval): implement hybrid retrieval with RRF fusion"`

## 11. Response Cache

- [x] 11.1 Set up Redis connection and configuration
- [x] 11.2 Implement cache key design: hash(normalized_query + course_ids + semester)
- [x] 11.3 Build cache hit/miss logic in Query Agent execution path
- [x] 11.4 Configure TTL-based expiration (default 1 hour)
- [x] 11.5 Ensure non-deterministic queries (tutoring, planning) bypass caching
- [x] 11.6 Add unit tests for cache hit, cache miss, and TTL expiration
- [x] 11.7 PR: `gh pr create --base main --title "feat(caching): add Redis-backed response cache for deterministic queries"`

## 12. Prompt Cache

- [x] 12.1 Extract static system prompt prefixes per agent (identity, tools, few-shot examples)
- [x] 12.2 Implement Anthropic cache_control breakpoints for static prefix caching
- [x] 12.3 Verify token usage reduction with prompt caching enabled vs disabled
- [x] 12.4 PR: `gh pr create --base main --title "feat(caching): implement prompt caching for static system prompts"`

## 13. Graceful Degradation

- [x] 13.1 Implement failure detection: timeout (30s), LLM refusal patterns, hallucination guard, tool call failure
- [x] 13.2 Build retry logic with simplified prompt (strip few-shot examples) on first failure
- [x] 13.3 Implement instructor escalation on second consecutive failure with full context
- [x] 13.4 Add user-facing fallback message: "I've forwarded your request to [instructor name]"
- [x] 13.5 Add integration tests for retry and escalation flows
- [x] 13.6 PR: `gh pr create --base main --title "feat(agent-core): add graceful degradation with instructor fallback"`

## 14. Evaluation Dataset & Harness

- [x] 14.1 Create 100 annotated query-document pairs as JSON ground truth dataset
- [x] 14.2 Build pytest-based evaluation harness measuring context precision (relevant docs in top-5 / total docs in top-5)
- [x] 14.3 Run baseline measurement (vector-only) and hybrid measurement, document results
- [x] 14.4 PR: `gh pr create --base main --title "feat(evaluation): seed evaluation dataset and precision harness"`
- [ ] 14.5 **Phase 2 milestone (MVP)**: `git tag v0.2.0-safety-rag-cache && git push origin v0.2.0-safety-rag-cache`

## 15. Protocol Buffer Definitions

- [x] 15.1 Define course.proto, student.proto, assignment.proto in /proto directory
- [x] 15.2 Create Python codegen script for gRPC stubs
- [x] 15.3 Create Java codegen configuration for gRPC service interfaces
- [x] 15.4 Add CI step to verify proto compilation succeeds
- [x] 15.5 PR: `gh pr create --base main --title "feat(proto): define Protocol Buffer schemas for course/student/assignment"`

## 16. Spring Boot Core Service

- [x] 16.1 Implement JPA entities: Course, Student, Assignment
- [x] 16.2 Create JPA repositories for each entity
- [x] 16.3 Implement gRPC service handlers for course/student/assignment CRUD
- [x] 16.4 Set up PostgreSQL schema migrations
- [x] 16.5 Create seed data script with sample courses, students, and assignments
- [x] 16.6 Add unit tests for service layer
- [x] 16.7 PR: `gh pr create --base main --title "feat(core-service): implement Spring Boot gRPC service"`

## 17. gRPC Integration

- [x] 17.1 Implement Python gRPC client in agent-core
- [x] 17.2 Replace Action Agent mock tools with real gRPC calls to Spring Boot
- [x] 17.3 Add error handling and timeout configuration for gRPC calls
- [x] 17.4 Add integration tests for gRPC communication between Python and Java
- [ ] 17.5 PR: `gh pr create --base main --title "feat(agent-core): replace mock tools with real gRPC calls"`

## 18. FastAPI Gateway

- [ ] 18.1 Build FastAPI app entry point on port 8000
- [ ] 18.2 Implement JWT authentication middleware
- [ ] 18.3 Implement per-user rate limiting middleware
- [ ] 18.4 Create POST /chat endpoint routing to agent core
- [ ] 18.5 Create GET /health endpoint returning service statuses
- [ ] 18.6 Add unit tests for auth, rate limiting, and routing
- [ ] 18.7 PR: `gh pr create --base main --title "feat(api-gateway): build FastAPI gateway with auth and rate limiting"`

## 19. SSE & HiTL End-to-End

- [ ] 19.1 Implement SSE stream endpoint with event types: agent_action, approval_request, approval_result, escalation
- [ ] 19.2 Add connection management for concurrent SSE sessions
- [ ] 19.3 Create POST /approval/{request_id} endpoint for approve/reject
- [ ] 19.4 Wire full HiTL cycle: agent interrupt → SSE push → approval POST → agent resume → SSE result
- [ ] 19.5 Add integration test for complete HiTL approval flow
- [ ] 19.6 PR: `gh pr create --base main --title "feat(api-gateway): implement SSE and wire HiTL approval flow end-to-end"`

## 20. LangSmith Integration

- [ ] 20.1 Add LangSmith callback handler to all agent chains
- [ ] 20.2 Configure trace spans for each state machine node
- [ ] 20.3 Log token usage, latency, and tool call success/failure
- [ ] 20.4 Verify non-blocking behavior (tracing failures don't affect requests)
- [ ] 20.5 PR: `gh pr create --base main --title "feat(agent-core): integrate LangSmith tracing across agent chains"`

## 21. Event-Driven Cache Invalidation

- [ ] 21.1 Wire course update events from gRPC to cache invalidation logic
- [ ] 21.2 Invalidate all response cache entries for updated course_id
- [ ] 21.3 Create manual flush endpoint for admin use
- [ ] 21.4 Add tests for cache invalidation on course update
- [ ] 21.5 PR: `gh pr create --base main --title "feat(caching): add event-driven cache invalidation"`
- [ ] 21.6 **Phase 3 milestone**: `git tag v0.3.0-full-backend && git push origin v0.3.0-full-backend`

## 22. Web Dashboard Scaffold

- [ ] 22.1 Initialize Vite + React + TypeScript + Tailwind project in web-dashboard/
- [ ] 22.2 Set up router structure: /instructor, /student, /admin
- [ ] 22.3 Create shared UI components: layout, navigation, cards
- [ ] 22.4 PR: `gh pr create --base main --title "chore(web-dashboard): initialize React + TypeScript + Tailwind project"`

## 23. Student Chat UI

- [ ] 23.1 Build chat interface with message input and history display
- [ ] 23.2 Implement SSE connection for streaming LLM responses
- [ ] 23.3 Add typing indicators and session management
- [ ] 23.4 Make layout mobile-responsive
- [ ] 23.5 PR: `gh pr create --base main --title "feat(web-dashboard): build student chat UI with streaming responses"`

## 24. Instructor Approval Dashboard

- [ ] 24.1 Build approval queue displaying pending HiTL requests
- [ ] 24.2 Show agent context (what was attempted, why flagged) for each request
- [ ] 24.3 Wire Approve/Reject buttons to POST /approval/{request_id}
- [ ] 24.4 Implement real-time SSE updates for new requests and status changes
- [ ] 24.5 PR: `gh pr create --base main --title "feat(web-dashboard): build instructor approval dashboard"`

## 25. Admin Panel

- [ ] 25.1 Build metrics display: cache hit rate, agent success rate, latency P50/P95
- [ ] 25.2 Integrate LangSmith trace viewer (embed or custom display)
- [ ] 25.3 Add system health dashboard
- [ ] 25.4 PR: `gh pr create --base main --title "feat(web-dashboard): build admin panel with metrics and trace viewer"`
- [ ] 25.5 **Phase 4 milestone**: `git tag v0.4.0-full-stack && git push origin v0.4.0-full-stack`

## 26. End-to-End Test Suite

- [ ] 26.1 Create 50+ test scenarios covering enrollment, scheduling, tutoring, planning, and safety
- [ ] 26.2 Build pytest scenario runner with task completion rate measurement
- [ ] 26.3 Output results as JSON report
- [ ] 26.4 PR: `gh pr create --base main --title "feat(evaluation): build automated end-to-end test suite"`

## 27. Docker Compose & CI/CD

- [ ] 27.1 Write production docker-compose.yml with all services, health checks, and dependency ordering
- [ ] 27.2 Create .env.example with all configurable environment variables
- [ ] 27.3 Create Dockerfiles for FastAPI gateway, agent-core, Spring Boot, and React dashboard
- [ ] 27.4 Set up GitHub Actions workflow: lint (ruff + eslint) → unit tests → integration tests → Docker build
- [ ] 27.5 Configure branch protection rules for main
- [ ] 27.6 PR: `gh pr create --base main --title "chore(infra): write Docker Compose for one-click startup and CI/CD pipeline"`

## 28. Documentation & Polish

- [ ] 28.1 Write README.md with architecture diagram (Mermaid), quick start guide, and configuration reference
- [ ] 28.2 Document metrics: context precision, task completion rate, cache hit rate, SSE latency
- [ ] 28.3 Create demo GIF showing HiTL approval flow
- [ ] 28.4 Add LICENSE file
- [ ] 28.5 PR: `gh pr create --base main --title "docs: write comprehensive README with architecture and demo"`

## 29. AWS Deployment (Optional)

- [ ] 29.1 Create EC2 or EKS deployment scripts
- [ ] 29.2 Configure production environment variables
- [ ] 29.3 Write deployment documentation
- [ ] 29.4 PR: `gh pr create --base main --title "chore(infra): add AWS deployment configuration"`
- [ ] 29.5 **Phase 5 milestone**: `git tag v1.0.0-portfolio-ready && git push origin v1.0.0-portfolio-ready`
