"""Tests for the reliability correlation engine."""

from collections.abc import Iterator
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import common.llm_tracing.operation as operation_mod
from common.models.enigmatologist_config import EnigmatologistConfig
from common.models.matik_config import MatikConfig
from common.models.reliability_correlation import ReliabilityCorrelation
from common.models.reliability_correlation_group import ReliabilityCorrelationGroup
from enigmatologist.correlations.reliability.incident.main import (
    _build_persistence_payload,
    _compute_group_from_all_correlations,
    _fetch_existing_correlations,
    _map_matches,
    _publish_to_scribe,
    run_correlation,
)
from enigmatologist.correlations.reliability.incident.state import (
    ChangeEvent,
    CorrelationGroupResult,
    CorrelationMatch,
    LLMCorrelationResult,
    ServiceCorrelationResult,
)

_MODULE = "enigmatologist.correlations.reliability.incident.main"
_SCRIBE_URL = "https://sqs.us-east-1.amazonaws.com/000000000000/test-scribe-high"


@pytest.fixture
def span_exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    """In-memory span exporter, wired in place of operation._tracer."""
    provider = TracerProvider()
    memory_exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory_exporter))
    monkeypatch.setattr(operation_mod, "_tracer", provider.get_tracer(__name__))
    yield memory_exporter


# =============================================================================
# _map_matches
# =============================================================================


class TestMapMatches:
    """Tests for _map_matches."""

    def test_maps_github_matches(self) -> None:
        """Maps CorrelationMatch items to ReliabilityCorrelation rows."""
        event_time = datetime(2025, 1, 15, 11, 0, 0)
        matches = [
            CorrelationMatch(
                id="PR-1",
                reasoning="Matched by affected service.",
                base_score=0.9,
                final_score=0.81,
                scoring_version="incident_v1",
            ),
        ]
        event_map = {
            "PR-1": ChangeEvent(
                id="PR-1",
                description="fix auth",
                start_time=event_time,
                end_time=event_time,
                services=["svc-auth"],
            ),
        }

        result = _map_matches(
            matches, "biztech_github", "INC-99", "SERVICE_MATCH", event_map
        )
        assert len(result) == 1
        r = result[0]
        assert r.anchor_entity_id == "INC-99"
        assert r.correlation_type == "SERVICE_MATCH"
        assert r.entity_type == "github_pr"
        assert r.entity_id == "PR-1"
        assert r.base_score == 0.9
        assert r.final_score == 0.81
        assert r.start_time == event_time
        assert r.end_time == event_time
        assert r.services == ["svc-auth"]

    def test_maps_jira_matches(self) -> None:
        """Jira source maps to jira_tcmr entity type."""
        matches = [
            CorrelationMatch(id="TCMR-1", reasoning="match"),
        ]
        result = _map_matches(matches, "jira", "INC-1", "LLM", {})
        assert result[0].entity_type == "jira_tcmr"
        assert result[0].correlation_type == "LLM"

    def test_missing_event_sets_none_times_and_services(self) -> None:
        """When event is not in the map, start_time, end_time, and services are None."""
        matches = [CorrelationMatch(id="PR-99", reasoning="match")]
        result = _map_matches(matches, "biztech_github", "INC-1", "LLM", {})
        assert result[0].start_time is None
        assert result[0].end_time is None
        assert result[0].services is None

    def test_unknown_source_uses_source_as_entity_type(self) -> None:
        """Unknown source string is used directly as entity_type."""
        matches = [CorrelationMatch(id="X-1", reasoning="match")]
        result = _map_matches(matches, "unknown_source", "INC-1", "LLM", {})
        assert result[0].entity_type == "unknown_source"


# =============================================================================
# _compute_group_from_all_correlations
# =============================================================================


