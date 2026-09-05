"""Health check endpoints."""

from typing import Any

from fastapi import APIRouter, Response
from sqlalchemy import text

from api.routes.deps import EngineDep
from common.utils import log_utils

logger = log_utils.get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
def get_health() -> dict[str, str]:
    """
    Kubernetes liveness probe.

    Returns basic health status without checking dependencies.
    """
    return {"status": "healthy", "service": "matik-api"}


@router.get("/ready")
def get_readiness(engine: EngineDep, response: Response) -> dict[str, Any]:
    """
    Kubernetes readiness probe.

    Checks database connectivity with a simple query.
    Returns 200 if ready, 503 if not ready.
    """
    health_status: dict[str, Any] = {
        "status": "ready",
        "service": "matik-api",
        "checks": {},
    }

    # Check database connectivity
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        health_status["checks"]["database"] = "ok"
    except Exception as e:
        logger.exception("database health check failed", error=str(e))
        health_status["status"] = "not_ready"
        health_status["checks"]["database"] = f"error: {e}"

    # Return 503 if not ready
    if health_status["status"] != "ready":
        response.status_code = 503
        logger.warning("readiness check failed", checks=health_status["checks"])

    return health_status
