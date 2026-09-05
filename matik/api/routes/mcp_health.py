"""MCP health check endpoint.

Exposes /v1/mcp/health so the MCP server can verify the Matik API
is reachable and ready to serve tool calls. This endpoint lives under
the /v1/mcp/ prefix so it is discoverable via the OpenAPI spec and
registered as an MCP tool by the tool registry.
"""

from typing import Any

from fastapi import APIRouter, Response
from sqlalchemy import text

from api.routes.deps import EngineDep
from common.utils import log_utils

logger = log_utils.get_logger(__name__)

router = APIRouter(prefix="/v1/mcp", tags=["mcp"])


@router.get(
    "/health",
    operation_id="mcp_health",
    summary="Check Matik API health for MCP connectivity",
    description=(
        "Returns the health status of the Matik API including database "
        "connectivity. Used by the MCP server to verify the API is reachable "
        "and ready to serve tool calls."
    ),
)
def mcp_health(engine: EngineDep, response: Response) -> dict[str, Any]:
    """Check API health for MCP server connectivity validation.

    Returns 200 if the API and its dependencies are healthy,
    503 if any critical dependency is unavailable.
    """
    checks: dict[str, Any] = {}
    status = "healthy"

    # Check database connectivity
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        logger.error("mcp health check: database unreachable", error=str(e))
        checks["database"] = f"error: {e}"
        status = "unhealthy"

    body: dict[str, Any] = {
        "status": status,
        "service": "matik-api",
        "checks": checks,
    }

    if status != "healthy":
        response.status_code = 503

    return body
