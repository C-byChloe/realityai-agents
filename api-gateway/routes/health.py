"""Health check endpoint — returns status of all dependent services."""

import os
import socket

from fastapi import APIRouter
from pydantic import BaseModel

health_router = APIRouter()


class ServiceStatus(BaseModel):
    name: str
    status: str  # "healthy" or "unhealthy"
    detail: str = ""


class HealthResponse(BaseModel):
    status: str  # "healthy" if all services up, "degraded" if some down
    services: list[ServiceStatus]


def _check_tcp(host: str, port: int, timeout: float = 2.0) -> bool:
    """Check if a TCP port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


@health_router.get("/health", response_model=HealthResponse)
async def health_check():
    """Return the health status of all dependent services."""
    services = []

    # Check gRPC core service
    grpc_host = os.environ.get("GRPC_HOST", "localhost")
    grpc_port = int(os.environ.get("GRPC_PORT", "9090"))
    grpc_ok = _check_tcp(grpc_host, grpc_port)
    services.append(ServiceStatus(
        name="core-service-grpc",
        status="healthy" if grpc_ok else "unhealthy",
        detail=f"{grpc_host}:{grpc_port}",
    ))

    # Check PostgreSQL
    pg_host = os.environ.get("DB_HOST", "localhost")
    pg_port = int(os.environ.get("DB_PORT", "5432"))
    pg_ok = _check_tcp(pg_host, pg_port)
    services.append(ServiceStatus(
        name="postgresql",
        status="healthy" if pg_ok else "unhealthy",
        detail=f"{pg_host}:{pg_port}",
    ))

    # Check Redis
    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_port = int(os.environ.get("REDIS_PORT", "6379"))
    redis_ok = _check_tcp(redis_host, redis_port)
    services.append(ServiceStatus(
        name="redis",
        status="healthy" if redis_ok else "unhealthy",
        detail=f"{redis_host}:{redis_port}",
    ))

    all_healthy = all(s.status == "healthy" for s in services)

    return HealthResponse(
        status="healthy" if all_healthy else "degraded",
        services=services,
    )
