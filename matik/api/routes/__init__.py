"""API route modules."""

from api.routes.correlations import router as correlations_router
from api.routes.enrichment import router as enrichment_router
from api.routes.ghe_org_crawl_tracker import router as ghe_org_crawl_tracker_router
from api.routes.ghe_pr import router as ghe_pr_router
from api.routes.ghe_pr_tracker import router as ghe_pr_tracker_router
from api.routes.health import router as health_router
from api.routes.incidentio import router as incidentio_router
from api.routes.jira import router as jira_router
from api.routes.mcp import router as mcp_router
from api.routes.mcp_health import router as mcp_health_router
from api.routes.root import router as root_router

__all__ = [
    "correlations_router",
    "enrichment_router",
    "ghe_org_crawl_tracker_router",
    "ghe_pr_router",
    "ghe_pr_tracker_router",
    "health_router",
    "incidentio_router",
    "jira_router",
    "mcp_health_router",
    "mcp_router",
    "root_router",
]
