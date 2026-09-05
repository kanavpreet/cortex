"""API service entry point."""

from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager, suppress
from typing import Any

import boto3
from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from api.routes import (
    correlations_router,
    enrichment_router,
    ghe_org_crawl_tracker_router,
    ghe_pr_router,
    ghe_pr_tracker_router,
    health_router,
    incidentio_router,
    jira_router,
    mcp_health_router,
    mcp_router,
    root_router,
)
from common.config import load_config, merge_config
from common.constants import (
    API_VERSION,
    SERVICE_SIGNATURE_HEADER,
    SERVICE_TIMESTAMP_HEADER,
    TASK_ID_HEADER,
)
from common.metrics import (
    DBMetrics,
    GHEAPIMetrics,
    HTTPMetrics,
    IncidentIOAPIMetrics,
    ServiceSignatureMetrics,
    TelescopeClient,
)
from common.utils import log_utils
from common.utils.db_utils import create_long_lived_engine
from common.utils.service_auth import SignatureVerificationError, verify_request

logger = log_utils.get_logger(__name__)

# Routes that must stay reachable with no identity check of any kind: kubelet
# liveness/readiness probes, MCP's own preflight check of the API, and the
# welcome/version route. None of these return source data. See the
# access-posture doc's "Routes exempt from any new check".
_SIGNATURE_EXEMPT_PATHS = frozenset({"/health", "/ready", "/v1/mcp/health", "/"})

# Load config and initialize telescope at module level (before app starts)
# This is needed because middleware must be added before the app starts
# Wrapped in try/except to allow tests to patch load_config
_config = None
_telescope: TelescopeClient | None = None
_ghe_api_metrics: GHEAPIMetrics | None = None
_incidentio_api_metrics: IncidentIOAPIMetrics | None = None
_db_metrics: DBMetrics | None = None
_service_signature_metrics: ServiceSignatureMetrics | None = None

try:
    _config = load_config("matik-api-config.yml")
    _config = merge_config(_config, "metrics.yml")

    # Merge metrics config (optional - for Telescope metrics)
    with suppress(FileNotFoundError):
        _config = merge_config(_config, "metrics.yml")

    # Configure logging early so all module-level initialization uses correct settings
    log_utils.configure(
        level=_config.common.log_level, environment=_config.common.environment
    )

    if _config.telescope and _config.telescope.enabled:  # pragma: no cover
        _config.telescope.service_name = "api"
        _config.telescope.environment = _config.common.environment
        _telescope = TelescopeClient(_config.telescope)
        _telescope.start()

        if _telescope.is_enabled:
            _ghe_api_metrics = GHEAPIMetrics(_telescope.meter, "api")
            _incidentio_api_metrics = IncidentIOAPIMetrics(_telescope.meter, "api")
            _db_metrics = DBMetrics(_telescope.meter, "api")
            _service_signature_metrics = ServiceSignatureMetrics(
                _telescope.meter, "api"
            )
except FileNotFoundError:
    # Config file not found - likely running in test mode
    # Tests will patch load_config and provide their own config
    pass

_service_secret: str | None = (
    _config.api.service_secret if _config and _config.api else None
)
_enforce_service_signature: bool = bool(
    _config and _config.api and _config.api.enforce_service_signature
)


