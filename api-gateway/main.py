"""FastAPI gateway — single entry point for all client requests.

Routes requests to the LangGraph agent core, handles JWT auth,
rate limiting, and health checks.
"""

import os

from fastapi import FastAPI

from auth.jwt import jwt_router
from middleware.rate_limit import RateLimitMiddleware
from routes.admin import admin_router
from routes.approval import approval_router
from routes.chat import chat_router
from routes.health import health_router
from routes.sse import sse_router

app = FastAPI(
    title="RealityAI API Gateway",
    description="API gateway for the RealityAI multi-agent course management system",
    version="0.1.0",
)

# Middleware
app.add_middleware(
    RateLimitMiddleware,
    requests_per_minute=int(os.environ.get("RATE_LIMIT_RPM", "60")),
)

# Routes
app.include_router(chat_router, prefix="/chat", tags=["chat"])
app.include_router(health_router, tags=["health"])
app.include_router(jwt_router, prefix="/auth", tags=["auth"])
app.include_router(sse_router, prefix="/sse", tags=["sse"])
app.include_router(approval_router, prefix="/approval", tags=["approval"])
app.include_router(admin_router, prefix="/admin", tags=["admin"])


@app.get("/")
async def root():
    return {"service": "realityai-api-gateway", "version": "0.1.0"}