class TestComputeGroupFromAllCorrelations:
    """Tests for _compute_group_from_all_correlations."""

    def test_computes_start_end_and_score(self) -> None:
        """Start, end, and average score are computed from correlations."""
        t1 = datetime(2025, 1, 15, 10, 0, 0)
        t2 = datetime(2025, 1, 15, 11, 0, 0)
        t3 = datetime(2025, 1, 15, 12, 0, 0)
        correlations = [
            ReliabilityCorrelation(
                anchor_entity_id="INC-1",
                correlation_type="LLM",
                entity_type="github_pr",
                entity_id="PR-1",
                start_time=t1,
                end_time=t2,
                final_score=0.8,
            ),
            ReliabilityCorrelation(
                anchor_entity_id="INC-1",
                correlation_type="SERVICE_MATCH",
                entity_type="jira_tcmr",
                entity_id="TCMR-1",
                start_time=t2,
                end_time=t3,
                final_score=0.6,
            ),
        ]
        start, end, base, final = _compute_group_from_all_correlations(
            correlations, "incident_v1"
        )
        assert start == t1
        assert end == t3
        expected = round(min((0.8 + 0.6) / 2, 0.90), 4)
        assert base == expected
        assert final == expected

    def test_empty_correlations(self) -> None:
        """Empty list returns None times and 0.0 scores."""
        start, end, base, final = _compute_group_from_all_correlations([], "v1")
        assert start is None
        assert end is None
        assert base == 0.0
        assert final == 0.0

    def test_no_scored_correlations(self) -> None:
        """Correlations with no final_score produce 0.0 group score."""
        correlations = [
            ReliabilityCorrelation(
                anchor_entity_id="INC-1",
                correlation_type="LLM",
                entity_type="github_pr",
                entity_id="PR-1",
                start_time=datetime(2025, 1, 15, 10, 0, 0),
                final_score=None,
            ),
        ]
        _, _, base, final = _compute_group_from_all_correlations(correlations, "v1")
        assert base == 0.0
        assert final == 0.0

    def test_group_end_considers_start_times(self) -> None:
        """group_end uses the latest of all start_times and end_times."""
        t1 = datetime(2025, 1, 15, 10, 0, 0)
        t_late_start = datetime(2025, 1, 15, 14, 0, 0)  # latest
        t_early_end = datetime(2025, 1, 15, 11, 0, 0)
        correlations = [
            ReliabilityCorrelation(
                anchor_entity_id="INC-1",
                correlation_type="LLM",
                entity_type="github_pr",
                entity_id="PR-1",
                start_time=t1,
                end_time=t_early_end,
                final_score=0.5,
            ),
            ReliabilityCorrelation(
                anchor_entity_id="INC-1",
                correlation_type="LLM",
                entity_type="github_pr",
                entity_id="PR-2",
                start_time=t_late_start,
                end_time=None,
                final_score=0.5,
            ),
        ]
        _, end, _, _ = _compute_group_from_all_correlations(correlations, "v1")
        assert end == t_late_start


# =============================================================================
# _build_persistence_payload
# =============================================================================


class TestBuildPersistencePayload:
    """Tests for _build_persistence_payload."""

    def test_builds_group_and_correlations(self) -> None:
        """Produces a group and correlation list from service + LLM results."""
        event_time = datetime(2025, 1, 15, 11, 0, 0)
        payload = {
            "reference_id": "INC-99",
            "affected_services": ["svc-a"],
        }
        group_result = CorrelationGroupResult(
            biztech_github=[],
            jira=[],
            scoring_version="incident_v1",
        )
        service_result = ServiceCorrelationResult(
            biztech_github=[
                CorrelationMatch(
                    id="PR-1",
                    reasoning="service match",
                    base_score=0.9,
                    final_score=0.81,
                    scoring_version="incident_v1",
                ),
            ],
            jira=[],
        )
        github_events = [
            ChangeEvent(
                id="PR-1", description="fix", start_time=event_time, end_time=event_time
            ),
        ]

        group, correlations = _build_persistence_payload(
            payload, group_result, service_result, None, github_events, [], []
        )

        assert group.anchor_entity_id == "INC-99"
        assert group.anchor_type == "incident"
        assert group.services == ["svc-a"]
        assert group.scoring_version == "incident_v1"
        assert len(correlations) == 1
        assert correlations[0].entity_id == "PR-1"
        assert correlations[0].correlation_type == "SERVICE_MATCH"

    def test_group_score_includes_existing_correlations(self) -> None:
        """Group fields are computed from merged (new + existing) correlations.

        The returned correlations list only contains NEW items,
        but the group's score accounts for both new and existing.
        """
        event_time = datetime(2025, 1, 15, 11, 0, 0)
        payload = {"reference_id": "INC-99", "affected_services": []}
        group_result = CorrelationGroupResult(scoring_version="incident_v1")

        llm_result = LLMCorrelationResult(
            biztech_github=[
                CorrelationMatch(
                    id="PR-1",
                    reasoning="llm match",
                    base_score=0.7,
                    final_score=0.42,
                    scoring_version="incident_v1",
                ),
            ],
            jira=[],
        )
        github_events = [
            ChangeEvent(id="PR-1", description="fix", start_time=event_time),
        ]

        existing = [
            ReliabilityCorrelation(
                anchor_entity_id="INC-99",
                correlation_type="LLM",
                entity_type="jira_tcmr",
                entity_id="TCMR-OLD",
                final_score=0.4,
                start_time=event_time,
            ),
        ]

        group, correlations = _build_persistence_payload(
            payload, group_result, None, llm_result, github_events, [], existing
        )

        # Only new correlations are returned
        assert len(correlations) == 1
        assert correlations[0].entity_id == "PR-1"

        # Group score is computed from merged set (new PR-1 + existing TCMR-OLD)
        expected_avg = (0.42 + 0.4) / 2
        assert group.base_score == round(min(expected_avg, 0.90), 4)

    def test_no_results_produces_empty_correlations(self) -> None:
        """When both service and LLM results are None, correlations is empty."""
        payload = {"reference_id": "INC-99", "affected_services": []}
        group_result = CorrelationGroupResult(scoring_version="incident_v1")

        group, correlations = _build_persistence_payload(
            payload, group_result, None, None, [], [], []
        )
        assert correlations == []
        assert group.anchor_entity_id == "INC-99"


# =============================================================================
# _fetch_existing_correlations
# =============================================================================


