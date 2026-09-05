"""Reliability correlation engine — incident anchor."""

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any

import httpx

from common.constants import API_V1_PREFIX
from common.llm_tracing import traced_llm_operation
from common.metrics import CorrelationMetrics, SQSPublisherMetrics
from common.models.matik_config import MatikConfig
from common.models.reliability_correlation import ReliabilityCorrelation
from common.models.reliability_correlation_group import ReliabilityCorrelationGroup
from common.models.scribe_messages import (
    CorrelationBaseMessage,
    CorrelationGroupBaseMessage,
)
from common.utils import log_utils
from common.utils.service_auth import (
    build_signature_headers,
    log_signature_rejection_401,
)

from .graph import build_graph
from .scoring import GROUP_SCORE_CAP
from .state import (
    ChangeEvent,
    CorrelationGroupResult,
    CorrelationMatch,
    IncidentCorrelationState,
    LLMCorrelationResult,
    ServiceCorrelationResult,
)

logger = log_utils.get_logger(__name__)

SOURCE_ENTITY_TYPE = {
    "biztech_github": "github_pr",
    "jira": "jira_tcmr",
}


def _map_matches(
    matches: list[CorrelationMatch],
    source: str,
    anchor_entity_id: str,
    correlation_type: str,
    event_map: dict[str, ChangeEvent],
) -> list[ReliabilityCorrelation]:
    """Map CorrelationMatch items to ReliabilityCorrelation rows."""
    entity_type = SOURCE_ENTITY_TYPE.get(source, source)
    results: list[ReliabilityCorrelation] = []
    for m in matches:
        event = event_map.get(m.id)
        results.append(
            ReliabilityCorrelation(
                anchor_entity_id=anchor_entity_id,
                correlation_type=correlation_type,
                entity_type=entity_type,
                entity_id=m.id,
                reasoning=m.reasoning,
                services=event.services if event else None,
                start_time=event.start_time if event else None,
                end_time=event.end_time if event else None,
                base_score=m.base_score,
                final_score=m.final_score,
                scoring_version=m.scoring_version,
            )
        )
    return results


async def _fetch_existing_correlations(
    api_client: httpx.AsyncClient,
    anchor_entity_id: str,
    config: MatikConfig | None,
) -> list[ReliabilityCorrelation]:
    """Fetch existing correlations for an anchor from the API.

    Returns an empty list on error so the caller can still proceed
    with only the current run's correlations.
    """
    url = f"{API_V1_PREFIX}/correlation/reliability/events/{anchor_entity_id}"
    try:
        timeout = (
            config.enigmatologist.matik_api_read_timeout
            if config and config.enigmatologist
            else 10.0
        )
        headers = (
            build_signature_headers(config.api.service_secret, "GET", url, b"")
            if config and config.api and config.api.service_secret
            else None
        )
        resp = await api_client.get(url, timeout=timeout, headers=headers)
        resp.raise_for_status()
        return [ReliabilityCorrelation.model_validate(item) for item in resp.json()]
    except httpx.HTTPStatusError as err:
        if err.response.status_code == 401:
            log_signature_rejection_401(
                logger,
                "fetching existing correlations",
                anchor_entity_id=anchor_entity_id,
            )
        else:
            logger.warning(
                "failed to fetch existing correlations",
                anchor_entity_id=anchor_entity_id,
                status_code=err.response.status_code,
                exc_info=True,
            )
        return []
    except Exception:
        logger.warning(
            "failed to fetch existing correlations",
            anchor_entity_id=anchor_entity_id,
            exc_info=True,
        )
        return []


