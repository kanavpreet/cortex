"""Dependency injection for API routes."""

from typing import Annotated, Any, cast

from fastapi import Depends, Request
from sqlalchemy.engine import Engine

from common.daos import (
    GHEPRDAO,
    GHEOrgCrawlTrackerDAO,
    GHEPRTrackerDAO,
    IncidentIOIncidentDAO,
    IncidentIOTrackerDAO,
    JiraBatchTrackerDAO,
    JiraIssuesDAO,
    ReliabilityCorrelationDAO,
    ReliabilityCorrelationGroupDAO,
)
from common.metrics import DBMetrics, GHEAPIMetrics, IncidentIOAPIMetrics
from common.models.enigmatologist_config import EnigmatologistConfig


def get_enigmatologist_config(request: Request) -> EnigmatologistConfig | None:
    """Get enigmatologist config from app state.

    Returns None if enigmatologist is not configured.
    """
    if hasattr(request.app.state, "enigmatologist_config"):
        return cast(
            EnigmatologistConfig | None,
            request.app.state.enigmatologist_config,
        )
    return None


def get_sqs_client(request: Request) -> Any | None:
    """Get shared SQS client from app state.

    Returns None if enigmatologist SQS is not configured.
    """
    if hasattr(request.app.state, "sqs_client"):
        return request.app.state.sqs_client
    return None


def get_engine(request: Request) -> Engine:
    """Get database engine from app state."""
    if request.app.state.engine is None:
        raise RuntimeError("Database engine not initialized")
    return cast(Engine, request.app.state.engine)


def get_db_metrics(request: Request) -> DBMetrics | None:
    """Get DB metrics from app state.

    Returns None if metrics are not initialized (e.g., telescope disabled).
    """
    if hasattr(request.app.state, "db_metrics"):
        return cast(DBMetrics | None, request.app.state.db_metrics)
    return None


def get_ghe_pr_dao(
    engine: Annotated[Engine, Depends(get_engine)],
    metrics: Annotated[DBMetrics | None, Depends(get_db_metrics)],
) -> GHEPRDAO:
    """Get GHE PR DAO."""
    return GHEPRDAO(engine, metrics)


def get_ghe_pr_tracker_dao(
    engine: Annotated[Engine, Depends(get_engine)],
    metrics: Annotated[DBMetrics | None, Depends(get_db_metrics)],
) -> GHEPRTrackerDAO:
    """Get GHE PR tracker DAO."""
    return GHEPRTrackerDAO(engine, metrics)


def get_ghe_org_crawl_tracker_dao(
    engine: Annotated[Engine, Depends(get_engine)],
    metrics: Annotated[DBMetrics | None, Depends(get_db_metrics)],
) -> GHEOrgCrawlTrackerDAO:
    """Get GHE org crawl tracker DAO."""
    return GHEOrgCrawlTrackerDAO(engine, metrics)


def get_jira_issues_dao(
    engine: Annotated[Engine, Depends(get_engine)],
    metrics: Annotated[DBMetrics | None, Depends(get_db_metrics)],
) -> JiraIssuesDAO:
    """Get JIRA issues DAO."""
    return JiraIssuesDAO(engine, metrics)


def get_jira_batch_tracker_dao(
    engine: Annotated[Engine, Depends(get_engine)],
    metrics: Annotated[DBMetrics | None, Depends(get_db_metrics)],
) -> JiraBatchTrackerDAO:
    """Get JIRA batch tracker DAO."""
    return JiraBatchTrackerDAO(engine, metrics)


def get_ghe_api_metrics(request: Request) -> GHEAPIMetrics | None:
    """Get GHE API metrics from app state.

    Returns None if metrics are not initialized (e.g., telescope disabled).
    """
    if hasattr(request.app.state, "ghe_api_metrics"):
        return cast(GHEAPIMetrics | None, request.app.state.ghe_api_metrics)
    return None


def get_incidentio_api_metrics(request: Request) -> IncidentIOAPIMetrics | None:
    """Get IncidentIO API metrics from app state.

    Returns None if metrics are not initialized (e.g., telescope disabled).
    """
    if hasattr(request.app.state, "incidentio_api_metrics"):
        return cast(
            IncidentIOAPIMetrics | None, request.app.state.incidentio_api_metrics
        )
    return None


def get_incidentio_incident_dao(
    engine: Annotated[Engine, Depends(get_engine)],
    metrics: Annotated[DBMetrics | None, Depends(get_db_metrics)],
) -> IncidentIOIncidentDAO:
    """Get IncidentIO incident DAO."""
    return IncidentIOIncidentDAO(engine, metrics)


def get_incidentio_tracker_dao(
    engine: Annotated[Engine, Depends(get_engine)],
    metrics: Annotated[DBMetrics | None, Depends(get_db_metrics)],
) -> IncidentIOTrackerDAO:
    """Get IncidentIO tracker DAO."""
    return IncidentIOTrackerDAO(engine, metrics)


def get_reliability_correlation_group_dao(
    engine: Annotated[Engine, Depends(get_engine)],
    metrics: Annotated[DBMetrics | None, Depends(get_db_metrics)],
) -> ReliabilityCorrelationGroupDAO:
    """Get reliability correlation group DAO."""
    return ReliabilityCorrelationGroupDAO(engine, metrics)


def get_reliability_correlation_dao(
    engine: Annotated[Engine, Depends(get_engine)],
    metrics: Annotated[DBMetrics | None, Depends(get_db_metrics)],
) -> ReliabilityCorrelationDAO:
    """Get reliability correlation DAO."""
    return ReliabilityCorrelationDAO(engine, metrics)


# Type aliases for cleaner dependency injection
EnigmatologistConfigDep = Annotated[
    EnigmatologistConfig | None, Depends(get_enigmatologist_config)
]
SQSClientDep = Annotated[Any | None, Depends(get_sqs_client)]
EngineDep = Annotated[Engine, Depends(get_engine)]
GHEPRDAODep = Annotated[GHEPRDAO, Depends(get_ghe_pr_dao)]
GHEPRTrackerDAODep = Annotated[GHEPRTrackerDAO, Depends(get_ghe_pr_tracker_dao)]
GHEOrgCrawlTrackerDAODep = Annotated[
    GHEOrgCrawlTrackerDAO, Depends(get_ghe_org_crawl_tracker_dao)
]
JiraIssuesDAODep = Annotated[JiraIssuesDAO, Depends(get_jira_issues_dao)]
JiraBatchTrackerDAODep = Annotated[
    JiraBatchTrackerDAO, Depends(get_jira_batch_tracker_dao)
]
GHEAPIMetricsDep = Annotated[GHEAPIMetrics | None, Depends(get_ghe_api_metrics)]
IncidentIOAPIMetricsDep = Annotated[
    IncidentIOAPIMetrics | None, Depends(get_incidentio_api_metrics)
]
DBMetricsDep = Annotated[DBMetrics | None, Depends(get_db_metrics)]
IncidentIOIncidentDAODep = Annotated[
    IncidentIOIncidentDAO, Depends(get_incidentio_incident_dao)
]
IncidentIOTrackerDAODep = Annotated[
    IncidentIOTrackerDAO, Depends(get_incidentio_tracker_dao)
]
ReliabilityCorrelationGroupDAODep = Annotated[
    ReliabilityCorrelationGroupDAO,
    Depends(get_reliability_correlation_group_dao),
]
ReliabilityCorrelationDAODep = Annotated[
    ReliabilityCorrelationDAO, Depends(get_reliability_correlation_dao)
]