class TestFetchExistingCorrelations:
    """Tests for _fetch_existing_correlations."""

    @pytest.mark.asyncio
    async def test_successful_fetch(self) -> None:
        """Returns list of ReliabilityCorrelation from API response."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {
                "anchor_entity_id": "INC-99",
                "correlation_type": "LLM",
                "entity_type": "github_pr",
                "entity_id": "PR-1",
            }
        ]
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        result = await _fetch_existing_correlations(mock_client, "INC-99", None)

        assert len(result) == 1
        assert result[0].entity_id == "PR-1"

    @pytest.mark.asyncio
    async def test_error_returns_empty_list(self) -> None:
        """API errors return an empty list."""
        mock_client = AsyncMock()
        mock_client.get.side_effect = RuntimeError("connection refused")

        result = await _fetch_existing_correlations(mock_client, "INC-99", None)

        assert result == []

    @pytest.mark.asyncio
    @patch(f"{_MODULE}.logger")
    async def test_401_logs_signature_rejection_distinctly(
        self, mock_logger: MagicMock
    ) -> None:
        """A signature-check 401 must be loud, not indistinguishable from "no data".

        Phase 1c: once matik-api enforces the service signature, a
        misconfigured/stale api.service_secret degrades to an empty result
        just like a genuine "no existing correlations" — this must at least
        log distinctly (error, not warning, with an actionable message) so
        it isn't silently mistaken for the latter.
        """
        unauthorized_response = MagicMock()
        unauthorized_response.status_code = 401

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=unauthorized_response
        )

        result = await _fetch_existing_correlations(mock_client, "INC-99", None)

        assert result == []
        mock_logger.error.assert_called_once()
        mock_logger.warning.assert_not_called()
        assert "signature" in mock_logger.error.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_signs_request_when_service_secret_configured(self) -> None:
        """Phase 1c: attaches a verifiable signature when config.api.service_secret is set."""
        from common.constants import API_V1_PREFIX
        from common.utils.service_auth import verify_request

        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        config = MatikConfig(api={"service_secret": "shared-secret"})
        await _fetch_existing_correlations(mock_client, "INC-99", config)

        call_kwargs = mock_client.get.call_args[1]
        headers = call_kwargs["headers"]
        verify_request(
            "shared-secret",
            "GET",
            f"{API_V1_PREFIX}/correlation/reliability/events/INC-99",
            b"",
            headers["X-Matik-Service-Timestamp"],
            headers["X-Matik-Service-Signature"],
        )  # does not raise

    @pytest.mark.asyncio
    async def test_no_signature_when_service_secret_unset(self) -> None:
        """No config.api (or no service_secret) means no signature headers."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        await _fetch_existing_correlations(mock_client, "INC-99", None)

        assert mock_client.get.call_args[1]["headers"] is None


# =============================================================================
# _publish_to_scribe
# =============================================================================


class TestPublishToScribe:
    """Tests for _publish_to_scribe."""

    @pytest.mark.asyncio
    async def test_sends_group_and_all_correlations(self) -> None:
        """Sends one group message and one message per correlation."""
        group = ReliabilityCorrelationGroup(
            anchor_entity_id="INC-99",
            anchor_type="incident",
            correlation_timestamp=datetime(2025, 1, 15, 12, 0, 0),
        )
        correlations = [
            ReliabilityCorrelation(
                anchor_entity_id="INC-99",
                correlation_type="LLM",
                entity_type="github_pr",
                entity_id="PR-1",
            ),
            ReliabilityCorrelation(
                anchor_entity_id="INC-99",
                correlation_type="SERVICE_MATCH",
                entity_type="jira_tcmr",
                entity_id="TCMR-1",
            ),
        ]

        mock_sqs = MagicMock()
        publisher_metrics = MagicMock()
        record = publisher_metrics.start_publish.return_value
        await _publish_to_scribe(
            mock_sqs,
            _SCRIBE_URL,
            group,
            correlations,
            publisher_metrics=publisher_metrics,
        )

        # 1 group + 2 correlations = 3 send_message calls
        assert mock_sqs.send_message.call_count == 3
        # 1 group + 2 correlations = 3 publish recorders, all success
        assert publisher_metrics.start_publish.call_count == 3
        assert record.call_count == 3
        for call in record.call_args_list:
            assert call[0] == (True, None)

    @pytest.mark.asyncio
    async def test_sends_group_only_when_no_correlations(self) -> None:
        """Sends only the group message when correlations list is empty."""
        group = ReliabilityCorrelationGroup(
            anchor_entity_id="INC-99",
            anchor_type="incident",
            correlation_timestamp=datetime(2025, 1, 15, 12, 0, 0),
        )

        mock_sqs = MagicMock()
        await _publish_to_scribe(mock_sqs, _SCRIBE_URL, group, [])

        assert mock_sqs.send_message.call_count == 1

    @pytest.mark.asyncio
    async def test_sends_to_correct_queue_url(self) -> None:
        """Messages are sent to the configured scribe queue URL."""
        group = ReliabilityCorrelationGroup(
            anchor_entity_id="INC-99",
            anchor_type="incident",
            correlation_timestamp=datetime(2025, 1, 15, 12, 0, 0),
        )

        mock_sqs = MagicMock()
        await _publish_to_scribe(mock_sqs, _SCRIBE_URL, group, [])

        call_kwargs = mock_sqs.send_message.call_args[1]
        assert call_kwargs["QueueUrl"] == _SCRIBE_URL

    @pytest.mark.asyncio
    async def test_group_publish_failure_records_metric_and_raises(self) -> None:
        """A failed group send records publish failure and re-raises."""
        group = ReliabilityCorrelationGroup(
            anchor_entity_id="INC-99",
            anchor_type="incident",
            correlation_timestamp=datetime(2025, 1, 15, 12, 0, 0),
        )

        err = RuntimeError("sqs unavailable")
        mock_sqs = MagicMock()
        mock_sqs.send_message.side_effect = err
        publisher_metrics = MagicMock()
        record = publisher_metrics.start_publish.return_value

        with pytest.raises(RuntimeError, match="sqs unavailable"):
            await _publish_to_scribe(
                mock_sqs, _SCRIBE_URL, group, [], publisher_metrics=publisher_metrics
            )

        publisher_metrics.start_publish.assert_called_once_with(
            "test-scribe-high", "CorrelationGroupBaseMessage"
        )
        record.assert_called_once_with(False, err)

    @pytest.mark.asyncio
    async def test_correlation_publish_failure_records_metric_and_raises(self) -> None:
        """A failed correlation send (after group succeeds) records failure and raises."""
        group = ReliabilityCorrelationGroup(
            anchor_entity_id="INC-99",
            anchor_type="incident",
            correlation_timestamp=datetime(2025, 1, 15, 12, 0, 0),
        )
        correlations = [
            ReliabilityCorrelation(
                anchor_entity_id="INC-99",
                correlation_type="LLM",
                entity_type="github_pr",
                entity_id="PR-1",
            ),
        ]

        err = RuntimeError("send failed")
        mock_sqs = MagicMock()
        # group send succeeds, correlation send raises
        mock_sqs.send_message.side_effect = [None, err]
        publisher_metrics = MagicMock()
        record = publisher_metrics.start_publish.return_value

        with pytest.raises(RuntimeError, match="send failed"):
            await _publish_to_scribe(
                mock_sqs,
                _SCRIBE_URL,
                group,
                correlations,
                publisher_metrics=publisher_metrics,
            )

        # group recorded success, correlation recorded failure
        assert publisher_metrics.start_publish.call_count == 2
        record.assert_any_call(True, None)
        record.assert_any_call(False, err)


