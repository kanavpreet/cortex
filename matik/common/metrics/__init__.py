"""Metrics utilities for Matik services.

Provides OpenTelemetry-based metrics integration with Telescope:
- TelescopeClient for OTLP HTTP export to local otel-collector sidecar
- ClientMetrics for external API client instrumentation
- DBMetrics for database query instrumentation
- HTTPMetrics for FastAPI HTTP middleware
- JobMetrics for background job instrumentation

Usage:
    from matik.common.metrics import (
        TelescopeClient,
        TelescopeConfig,
        ClientMetrics,
        DBMetrics,
        HTTPMetrics,
        JobMetrics,
    )

    # Initialize Telescope client
    client = TelescopeClient(config)
    client.start()

    # Create metrics instances
    client_metrics = ClientMetrics(client.meter, "historian", "incidentio")
    db_metrics = DBMetrics(client.meter, "historian")
    job_metrics = JobMetrics(client.meter, "historian")

    # Use HTTP middleware with FastAPI
    app.add_middleware(HTTPMetrics.create_middleware(client.meter, "api"))
"""

from common.metrics.chronicler_metrics import ChroniclerMetrics
from common.metrics.client_metrics import ClientMetrics
from common.metrics.correlation_metrics import CorrelationMetrics
from common.metrics.db_metrics import DBMetrics
from common.metrics.enricher_metrics import EnricherMetrics
from common.metrics.facade_metrics import FacadeMetrics
from common.metrics.ghe_api_metrics import GHEAPIMetrics
from common.metrics.ghe_cache_metrics import GHECacheMetrics
from common.metrics.http_middleware import HTTPMetrics
from common.metrics.incidentio_api_metrics import IncidentIOAPIMetrics
from common.metrics.jira_cache_metrics import JiraCacheMetrics
from common.metrics.job_metrics import JobMetrics
from common.metrics.mcp_metrics import McpMetrics
from common.metrics.scribe_metrics import ScribeMetrics
from common.metrics.service_signature_metrics import ServiceSignatureMetrics
from common.metrics.sqs_metrics import SQSMetrics
from common.metrics.sqs_publisher_metrics import SQSPublisherMetrics
from common.metrics.telescope import TelescopeClient, TelescopeConfig

__all__ = [
    "ChroniclerMetrics",
    "ClientMetrics",
    "CorrelationMetrics",
    "DBMetrics",
    "EnricherMetrics",
    "FacadeMetrics",
    "GHEAPIMetrics",
    "GHECacheMetrics",
    "HTTPMetrics",
    "IncidentIOAPIMetrics",
    "JiraCacheMetrics",
    "JobMetrics",
    "McpMetrics",
    "SQSMetrics",
    "SQSPublisherMetrics",
    "ScribeMetrics",
    "ServiceSignatureMetrics",
    "TelescopeClient",
    "TelescopeConfig",
]