def create_service_signature_dispatch(
    secret: str | None,
    enforce: bool,
    metrics: ServiceSignatureMetrics | None = None,
) -> Callable[
    [Request, Callable[[Request], Awaitable[Response]]], Coroutine[Any, Any, Response]
]:
    """Build the Phase 1c dispatch function for use with BaseHTTPMiddleware.

    Defense-in-depth behind AirMesh's `allows` mTLS boundary — see the
    access-posture doc, Finding 2d / Phase 1c. When `secret` is unset (not
    yet provisioned in this environment), every request is a no-op pass
    through. When `enforce` is False (shadow mode), failures are logged but
    requests still proceed, so rollout can be verified against real traffic
    before anything gets rejected.

    `metrics` (matik_api_service_signature_checks_total) is what tells you
    whether shadow-mode logs are actually clean enough to flip `enforce` on
    for an environment, and alerts if enforced requests start failing —
    exempt/no-secret bypasses are not recorded, since health-check traffic
    would otherwise dominate the series.

    Usage:
        app.add_middleware(
            BaseHTTPMiddleware,
            dispatch=create_service_signature_dispatch(secret, enforce, metrics),
        )
    """

    async def dispatch(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not secret or request.url.path in _SIGNATURE_EXEMPT_PATHS:
            return await call_next(request)

        body = await request.body()
        # Signed target is path + raw query string, matching what callers
        # sign via httpx.URL(...).raw_path — read straight from the ASGI
        # scope rather than request.url.query, so this is the exact query
        # bytes received, not a parsed-and-reserialized reconstruction of it.
        query_string = request.scope.get("query_string", b"").decode()
        target = request.url.path + (f"?{query_string}" if query_string else "")
        try:
            verify_request(
                secret,
                request.method,
                target,
                body,
                request.headers.get(SERVICE_TIMESTAMP_HEADER),
                request.headers.get(SERVICE_SIGNATURE_HEADER),
            )
        except SignatureVerificationError as err:
            if metrics:
                metrics.record(valid=False, reason=str(err), enforced=enforce)
            log = logger.warning if enforce else logger.info
            log(
                "service signature check failed"
                + ("" if enforce else " (shadow mode, not enforced)"),
                method=request.method,
                path=request.url.path,
                reason=str(err),
            )
            if enforce:
                return Response(
                    content="invalid service signature",
                    status_code=401,
                )
        else:
            if metrics:
                metrics.record(valid=True, reason="", enforced=enforce)

        return await call_next(request)

    return dispatch


class TaskIDMiddleware(BaseHTTPMiddleware):
    """Middleware to set task ID for logging and tracing."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Set task ID from request header or generate new one."""
        # Get task ID from header or generate new one
        incoming_id = request.headers.get(TASK_ID_HEADER)
        if incoming_id:
            task_id = log_utils.set_task_id(incoming_id)
        else:
            task_id = log_utils.generate_task_id(__name__)

        # Log incoming request (skip health checks to reduce noise)
        if request.url.path not in ("/health", "/ready"):
            logger.info(
                "request started",
                method=request.method,
                path=request.url.path,
            )

        try:
            response = await call_next(request)
        except Exception:
            logger.error(
                "request failed",
                method=request.method,
                path=request.url.path,
                exc_info=True,
            )
            raise
        else:
            if response.status_code >= 400 and request.url.path not in (
                "/health",
                "/ready",
            ):
                log = logger.error if response.status_code >= 500 else logger.warning
                log(
                    "request completed with error status",
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                )
            # Add task ID to response header
            response.headers[TASK_ID_HEADER] = task_id
            return response
        finally:
            log_utils.clear_task_id()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Handle startup and shutdown events."""
    # In test mode, config is loaded via patch; in production, it's loaded at module level
    config = _config if _config else load_config("matik-api-config.yml")
    log_utils.configure(
        level=config.common.log_level, environment=config.common.environment
    )

    # Initialize database engine
    if config.mysql:
        app.state.engine = create_long_lived_engine(config.mysql)
        logger.info("database engine initialized")
    else:
        logger.warning("no mysql config found, database features disabled")
        app.state.engine = None

    # Store enigmatologist config and create shared SQS client for routes
    app.state.enigmatologist_config = config.enigmatologist
    if config.enigmatologist and config.enigmatologist.sqs_queue_url:
        session = boto3.Session(region_name=config.enigmatologist.region)
        app.state.sqs_client = session.client("sqs")
        logger.info(
            "enigmatologist SQS dispatch configured",
            queue_url=config.enigmatologist.sqs_queue_url,
        )
    else:
        app.state.sqs_client = None
        logger.info("enigmatologist not configured, correlation dispatch disabled")

    # Set metrics on app state (initialized at module level)
    app.state.telescope = _telescope
    app.state.ghe_api_metrics = _ghe_api_metrics
    app.state.incidentio_api_metrics = _incidentio_api_metrics
    app.state.db_metrics = _db_metrics

    if _telescope and _telescope.is_enabled:
        logger.info("telescope metrics initialized")
    else:
        logger.info("telescope metrics disabled or not configured")

    logger.info("api service started")

    yield

    # Cleanup
    if hasattr(app.state, "engine") and app.state.engine:
        app.state.engine.dispose()
        logger.info("database engine disposed")

    if _telescope:
        _telescope.shutdown()
        logger.info("telescope metrics shutdown")

    logger.info("api service shutting down")


app = FastAPI(
    title="Matik API",
    description="AIOps platform API for correlation data",
    version=API_VERSION,
    lifespan=lifespan,
)

# Add middleware (order matters - last added runs first)
# HTTPMetrics must be added before app starts, so it's done at module level
if _telescope and _telescope.is_enabled:  # pragma: no cover
    app.add_middleware(
        BaseHTTPMiddleware,
        dispatch=HTTPMetrics.create_dispatch(_telescope.meter, "api"),
    )
app.add_middleware(
    BaseHTTPMiddleware,
    dispatch=create_service_signature_dispatch(
        _service_secret, _enforce_service_signature, _service_signature_metrics
    ),
)
app.add_middleware(TaskIDMiddleware)

# Include routers
app.include_router(root_router)
app.include_router(health_router)
app.include_router(enrichment_router)
app.include_router(ghe_pr_router)
app.include_router(ghe_pr_tracker_router)
app.include_router(ghe_org_crawl_tracker_router)
app.include_router(incidentio_router)
app.include_router(jira_router)
app.include_router(correlations_router)
app.include_router(mcp_health_router)
app.include_router(mcp_router)