def _compute_group_from_all_correlations(
    all_correlations: list[ReliabilityCorrelation],
    scoring_version: str,
) -> tuple[datetime | None, datetime | None, float, float]:
    """Compute group-level fields from the full set of correlations.

    Args:
        all_correlations: Merged list (existing + new) of correlations
        scoring_version: Scoring version string

    Returns:
        (start_time, end_time, base_score, final_score)
    """
    start_times = [c.start_time for c in all_correlations if c.start_time]
    end_times = [c.end_time for c in all_correlations if c.end_time]
    # Also consider start_time when computing the latest boundary
    all_end_candidates = end_times + start_times

    group_start = min(start_times) if start_times else None
    group_end = max(all_end_candidates) if all_end_candidates else None

    scored = [c.final_score for c in all_correlations if c.final_score is not None]
    if scored:
        avg_score = sum(scored) / len(scored)
        group_base = round(min(avg_score, GROUP_SCORE_CAP), 4)
    else:
        group_base = 0.0

    return group_start, group_end, group_base, group_base


def _build_persistence_payload(
    payload: dict[str, Any],
    group_result: CorrelationGroupResult,
    service_result: ServiceCorrelationResult | None,
    llm_result: LLMCorrelationResult | None,
    github_events: list[ChangeEvent],
    jira_events: list[ChangeEvent],
    existing_correlations: list[ReliabilityCorrelation],
) -> tuple[ReliabilityCorrelationGroup, list[ReliabilityCorrelation]]:
    """Map graph output to DB models for API persistence.

    Uses the original service/LLM results (not the merged group) to preserve
    which path each match came from, tagging correlation_type correctly.

    Group start_time, end_time, and base_score are recomputed from the full
    set of correlations (existing + new from this run).

    Args:
        payload: Original correlation request payload
        group_result: CorrelationGroupResult (for scoring_version)
        service_result: Service-matched correlations (correlation_type=SERVICE_MATCH)
        llm_result: LLM-scored correlations (correlation_type=LLM)
        github_events: ChangeEvents from GitHub (carry start_time/end_time)
        jira_events: ChangeEvents from Jira (carry start_time/end_time)
        existing_correlations: Previously-persisted correlations for this anchor

    Returns:
        Tuple of (group model, list of correlation models)
    """
    anchor_entity_id = payload["reference_id"]
    now = datetime.now(UTC).replace(tzinfo=None)

    github_map = {e.id: e for e in github_events}
    jira_map = {e.id: e for e in jira_events}

    new_correlations: list[ReliabilityCorrelation] = []

    if service_result:
        new_correlations.extend(
            _map_matches(
                service_result.biztech_github,
                "biztech_github",
                anchor_entity_id,
                "SERVICE_MATCH",
                github_map,
            )
        )
        new_correlations.extend(
            _map_matches(
                service_result.jira,
                "jira",
                anchor_entity_id,
                "SERVICE_MATCH",
                jira_map,
            )
        )

    if llm_result:
        new_correlations.extend(
            _map_matches(
                llm_result.biztech_github,
                "biztech_github",
                anchor_entity_id,
                "LLM",
                github_map,
            )
        )
        new_correlations.extend(
            _map_matches(
                llm_result.jira,
                "jira",
                anchor_entity_id,
                "LLM",
                jira_map,
            )
        )

    # Merge: new correlations overwrite existing ones by entity_id
    new_entity_ids = {c.entity_id for c in new_correlations}
    merged = list(new_correlations)
    for existing in existing_correlations:
        if existing.entity_id not in new_entity_ids:
            merged.append(existing)

    # Compute group fields from the full merged set
    start_time, end_time, base_score, final_score = (
        _compute_group_from_all_correlations(merged, group_result.scoring_version or "")
    )

    group = ReliabilityCorrelationGroup(
        anchor_entity_id=anchor_entity_id,
        anchor_type="incident",
        services=payload.get("affected_services"),
        start_time=start_time,
        end_time=end_time,
        correlation_timestamp=now,
        base_score=base_score,
        final_score=final_score,
        scoring_version=group_result.scoring_version,
    )

    return group, new_correlations


