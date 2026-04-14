# RealityAI Multi-Agent System

An AI-powered course management assistant for Higher Education, built around a multi-agent orchestration system with two-layer safety controls, hybrid retrieval, and production-grade cost optimization.

## Architecture

```
Client (React + Tailwind)
    │
    ▼
API Gateway (FastAPI) ── JWT Auth, Rate Limiting, SSE
    │
    ▼
Agent Core (LangGraph State Machine)
    ├── Action Agent (writes) ──► gRPC ──► Spring Boot Core
    ├── Query Agent (reads/RAG) ──► ChromaDB + PostgreSQL
    └── Planning Agent (multi-step reasoning)
    │
    ▼
Two-Layer Safety: Static Risk Classifier + Dynamic Intent Analyzer
    │
    ▼
Human-in-the-Loop (instructor approval for high-risk operations)
```

## Tech Stack

| Service | Language | Framework | Port |
|---------|----------|-----------|------|
| API Gateway | Python | FastAPI | 8000 |
| Agent Core | Python | LangChain / LangGraph | (internal) |
| Core Service | Java | Spring Boot | 8080 |
| Web Dashboard | TypeScript | React + Tailwind | 3000 |

| Data Store | Purpose |
|------------|---------|
| PostgreSQL | Relational metadata, course data |
| ChromaDB | Vector embeddings for RAG |
| Redis | Response cache, session state |

## Quick Start

```bash
# Start infrastructure services
docker-compose up -d

# Install Python dependencies
pip install -r agent-core/requirements.txt

# Run the CLI test harness
python agent-core/cli.py "What time does CS101 meet?"
```

## Project Structure

```
realityai-agents/
├── agent-core/          # Python: LangGraph + agents + RAG
├── api-gateway/         # Python: FastAPI gateway
├── core-service/        # Java: Spring Boot gRPC service
├── web-dashboard/       # React + TypeScript + Tailwind
├── proto/               # Shared .proto files
└── docker-compose.yml   # Infrastructure services
```

## Development

See [RealityAI_Development_Plan.md](RealityAI_Development_Plan.md) for the full development plan.