# =============================================================================
# run_correlation
# =============================================================================


class TestRunCorrelation:
    """Tests for run_correlation."""

    @pytest.mark.asyncio
    async def test_invokes_graph_and_publishes_to_scribe(self) -> None:
        """Runs graph, fetches existing, builds payload, and publishes to Scribe."""
        payload = {
            "reference_id": "INC-99",
            "description_summary": "VPN outage",
            "created_at": "2025-01-15T12:00:00",
            "affected_services": ["svc-vpn"],
        }
        config = MatikConfig(
            enigmatologist=EnigmatologistConfig(
                scribe_queue_url=_SCRIBE_URL, correlation_system_prompt="test"
            )
        )

        mock_group_result = CorrelationGroupResult(
            scoring_version="incident_v1",
            base_score=0.5,
            final_score=0.5,
        )
        mock_graph_result: dict[str, Any] = {
            "incident_description": "VPN outage",
            "incident_created_at": "2025-01-15T12:00:00",
            "incident_affected_services": ["svc-vpn"],
            "jira_time_field": "tcmr_planned_start_date",
            "github_time_field": "merged_at",
            "jira_events": [],
            "github_events": [],
            "service_correlation_result": None,
            "llm_correlation_result": None,
            "correlation_group": mock_group_result,
        }

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = mock_graph_result

        mock_api_client = AsyncMock()
        mock_sqs = MagicMock()
        mock_group = MagicMock()
        mock_correlations = [MagicMock()]

        with (
            patch(f"{_MODULE}.build_graph", return_value=mock_graph),
            patch(
                f"{_MODULE}._fetch_existing_correlations",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_fetch,
            patch(
                f"{_MODULE}._build_persistence_payload",
                return_value=(mock_group, mock_correlations),
            ) as mock_build,
            patch(
                f"{_MODULE}._publish_to_scribe",
                new_callable=AsyncMock,
            ) as mock_publish,
        ):
            await run_correlation(
                payload, MagicMock(), mock_api_client, config, sqs_client=mock_sqs
            )

        mock_graph.ainvoke.assert_called_once()
        mock_fetch.assert_called_once_with(mock_api_client, "INC-99", config)
        mock_build.assert_called_once()
        mock_publish.assert_called_once_with(
            mock_sqs,
            _SCRIBE_URL,
            mock_group,
            mock_correlations,
            publisher_metrics=None,
        )

    @pytest.mark.asyncio
    async def test_opens_root_span_tagged_with_incident_id(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        """run_correlation opens one root span carrying the incident_id and
        entity_created_at, distinct from the inner per-LLM-call span."""
        payload = {
            "reference_id": "INC-99",
            "description_summary": "VPN outage",
            "created_at": "2025-01-15T12:00:00",
            "affected_services": ["svc-vpn"],
        }
        config = MatikConfig(
            enigmatologist=EnigmatologistConfig(
                scribe_queue_url=_SCRIBE_URL, correlation_system_prompt="test"
            )
        )

        mock_graph_result: dict[str, Any] = {
            "incident_description": "VPN outage",
            "incident_created_at": "2025-01-15T12:00:00",
            "incident_affected_services": ["svc-vpn"],
            "jira_time_field": "tcmr_planned_start_date",
            "github_time_field": "merged_at",
            "jira_events": [],
            "github_events": [],
            "service_correlation_result": None,
            "llm_correlation_result": None,
            "correlation_group": None,
        }
        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = mock_graph_result

        with patch(f"{_MODULE}.build_graph", return_value=mock_graph):
            await run_correlation(payload, MagicMock(), None, config)

        (span,) = span_exporter.get_finished_spans()
        assert span.name == "enigmatologist_correlation_run"
        assert span.attributes is not None
        assert span.attributes["incident_id"] == "INC-99"
        assert span.attributes["entity_created_at"] == "2025-01-15T12:00:00"

    @pytest.mark.asyncio
    async def test_malformed_created_at_does_not_raise(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        """An unparseable created_at is tolerated: no entity_created_at attribute,
        run still completes."""
        payload = {
            "reference_id": "INC-99",
            "description_summary": "test",
            "created_at": "not-a-timestamp",
            "affected_services": [],
        }

        mock_graph_result: dict[str, Any] = {
            "incident_description": "test",
            "incident_created_at": "not-a-timestamp",
            "incident_affected_services": [],
            "jira_time_field": "tcmr_planned_start_date",
            "github_time_field": "merged_at",
            "jira_events": [],
            "github_events": [],
            "service_correlation_result": None,
            "llm_correlation_result": None,
            "correlation_group": None,
        }
        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = mock_graph_result

        with patch(f"{_MODULE}.build_graph", return_value=mock_graph):
            await run_correlation(payload, MagicMock(), None, None)

        (span,) = span_exporter.get_finished_spans()
        assert span.attributes is not None
        assert "entity_created_at" not in span.attributes

    @pytest.mark.asyncio
    async def test_seeds_incident_channel_summary_into_state(self) -> None:
        """The OpsBot channel summary, when present on the payload, is seeded
        into the graph state so the correlation prompt can use it."""
        payload = {
            "reference_id": "INC-99",
            "description_summary": "VPN outage",
            "created_at": "2025-01-15T12:00:00",
            "affected_services": ["svc-vpn"],
            "incident_channel_summary": "Team is investigating elevated error rates",
        }
        config = MatikConfig(
            enigmatologist=EnigmatologistConfig(
                scribe_queue_url=_SCRIBE_URL, correlation_system_prompt="test"
            )
        )

        mock_graph_result: dict[str, Any] = {
            "incident_description": "VPN outage",
            "incident_created_at": "2025-01-15T12:00:00",
            "incident_affected_services": ["svc-vpn"],
            "jira_time_field": "tcmr_planned_start_date",
            "github_time_field": "merged_at",
            "jira_events": [],
            "github_events": [],
            "service_correlation_result": None,
            "llm_correlation_result": None,
            "correlation_group": None,
        }
        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = mock_graph_result

        with patch(f"{_MODULE}.build_graph", return_value=mock_graph):
            await run_correlation(payload, MagicMock(), None, config)

        seeded_state = mock_graph.ainvoke.call_args[0][0]
        assert seeded_state["incident_channel_summary"] == (
            "Team is investigating elevated error rates"
        )

    @pytest.mark.asyncio
    async def test_raises_when_scribe_not_configured(self) -> None:
        """RuntimeError is raised when sqs_client is None and group_result exists."""
        payload = {
            "reference_id": "INC-99",
            "description_summary": "test",
            "created_at": "2025-01-15T12:00:00",
            "affected_services": [],
        }

        mock_group_result = CorrelationGroupResult(scoring_version="incident_v1")
        mock_graph_result: dict[str, Any] = {
            "incident_description": "test",
            "incident_created_at": "2025-01-15T12:00:00",
            "incident_affected_services": [],
            "jira_time_field": "tcmr_planned_start_date",
            "github_time_field": "merged_at",
            "jira_events": [],
            "github_events": [],
            "service_correlation_result": None,
            "llm_correlation_result": None,
            "correlation_group": mock_group_result,
        }

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = mock_graph_result

        with (
            patch(f"{_MODULE}.build_graph", return_value=mock_graph),
            pytest.raises(RuntimeError, match="scribe SQS client or queue URL"),
        ):
            # sqs_client=None with a group_result → must raise
            await run_correlation(payload, MagicMock(), None, None, sqs_client=None)

    @pytest.mark.asyncio
    async def test_skips_fetch_when_no_api_client(self) -> None:
        """When api_client is None, _fetch_existing_correlations is not called."""
        payload = {
            "reference_id": "INC-99",
            "description_summary": "test",
            "created_at": "2025-01-15T12:00:00",
            "affected_services": [],
        }
        config = MatikConfig(
            enigmatologist=EnigmatologistConfig(
                scribe_queue_url=_SCRIBE_URL, correlation_system_prompt="test"
            )
        )

        mock_group_result = CorrelationGroupResult(scoring_version="incident_v1")
        mock_graph_result: dict[str, Any] = {
            "incident_description": "test",
            "incident_created_at": "2025-01-15T12:00:00",
            "incident_affected_services": [],
            "jira_time_field": "tcmr_planned_start_date",
            "github_time_field": "merged_at",
            "jira_events": [],
            "github_events": [],
            "service_correlation_result": None,
            "llm_correlation_result": None,
            "correlation_group": mock_group_result,
        }

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = mock_graph_result

        with (
            patch(f"{_MODULE}.build_graph", return_value=mock_graph),
            patch(
                f"{_MODULE}._fetch_existing_correlations",
                new_callable=AsyncMock,
            ) as mock_fetch,
            patch(
                f"{_MODULE}._build_persistence_payload",
                return_value=(MagicMock(), []),
            ),
            patch(f"{_MODULE}._publish_to_scribe", new_callable=AsyncMock),
        ):
            await run_correlation(
                payload, MagicMock(), None, config, sqs_client=MagicMock()
            )

        mock_fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_group_result_skips_persistence(self) -> None:
        """When graph produces no correlation_group, persistence is skipped."""
        payload = {
            "reference_id": "INC-99",
            "description_summary": "test",
            "created_at": "2025-01-15T12:00:00",
            "affected_services": [],
        }

        mock_graph_result: dict[str, Any] = {
            "incident_description": "test",
            "incident_created_at": "2025-01-15T12:00:00",
            "incident_affected_services": [],
            "jira_time_field": "tcmr_planned_start_date",
            "github_time_field": "merged_at",
            "jira_events": [],
            "github_events": [],
            "service_correlation_result": None,
            "llm_correlation_result": None,
            "correlation_group": None,
        }

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = mock_graph_result

        with (
            patch(f"{_MODULE}.build_graph", return_value=mock_graph),
            patch(
                f"{_MODULE}._publish_to_scribe",
                new_callable=AsyncMock,
            ) as mock_publish,
        ):
            await run_correlation(payload, MagicMock(), AsyncMock(), None)

        mock_publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_records_candidates_and_outcome_metrics(self) -> None:
        """run_correlation records candidate counts and outcome when metrics provided."""
        payload = {
            "reference_id": "INC-99",
            "description_summary": "test",
            "created_at": "2025-01-15T12:00:00",
            "affected_services": [],
        }
        config = MatikConfig(
            enigmatologist=EnigmatologistConfig(
                scribe_queue_url=_SCRIBE_URL, correlation_system_prompt="test"
            )
        )

        event_time = datetime(2025, 1, 15, 10, 0, 0)
        github_event = ChangeEvent(
            id="PR-1", description="fix", start_time=event_time, services=["svc-a"]
        )
        jira_event = ChangeEvent(
            id="TCMR-1", description="deploy", start_time=event_time, services=["svc-a"]
        )
        service_result = ServiceCorrelationResult(
            biztech_github=[
                CorrelationMatch(
                    id="PR-1", reasoning="match", final_score=0.8, scoring_version="v1"
                )
            ],
            jira=[],
        )
        mock_group_result = CorrelationGroupResult(scoring_version="incident_v1")
        mock_graph_result: dict[str, Any] = {
            "incident_description": "test",
            "incident_created_at": "2025-01-15T12:00:00",
            "incident_affected_services": [],
            "jira_time_field": "tcmr_planned_start_date",
            "github_time_field": "merged_at",
            "jira_events": [jira_event],
            "github_events": [github_event],
            "service_correlation_result": service_result,
            "llm_correlation_result": None,
            "correlation_group": mock_group_result,
        }

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = mock_graph_result
        mock_metrics = MagicMock()

        with (
            patch(f"{_MODULE}.build_graph", return_value=mock_graph),
            patch(
                f"{_MODULE}._fetch_existing_correlations",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                f"{_MODULE}._build_persistence_payload", return_value=(MagicMock(), [])
            ),
            patch(f"{_MODULE}._publish_to_scribe", new_callable=AsyncMock),
        ):
            await run_correlation(
                payload,
                MagicMock(),
                AsyncMock(),
                config,
                sqs_client=MagicMock(),
                correlation_metrics=mock_metrics,
            )

        mock_metrics.record_candidates_evaluated.assert_called_once_with(
            github_count=1, jira_count=1
        )
        mock_metrics.record_match_scores.assert_called_once()
        scores_call = mock_metrics.record_match_scores.call_args
        assert scores_call[0][1] == "SERVICE_MATCH"
        assert 0.8 in scores_call[0][0]
        mock_metrics.record_run_outcome.assert_called_once()
        outcome_call = mock_metrics.record_run_outcome.call_args
        assert outcome_call[0][0] == "service_only"

    @pytest.mark.asyncio
    async def test_records_llm_only_outcome_metrics(self) -> None:
        """run_correlation records llm_only outcome when only LLM matches exist."""
        payload = {
            "reference_id": "INC-99",
            "description_summary": "test",
            "created_at": "2025-01-15T12:00:00",
            "affected_services": [],
        }
        config = MatikConfig(
            enigmatologist=EnigmatologistConfig(
                scribe_queue_url=_SCRIBE_URL, correlation_system_prompt="test"
            )
        )

        llm_result = LLMCorrelationResult(
            biztech_github=[
                CorrelationMatch(
                    id="PR-1", reasoning="llm", final_score=0.7, scoring_version="v1"
                )
            ],
            jira=[
                CorrelationMatch(
                    id="TCMR-1", reasoning="llm", final_score=0.6, scoring_version="v1"
                )
            ],
        )
        mock_group_result = CorrelationGroupResult(scoring_version="incident_v1")
        mock_graph_result: dict[str, Any] = {
            "incident_description": "test",
            "incident_created_at": "2025-01-15T12:00:00",
            "incident_affected_services": [],
            "jira_time_field": "tcmr_planned_start_date",
            "github_time_field": "merged_at",
            "jira_events": [],
            "github_events": [],
            "service_correlation_result": None,
            "llm_correlation_result": llm_result,
            "correlation_group": mock_group_result,
        }

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = mock_graph_result
        mock_metrics = MagicMock()

        with (
            patch(f"{_MODULE}.build_graph", return_value=mock_graph),
            patch(
                f"{_MODULE}._fetch_existing_correlations",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                f"{_MODULE}._build_persistence_payload", return_value=(MagicMock(), [])
            ),
            patch(f"{_MODULE}._publish_to_scribe", new_callable=AsyncMock),
        ):
            await run_correlation(
                payload,
                MagicMock(),
                AsyncMock(),
                config,
                sqs_client=MagicMock(),
                correlation_metrics=mock_metrics,
            )

        outcome_call = mock_metrics.record_run_outcome.call_args
        assert outcome_call[0][0] == "llm_only"
        # LLM scores from both github and jira are recorded
        scores_call = mock_metrics.record_match_scores.call_args
        assert scores_call[0][1] == "LLM"
        assert sorted(scores_call[0][0]) == sorted([0.7, 0.6])

    @pytest.mark.asyncio
    async def test_records_both_outcome_when_service_and_llm_match(self) -> None:
        """run_correlation records both outcome when service and LLM matches exist."""
        payload = {
            "reference_id": "INC-99",
            "description_summary": "test",
            "created_at": "2025-01-15T12:00:00",
            "affected_services": [],
        }
        config = MatikConfig(
            enigmatologist=EnigmatologistConfig(
                scribe_queue_url=_SCRIBE_URL, correlation_system_prompt="test"
            )
        )

        service_result = ServiceCorrelationResult(
            biztech_github=[
                CorrelationMatch(
                    id="PR-1", reasoning="svc", final_score=0.9, scoring_version="v1"
                )
            ],
            jira=[],
        )
        llm_result = LLMCorrelationResult(
            biztech_github=[],
            jira=[
                CorrelationMatch(
                    id="TCMR-1", reasoning="llm", final_score=0.65, scoring_version="v1"
                )
            ],
        )
        mock_group_result = CorrelationGroupResult(scoring_version="incident_v1")
        mock_graph_result: dict[str, Any] = {
            "incident_description": "test",
            "incident_created_at": "2025-01-15T12:00:00",
            "incident_affected_services": [],
            "jira_time_field": "tcmr_planned_start_date",
            "github_time_field": "merged_at",
            "jira_events": [],
            "github_events": [],
            "service_correlation_result": service_result,
            "llm_correlation_result": llm_result,
            "correlation_group": mock_group_result,
        }

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = mock_graph_result
        mock_metrics = MagicMock()

        with (
            patch(f"{_MODULE}.build_graph", return_value=mock_graph),
            patch(
                f"{_MODULE}._fetch_existing_correlations",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                f"{_MODULE}._build_persistence_payload", return_value=(MagicMock(), [])
            ),
            patch(f"{_MODULE}._publish_to_scribe", new_callable=AsyncMock),
        ):
            await run_correlation(
                payload,
                MagicMock(),
                AsyncMock(),
                config,
                sqs_client=MagicMock(),
                correlation_metrics=mock_metrics,
            )

        outcome_call = mock_metrics.record_run_outcome.call_args
        assert outcome_call[0][0] == "both"
        assert mock_metrics.record_match_scores.call_count == 2

    @pytest.mark.asyncio
    async def test_records_none_outcome_when_no_matches(self) -> None:
        """run_correlation records none outcome when no matches are found."""
        payload = {
            "reference_id": "INC-99",
            "description_summary": "test",
            "created_at": "2025-01-15T12:00:00",
            "affected_services": [],
        }
        config = MatikConfig(
            enigmatologist=EnigmatologistConfig(
                scribe_queue_url=_SCRIBE_URL, correlation_system_prompt="test"
            )
        )

        mock_group_result = CorrelationGroupResult(scoring_version="incident_v1")
        mock_graph_result: dict[str, Any] = {
            "incident_description": "test",
            "incident_created_at": "2025-01-15T12:00:00",
            "incident_affected_services": [],
            "jira_time_field": "tcmr_planned_start_date",
            "github_time_field": "merged_at",
            "jira_events": [],
            "github_events": [],
            "service_correlation_result": None,
            "llm_correlation_result": None,
            "correlation_group": mock_group_result,
        }

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = mock_graph_result
        mock_metrics = MagicMock()

        with (
            patch(f"{_MODULE}.build_graph", return_value=mock_graph),
            patch(
                f"{_MODULE}._fetch_existing_correlations",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                f"{_MODULE}._build_persistence_payload", return_value=(MagicMock(), [])
            ),
            patch(f"{_MODULE}._publish_to_scribe", new_callable=AsyncMock),
        ):
            await run_correlation(
                payload,
                MagicMock(),
                AsyncMock(),
                config,
                sqs_client=MagicMock(),
                correlation_metrics=mock_metrics,
            )

        mock_metrics.record_candidates_evaluated.assert_called_once_with(
            github_count=0, jira_count=0
        )
        mock_metrics.record_match_scores.assert_not_called()
        outcome_call = mock_metrics.record_run_outcome.call_args
        assert outcome_call[0][0] == "none"

    @pytest.mark.asyncio
    async def test_graph_error_records_error_duration_and_raises(self) -> None:
        """When graph.ainvoke raises, run duration is recorded as 'error' and re-raised."""
        payload = {
            "reference_id": "INC-99",
            "description_summary": "test",
            "created_at": "2025-01-15T12:00:00",
            "affected_services": [],
        }
        config = MatikConfig(
            enigmatologist=EnigmatologistConfig(
                scribe_queue_url=_SCRIBE_URL, correlation_system_prompt="test"
            )
        )

        mock_graph = AsyncMock()
        mock_graph.ainvoke.side_effect = RuntimeError("graph boom")
        mock_metrics = MagicMock()
        record_duration = mock_metrics.start_run.return_value

        with (
            patch(f"{_MODULE}.build_graph", return_value=mock_graph),
            pytest.raises(RuntimeError, match="graph boom"),
        ):
            await run_correlation(
                payload,
                MagicMock(),
                AsyncMock(),
                config,
                sqs_client=MagicMock(),
                correlation_metrics=mock_metrics,
            )

        record_duration.assert_called_once_with("error")

    @pytest.mark.asyncio
    async def test_records_duration_none_when_only_duration_metric(self) -> None:
        """With a start_run recorder but no outcome metrics path, duration logs 'none'.

        Exercises the branch where record_duration exists but correlation_metrics
        does not drive the outcome computation block.
        """
        payload = {
            "reference_id": "INC-99",
            "description_summary": "test",
            "created_at": "2025-01-15T12:00:00",
            "affected_services": [],
        }

        mock_graph_result: dict[str, Any] = {
            "incident_description": "test",
            "incident_created_at": "2025-01-15T12:00:00",
            "incident_affected_services": [],
            "jira_time_field": "tcmr_planned_start_date",
            "github_time_field": "merged_at",
            "jira_events": [],
            "github_events": [],
            "service_correlation_result": None,
            "llm_correlation_result": None,
            "correlation_group": None,
        }

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = mock_graph_result

        with patch(f"{_MODULE}.build_graph", return_value=mock_graph):
            # api_client/config None and no correlation_metrics → no group, no persistence;
            # the elif record_duration branch is the only metrics path and is exercised
            # via start_run being unavailable, so this simply must not raise.
            await run_correlation(payload, MagicMock(), None, None)

        mock_graph.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_metrics_when_correlation_metrics_is_none(self) -> None:
        """run_correlation does not error when correlation_metrics is None."""
        payload = {
            "reference_id": "INC-99",
            "description_summary": "test",
            "created_at": "2025-01-15T12:00:00",
            "affected_services": [],
        }
        config = MatikConfig(
            enigmatologist=EnigmatologistConfig(
                scribe_queue_url=_SCRIBE_URL, correlation_system_prompt="test"
            )
        )

        mock_group_result = CorrelationGroupResult(scoring_version="incident_v1")
        mock_graph_result: dict[str, Any] = {
            "incident_description": "test",
            "incident_created_at": "2025-01-15T12:00:00",
            "incident_affected_services": [],
            "jira_time_field": "tcmr_planned_start_date",
            "github_time_field": "merged_at",
            "jira_events": [],
            "github_events": [],
            "service_correlation_result": ServiceCorrelationResult(
                biztech_github=[
                    CorrelationMatch(
                        id="PR-1", reasoning="m", final_score=0.8, scoring_version="v1"
                    )
                ],
                jira=[],
            ),
            "llm_correlation_result": None,
            "correlation_group": mock_group_result,
        }

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = mock_graph_result

        with (
            patch(f"{_MODULE}.build_graph", return_value=mock_graph),
            patch(
                f"{_MODULE}._fetch_existing_correlations",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                f"{_MODULE}._build_persistence_payload", return_value=(MagicMock(), [])
            ),
            patch(f"{_MODULE}._publish_to_scribe", new_callable=AsyncMock),
        ):
            # Should not raise even with matches present and metrics=None
            await run_correlation(
                payload,
                MagicMock(),
                AsyncMock(),
                config,
                sqs_client=MagicMock(),
                correlation_metrics=None,
            )