async def _publish_to_scribe(
    sqs_client: Any,
    scribe_queue_url: str,
    group: ReliabilityCorrelationGroup,
    correlations: list[ReliabilityCorrelation],
    publisher_metrics: SQSPublisherMetrics | None = None,
) -> None:
    """Publish correlation results to Scribe via SQS for database persistence.

    Sends one CorrelationGroupBaseMessage followed by one CorrelationBaseMessage
    per correlation. Raises on SQS errors so the caller can retry.
    """
    queue_name = scribe_queue_url.split("/")[-1]

    group_msg = CorrelationGroupBaseMessage(data=group.model_dump(mode="json"))
    record_group = (
        publisher_metrics.start_publish(queue_name, "CorrelationGroupBaseMessage")
        if publisher_metrics
        else None
    )
    try:
        await asyncio.to_thread(
            sqs_client.send_message,
            QueueUrl=scribe_queue_url,
            MessageBody=group_msg.model_dump_json(),
        )
        if record_group:
            record_group(True, None)
    except Exception as err:
        if record_group:
            record_group(False, err)
        raise
    logger.info(
        "published correlation group to scribe",
        anchor_entity_id=group.anchor_entity_id,
    )

    for correlation in correlations:
        corr_msg = CorrelationBaseMessage(data=correlation.model_dump(mode="json"))
        record_corr = (
            publisher_metrics.start_publish(queue_name, "CorrelationBaseMessage")
            if publisher_metrics
            else None
        )
        try:
            await asyncio.to_thread(
                sqs_client.send_message,
                QueueUrl=scribe_queue_url,
                MessageBody=corr_msg.model_dump_json(),
            )
            if record_corr:
                record_corr(True, None)
        except Exception as err:
            if record_corr:
                record_corr(False, err)
            raise

    if correlations:
        logger.info(
            "published correlation events to scribe",
            anchor_entity_id=group.anchor_entity_id,
            count=len(correlations),
        )


async def run_correlation(
    payload: dict[str, Any],
    llm_client: Any,
    api_client: httpx.AsyncClient | None,
    config: MatikConfig | None,
    sqs_client: Any | None = None,
    correlation_metrics: CorrelationMetrics | None = None,
    publisher_metrics: SQSPublisherMetrics | None = None,
) -> None:
    """Run the reliability correlation graph for a single incident.

    Raises on failure so the caller can decide whether to delete the
    SQS message (success) or leave it for retry (failure).

    Args:
        payload: SQS message payload with incident fields
        llm_client: Facade or Bedrock LLM client for semantic correlation
        api_client: HTTP client for data fetching and persistence
        config: Full Matik configuration
    """
    logger.info("processing correlation", reference_id=payload.get("reference_id"))

    created_at_raw = payload.get("created_at")
    entity_created_at: datetime | None = None
    if created_at_raw:
        with contextlib.suppress(ValueError):
            entity_created_at = datetime.fromisoformat(created_at_raw)

    with traced_llm_operation(
        "enigmatologist_correlation_run",
        source="enigmatologist",
        entity_id={"incident_id": payload.get("reference_id", "")},
        entity_created_at=entity_created_at,
    ):
        graph = build_graph()

        state: IncidentCorrelationState = {
            "incident_id": payload.get("reference_id", ""),
            "incident_description": payload.get("description_summary", ""),
            "incident_channel_summary": payload.get("incident_channel_summary"),
            "incident_created_at": payload.get("created_at", ""),
            "incident_affected_services": payload.get("affected_services", []),
            "jira_time_field": "tcmr_planned_start_date",
            "github_time_field": "merged_at",
            "jira_events": [],
            "github_events": [],
            "service_correlation_result": None,
            "llm_correlation_result": None,
            "correlation_group": None,
        }

        record_duration = (
            correlation_metrics.start_run() if correlation_metrics else None
        )
        try:
            result = await graph.ainvoke(
                state,
                config={
                    "configurable": {
                        "llm_client": llm_client,
                        "enigmatologist_config": (
                            config.enigmatologist if config else None
                        ),
                        "api_base_url": (
                            str(api_client.base_url).rstrip("/") if api_client else None
                        ),
                        # Threaded through the generic `configurable` dict, not a
                        # scoped client, because these fetch nodes call the API
                        # via raw httpx rather than MatikApiClient (see
                        # nodes.py::_signature_headers). Any new fetch node must
                        # remember to read this and sign its own requests — it
                        # won't come for free. Tracked for consolidation onto
                        # MatikApiClient in a follow-up (access-posture doc,
                        # Decisions still open).
                        "api_service_secret": (
                            config.api.service_secret if config and config.api else None
                        ),
                        "correlation_metrics": correlation_metrics,
                    }
                },
            )
        except Exception:
            if record_duration:
                record_duration("error")
            raise

        logger.info(
            "correlation completed",
            reference_id=payload.get("reference_id"),
            incident_affected_services=payload.get("affected_services"),
            incident_description=payload.get("description_summary"),
            incident_channel_summary=payload.get("incident_channel_summary"),
            jira_events=[
                (e.id, e.description, e.services) for e in result.get("jira_events", [])
            ],
            github_events=[(e.id, e.services) for e in result.get("github_events", [])],
            service_correlation_result=result.get("service_correlation_result"),
            llm_correlation_result=result.get("llm_correlation_result"),
            correlation_group=result.get("correlation_group"),
        )

        # Record correlation metrics
        if correlation_metrics:
            github_events: list[ChangeEvent] = result.get("github_events", [])
            jira_events: list[ChangeEvent] = result.get("jira_events", [])
            service_result: ServiceCorrelationResult | None = result.get(
                "service_correlation_result"
            )
            llm_result: LLMCorrelationResult | None = result.get(
                "llm_correlation_result"
            )

            correlation_metrics.record_candidates_evaluated(
                github_count=len(github_events),
                jira_count=len(jira_events),
            )

            service_count = 0
            llm_count = 0

            if service_result:
                service_count = len(service_result.biztech_github) + len(
                    service_result.jira
                )
                scores = [
                    m.final_score
                    for m in service_result.biztech_github
                    if m.final_score is not None
                ] + [
                    m.final_score
                    for m in service_result.jira
                    if m.final_score is not None
                ]
                if scores:
                    correlation_metrics.record_match_scores(scores, "SERVICE_MATCH")

            if llm_result:
                llm_count = len(llm_result.biztech_github) + len(llm_result.jira)
                scores = [
                    m.final_score
                    for m in llm_result.biztech_github
                    if m.final_score is not None
                ] + [
                    m.final_score for m in llm_result.jira if m.final_score is not None
                ]
                if scores:
                    correlation_metrics.record_match_scores(scores, "LLM")

            if service_count > 0 and llm_count > 0:
                outcome = "both"
            elif service_count > 0:
                outcome = "service_only"
            elif llm_count > 0:
                outcome = "llm_only"
            else:
                outcome = "none"

            correlation_metrics.record_run_outcome(outcome, service_count, llm_count)

            if record_duration:
                record_duration(outcome)
        elif record_duration:
            record_duration("none")

        scribe_queue_url = (
            config.enigmatologist.scribe_queue_url
            if config and config.enigmatologist
            else ""
        )

        group_result: CorrelationGroupResult | None = result.get("correlation_group")
        if group_result:
            if not sqs_client or not scribe_queue_url:
                raise RuntimeError(
                    "scribe SQS client or queue URL not configured — cannot persist correlation results"
                )
            existing = (
                await _fetch_existing_correlations(
                    api_client, payload["reference_id"], config
                )
                if api_client
                else []
            )
            group, correlations = _build_persistence_payload(
                payload,
                group_result,
                result.get("service_correlation_result"),
                result.get("llm_correlation_result"),
                result.get("github_events", []),
                result.get("jira_events", []),
                existing,
            )
            await _publish_to_scribe(
                sqs_client,
                scribe_queue_url,
                group,
                correlations,
                publisher_metrics=publisher_metrics,
            )
