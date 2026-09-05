"""Unit tests for incident correlation graph nodes."""

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from langchain_core.runnables import RunnableConfig
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import enigmatologist.correlations.reliability.incident.nodes as nodes_mod
from common.models.enigmatologist_config import EnigmatologistConfig
from enigmatologist.correlations.reliability.incident.nodes import (
    _DEFAULT_API_BASE_URL,
    _build_user_prompt,
    _has_service_overlap,
    assign_correlations_by_llm,
    assign_correlations_by_service,
    fetch_biztech_github_prs,
    fetch_jira_issues,
    has_change_events,
    is_service_exists,
    route_after_fetch,
)
from enigmatologist.correlations.reliability.incident.state import (
    ChangeEvent,
    IncidentCorrelationState,
)


@pytest.fixture
def span_exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    """In-memory span exporter, wired in place of nodes._tracer."""
    provider = TracerProvider()
    memory_exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory_exporter))
    monkeypatch.setattr(nodes_mod, "_tracer", provider.get_tracer(__name__))
    yield memory_exporter


def _make_config(
    cfg: EnigmatologistConfig | None = None,
    api_base_url: str | None = None,
    facade_client: object | None = None,
    api_service_secret: str | None = None,
) -> RunnableConfig:
    """Build a RunnableConfig-like dict for tests."""
    if cfg is None:
        cfg = EnigmatologistConfig(
            scribe_queue_url="",
            correlation_system_prompt="test",
            speculative_patterns=[],
        )
    return RunnableConfig(
        configurable={
            "enigmatologist_config": cfg,
            "api_base_url": api_base_url or "http://localhost:8080",
            "llm_client": facade_client,
            "api_service_secret": api_service_secret,
        }
    )


def _empty_state(**overrides: Any) -> IncidentCorrelationState:
    """Build a minimal state dict with optional overrides."""
    base: dict[str, Any] = {
        "incident_id": "INC-99",
        "incident_description": "test incident",
        "incident_created_at": datetime(2025, 1, 15, 12, 0, 0),
        "incident_affected_services": [],
        "jira_time_field": "created_at",
        "github_time_field": "created_at",
        "jira_events": [],
        "github_events": [],
        "service_correlation_result": None,
        "llm_correlation_result": None,
        "correlation_group": None,
    }
    base.update(overrides)
    return base  # type: ignore[return-value]


# =============================================================================
# _has_service_overlap
# =============================================================================


class TestHasServiceOverlap:
    """Tests for _has_service_overlap."""

    def test_overlap_returns_true(self) -> None:
        assert _has_service_overlap(["svc-a", "svc-b"], ["svc-b", "svc-c"]) is True

    def test_no_overlap_returns_false(self) -> None:
        assert _has_service_overlap(["svc-a"], ["svc-b"]) is False

    def test_empty_event_services_returns_false(self) -> None:
        assert _has_service_overlap([], ["svc-a"]) is False

    def test_none_event_services_returns_false(self) -> None:
        assert _has_service_overlap(None, ["svc-a"]) is False

    def test_empty_incident_services_returns_false(self) -> None:
        assert _has_service_overlap(["svc-a"], []) is False

    def test_case_insensitive_match(self) -> None:
        assert _has_service_overlap(["SVC-A"], ["svc-a"]) is True

    def test_case_insensitive_no_overlap(self) -> None:
        assert _has_service_overlap(["SVC-A"], ["SVC-B"]) is False

    def test_mixed_case_both_sides(self) -> None:
        assert _has_service_overlap(["Svc-A", "SVC-B"], ["svc-b", "svc-c"]) is True


# =============================================================================
# is_service_exists
# =============================================================================


class TestIsServiceExists:
    """Tests for is_service_exists routing function."""

    def test_returns_true_when_services_exist(self) -> None:
        state = _empty_state(incident_affected_services=["svc-a"])
        assert is_service_exists(state) is True

    def test_returns_false_when_services_empty(self) -> None:
        state = _empty_state(incident_affected_services=[])
        assert is_service_exists(state) is False

    def test_returns_false_when_services_missing(self) -> None:
        state = _empty_state()
        del state["incident_affected_services"]  # type: ignore[misc]
        assert is_service_exists(state) is False


# =============================================================================
# has_change_events / route_after_fetch
# =============================================================================


def _change_event() -> ChangeEvent:
    """Build a minimal ChangeEvent for routing tests."""
    return ChangeEvent(
        id="evt-1",
        description="a change",
        start_time=datetime(2025, 1, 15, 11, 0, 0),
    )


class TestHasChangeEvents:
    """Tests for has_change_events (source-agnostic event detection)."""

    def test_false_when_no_events(self) -> None:
        assert has_change_events(_empty_state()) is False

    def test_true_when_github_events(self) -> None:
        state = _empty_state(github_events=[_change_event()])
        assert has_change_events(state) is True

    def test_true_when_jira_events(self) -> None:
        state = _empty_state(jira_events=[_change_event()])
        assert has_change_events(state) is True


class TestRouteAfterFetch:
    """Tests for route_after_fetch routing function."""

    def test_skip_when_no_events(self) -> None:
        assert route_after_fetch(_empty_state()) == "skip"

    def test_service_when_events_and_services(self) -> None:
        state = _empty_state(
            github_events=[_change_event()],
            incident_affected_services=["svc-a"],
        )
        assert route_after_fetch(state) == "service"

    def test_llm_when_events_and_no_services(self) -> None:
        state = _empty_state(jira_events=[_change_event()])
        assert route_after_fetch(state) == "llm"


# =============================================================================
# fetch_jira_issues
# =============================================================================


class TestFetchJiraIssues:
    """Tests for fetch_jira_issues node."""

    def test_fetches_and_converts_issues(self) -> None:
        """Successful API response is converted to ChangeEvent list."""
        incident_time = datetime(2025, 1, 15, 12, 0, 0)
        state = _empty_state(incident_created_at=incident_time)
        config = _make_config()

        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {
                "issue_key": "TCMR-1",
                "issue_summary": "VPN cert rotation",
                "created_at": "2025-01-15T10:00:00",
                "services": ["svc-vpn"],
            },
        ]
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            return_value=mock_resp,
        ) as mock_get:
            result = fetch_jira_issues(state, config)

        assert len(result["jira_events"]) == 1
        assert result["jira_events"][0].id == "TCMR-1"
        assert result["jira_events"][0].services == ["svc-vpn"]

        # Verify the request params
        call_kwargs = mock_get.call_args
        assert "/v1/jira/issues" in str(call_kwargs.args[0])

    def test_opens_named_span_with_event_count(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        """A fetch_jira_issues span is opened with the fetched event count."""
        state = _empty_state(incident_created_at=datetime(2025, 1, 15, 12, 0, 0))
        config = _make_config()

        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {
                "issue_key": "TCMR-1",
                "issue_summary": "VPN cert rotation",
                "created_at": "2025-01-15T10:00:00",
                "services": ["svc-vpn"],
            },
        ]
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            return_value=mock_resp,
        ):
            fetch_jira_issues(state, config)

        (span,) = span_exporter.get_finished_spans()
        assert span.name == "fetch_jira_issues"
        assert span.attributes is not None
        assert span.attributes["event_count"] == 1

    def test_signs_request_when_service_secret_configured(self) -> None:
        """Phase 1c: attaches a verifiable signature when api_service_secret is set."""
        from common.utils.service_auth import verify_request

        state = _empty_state(incident_created_at=datetime(2025, 1, 15, 12, 0, 0))
        config = _make_config(api_service_secret="shared-secret")

        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            return_value=mock_resp,
        ) as mock_get:
            fetch_jira_issues(state, config)

        headers = mock_get.call_args.kwargs["headers"]
        signed_target = mock_get.call_args.args[0].raw_path.decode()
        assert signed_target.startswith("/v1/jira/issues?")  # query is covered too
        verify_request(
            "shared-secret",
            "GET",
            signed_target,
            b"",
            headers["X-Matik-Service-Timestamp"],
            headers["X-Matik-Service-Signature"],
        )  # does not raise

    def test_no_signature_when_service_secret_unset(self) -> None:
        state = _empty_state(incident_created_at=datetime(2025, 1, 15, 12, 0, 0))
        config = _make_config()  # api_service_secret defaults to None

        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            return_value=mock_resp,
        ) as mock_get:
            fetch_jira_issues(state, config)

        assert mock_get.call_args.kwargs["headers"] is None

    def test_no_incident_time_returns_empty(self) -> None:
        """When incident_created_at is missing, jira_events is set to []."""
        state = _empty_state(incident_created_at=None)
        config = _make_config()
        result = fetch_jira_issues(state, config)
        assert result["jira_events"] == []

    def test_incident_time_as_string(self) -> None:
        """incident_created_at as ISO string is parsed correctly."""
        state = _empty_state(incident_created_at="2025-01-15T12:00:00")
        config = _make_config()

        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            return_value=mock_resp,
        ):
            result = fetch_jira_issues(state, config)
        assert result["jira_events"] == []

    def test_api_error_returns_empty(self) -> None:
        """HTTP error results in empty jira_events."""
        state = _empty_state()
        config = _make_config()

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            side_effect=httpx.HTTPStatusError(
                "500", request=MagicMock(), response=MagicMock()
            ),
        ):
            result = fetch_jira_issues(state, config)
        assert result["jira_events"] == []

    @patch("enigmatologist.correlations.reliability.incident.nodes.logger")
    def test_401_logs_signature_rejection_distinctly(
        self, mock_logger: MagicMock
    ) -> None:
        """A signature-check 401 must be loud, not indistinguishable from "no data".

        Phase 1c: once matik-api enforces the service signature, a
        misconfigured/stale api_service_secret degrades to an empty result
        just like a genuine "no issues in this window" — this must at least
        log distinctly (error, not warning, with an actionable message) so
        it isn't silently mistaken for the latter.
        """
        state = _empty_state()
        config = _make_config()

        unauthorized_response = MagicMock()
        unauthorized_response.status_code = 401

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            side_effect=httpx.HTTPStatusError(
                "401", request=MagicMock(), response=unauthorized_response
            ),
        ):
            result = fetch_jira_issues(state, config)

        assert result["jira_events"] == []
        mock_logger.error.assert_called_once()
        mock_logger.warning.assert_not_called()
        assert "signature" in mock_logger.error.call_args[0][0].lower()

    def test_uses_config_lookback(self) -> None:
        """Uses lookback_hours_jira from config."""
        cfg = EnigmatologistConfig(
            scribe_queue_url="",
            correlation_system_prompt="test",
            lookback_hours_jira=48,
            speculative_patterns=[],
        )
        incident_time = datetime(2025, 1, 15, 12, 0, 0)
        state = _empty_state(incident_created_at=incident_time)
        config = _make_config(cfg=cfg)

        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            return_value=mock_resp,
        ) as mock_get:
            fetch_jira_issues(state, config)

        url = mock_get.call_args.args[0]
        expected_start = (incident_time - timedelta(hours=48)).isoformat()
        assert url.params["start_time"] == expected_start

    def test_uses_default_api_base_url_when_not_configured(self) -> None:
        """Falls back to _DEFAULT_API_BASE_URL when api_base_url is None."""
        state = _empty_state()
        config = _make_config(api_base_url=None)
        config["configurable"]["api_base_url"] = None

        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            return_value=mock_resp,
        ) as mock_get:
            fetch_jira_issues(state, config)

        assert _DEFAULT_API_BASE_URL in str(mock_get.call_args.args[0])

    def test_passes_time_field_param(self) -> None:
        """time_field is included in request params when present in state."""
        state = _empty_state(jira_time_field="updated_at")
        config = _make_config()

        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            return_value=mock_resp,
        ) as mock_get:
            fetch_jira_issues(state, config)

        url = mock_get.call_args.args[0]
        assert url.params["time_field"] == "updated_at"

    def test_fallback_summary_field(self) -> None:
        """Falls back to 'summary' when 'issue_summary' is missing."""
        state = _empty_state()
        config = _make_config()

        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {
                "issue_key": "TCMR-2",
                "summary": "fallback summary",
                "created_at": "2025-01-15T10:00:00",
            },
        ]
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            return_value=mock_resp,
        ):
            result = fetch_jira_issues(state, config)

        assert result["jira_events"][0].description == "fallback summary"


# =============================================================================
# fetch_biztech_github_prs
# =============================================================================


class TestFetchBiztechGithubPrs:
    """Tests for fetch_biztech_github_prs node."""

    def test_fetches_and_converts_prs(self) -> None:
        """Successful API response is converted to ChangeEvent list."""
        incident_time = datetime(2025, 1, 15, 12, 0, 0)
        state = _empty_state(incident_created_at=incident_time)
        config = _make_config()

        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {
                "pull_request_id": 42,
                "pull_request_summary": "Fix auth flow",
                "created_at": "2025-01-15T11:00:00",
                "merged_at": "2025-01-15T11:30:00",
                "services": ["svc-auth"],
            },
        ]
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            return_value=mock_resp,
        ):
            result = fetch_biztech_github_prs(state, config)

        assert len(result["github_events"]) == 1
        assert result["github_events"][0].id == "42"
        assert result["github_events"][0].services == ["svc-auth"]

    def test_opens_named_span_with_event_count(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        """A fetch_biztech_github_prs span is opened with the fetched event count."""
        state = _empty_state(incident_created_at=datetime(2025, 1, 15, 12, 0, 0))
        config = _make_config()

        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {
                "pull_request_id": 42,
                "pull_request_summary": "Fix auth flow",
                "created_at": "2025-01-15T11:00:00",
                "merged_at": "2025-01-15T11:30:00",
                "services": ["svc-auth"],
            },
        ]
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            return_value=mock_resp,
        ):
            fetch_biztech_github_prs(state, config)

        (span,) = span_exporter.get_finished_spans()
        assert span.name == "fetch_biztech_github_prs"
        assert span.attributes is not None
        assert span.attributes["event_count"] == 1

    def test_signs_request_when_service_secret_configured(self) -> None:
        """Phase 1c: attaches a verifiable signature when api_service_secret is set."""
        from common.utils.service_auth import verify_request

        state = _empty_state(incident_created_at=datetime(2025, 1, 15, 12, 0, 0))
        config = _make_config(api_service_secret="shared-secret")

        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            return_value=mock_resp,
        ) as mock_get:
            fetch_biztech_github_prs(state, config)

        headers = mock_get.call_args.kwargs["headers"]
        signed_target = mock_get.call_args.args[0].raw_path.decode()
        assert signed_target.startswith("/v1/ghe/pr?")  # query is covered too
        verify_request(
            "shared-secret",
            "GET",
            signed_target,
            b"",
            headers["X-Matik-Service-Timestamp"],
            headers["X-Matik-Service-Signature"],
        )  # does not raise

    def test_no_incident_time_returns_empty(self) -> None:
        """When incident_created_at is missing, github_events is set to []."""
        state = _empty_state(incident_created_at=None)
        config = _make_config()
        result = fetch_biztech_github_prs(state, config)
        assert result["github_events"] == []

    def test_api_error_returns_empty(self) -> None:
        """HTTP error results in empty github_events."""
        state = _empty_state()
        config = _make_config()

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            side_effect=httpx.ConnectError("connection refused"),
        ):
            result = fetch_biztech_github_prs(state, config)
        assert result["github_events"] == []

    @patch("enigmatologist.correlations.reliability.incident.nodes.logger")
    def test_401_logs_signature_rejection_distinctly(
        self, mock_logger: MagicMock
    ) -> None:
        """A signature-check 401 must be loud, not indistinguishable from "no data"."""
        state = _empty_state()
        config = _make_config()

        unauthorized_response = MagicMock()
        unauthorized_response.status_code = 401

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            side_effect=httpx.HTTPStatusError(
                "401", request=MagicMock(), response=unauthorized_response
            ),
        ):
            result = fetch_biztech_github_prs(state, config)

        assert result["github_events"] == []
        mock_logger.error.assert_called_once()
        mock_logger.warning.assert_not_called()
        assert "signature" in mock_logger.error.call_args[0][0].lower()

    def test_uses_config_lookback(self) -> None:
        """Uses lookback_hours_github from config."""
        cfg = EnigmatologistConfig(
            scribe_queue_url="",
            correlation_system_prompt="test",
            lookback_hours_github=12,
            speculative_patterns=[],
        )
        incident_time = datetime(2025, 1, 15, 12, 0, 0)
        state = _empty_state(incident_created_at=incident_time)
        config = _make_config(cfg=cfg)

        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            return_value=mock_resp,
        ) as mock_get:
            fetch_biztech_github_prs(state, config)

        url = mock_get.call_args.args[0]
        expected_start = (incident_time - timedelta(hours=12)).isoformat()
        assert url.params["start_time"] == expected_start

    def test_fallback_title_field(self) -> None:
        """Falls back to 'title' when 'pull_request_summary' is missing."""
        state = _empty_state()
        config = _make_config()

        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {
                "pull_request_id": 99,
                "title": "fallback title",
                "created_at": "2025-01-15T10:00:00",
            },
        ]
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            return_value=mock_resp,
        ):
            result = fetch_biztech_github_prs(state, config)

        assert result["github_events"][0].description == "fallback title"

    def test_pr_uses_merged_at_for_times(self) -> None:
        """start_time and end_time use merged_at when available."""
        state = _empty_state()
        config = _make_config()

        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {
                "pull_request_id": 1,
                "title": "pr",
                "created_at": "2025-01-15T10:00:00",
                "merged_at": "2025-01-15T11:00:00",
            },
        ]
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "enigmatologist.correlations.reliability.incident.nodes.httpx.get",
            return_value=mock_resp,
        ):
            result = fetch_biztech_github_prs(state, config)

        event = result["github_events"][0]
        assert event.start_time == datetime.fromisoformat("2025-01-15T11:00:00")
        assert event.end_time == datetime.fromisoformat("2025-01-15T11:00:00")


# =============================================================================
# assign_correlations_by_service
# =============================================================================


class TestAssignCorrelationsByService:
    """Tests for assign_correlations_by_service."""

    def test_assigns_matching_events(self) -> None:
        """Events with overlapping services are assigned as correlations."""
        event_time = datetime(2025, 1, 15, 11, 0, 0)
        state = _empty_state(
            incident_affected_services=["svc-a"],
            github_events=[
                ChangeEvent(
                    id="PR-1",
                    description="fix",
                    start_time=event_time,
                    services=["svc-a", "svc-b"],
                ),
            ],
            jira_events=[
                ChangeEvent(
                    id="TCMR-1",
                    description="change",
                    start_time=event_time,
                    services=["svc-a"],
                ),
            ],
        )
        result = assign_correlations_by_service(state)
        svc_result = result["service_correlation_result"]
        assert svc_result is not None
        assert len(svc_result.biztech_github) == 1
        assert len(svc_result.jira) == 1

    def test_no_overlap_returns_state_unchanged(self) -> None:
        """When no events have overlapping services, no result is set."""
        event_time = datetime(2025, 1, 15, 11, 0, 0)
        state = _empty_state(
            incident_affected_services=["svc-a"],
            github_events=[
                ChangeEvent(
                    id="PR-1",
                    description="fix",
                    start_time=event_time,
                    services=["svc-b"],
                ),
            ],
        )
        result = assign_correlations_by_service(state)
        assert result.get("service_correlation_result") is None

    def test_no_services_returns_state_unchanged(self) -> None:
        """When incident has no services, no correlations are assigned."""
        event_time = datetime(2025, 1, 15, 11, 0, 0)
        state = _empty_state(
            incident_affected_services=[],
            github_events=[
                ChangeEvent(
                    id="PR-1",
                    description="fix",
                    start_time=event_time,
                    services=["svc-a"],
                ),
            ],
        )
        result = assign_correlations_by_service(state)
        assert result.get("service_correlation_result") is None


# =============================================================================
# _build_user_prompt
# =============================================================================


class TestBuildUserPrompt:
    """Tests for _build_user_prompt."""

    def test_includes_all_incident_fields(self) -> None:
        """Prompt includes incident description, time, and services."""
        event_time = datetime(2025, 1, 15, 11, 0, 0)
        state = _empty_state(
            incident_description="VPN is down",
            incident_created_at=datetime(2025, 1, 15, 12, 0, 0),
            incident_affected_services=["svc-vpn"],
            github_events=[
                ChangeEvent(id="PR-1", description="cert fix", start_time=event_time),
            ],
            jira_events=[
                ChangeEvent(
                    id="TCMR-1", description="cert rotation", start_time=event_time
                ),
            ],
        )
        prompt = _build_user_prompt(state)
        assert "VPN is down" in prompt
        assert "svc-vpn" in prompt
        assert "PR-1" in prompt
        assert "TCMR-1" in prompt

    def test_empty_events(self) -> None:
        """Prompt handles empty event lists gracefully."""
        state = _empty_state()
        prompt = _build_user_prompt(state)
        assert "Github PRs: []" in prompt
        assert "Jira issues: []" in prompt

    def test_includes_incident_channel_summary_when_present(self) -> None:
        """OpsBot's channel summary, when present, is carried into the prompt."""
        state = _empty_state(
            incident_channel_summary="Team is investigating elevated error rates"
        )
        prompt = _build_user_prompt(state)
        assert "Team is investigating elevated error rates" in prompt

    def test_incident_channel_summary_absent_renders_none(self) -> None:
        """No incident_channel_summary in state (e.g. incidentio-only trigger,
        no OpsBot summary yet) renders as None rather than raising."""
        state = _empty_state()
        prompt = _build_user_prompt(state)
        assert "Incident channel summary: None" in prompt


# =============================================================================
# assign_correlations_by_llm
# =============================================================================


class TestAssignCorrelationsByLLM:
    """Tests for assign_correlations_by_llm."""

    @pytest.mark.asyncio
    async def test_successful_llm_correlation(self) -> None:
        """Valid LLM response creates an LLMCorrelationResult on state."""
        facade_client = AsyncMock()
        facade_client.send_message_with_retry.return_value = json.dumps(
            {
                "biztech_github": [
                    {"id": "PR-1", "score": 0.8, "reasoning": "same component"},
                ],
                "jira": [],
            }
        )

        cfg = EnigmatologistConfig(
            scribe_queue_url="",
            correlation_system_prompt="You are a correlation engine.",
            min_llm_score=0.3,
            speculative_patterns=[],
        )
        config = _make_config(cfg=cfg, facade_client=facade_client)

        state = _empty_state(
            github_events=[
                ChangeEvent(
                    id="PR-1",
                    description="fix auth",
                    start_time=datetime(2025, 1, 15, 11, 0, 0),
                ),
            ],
        )

        result = await assign_correlations_by_llm(state, config)
        llm_result = result["llm_correlation_result"]
        assert llm_result is not None
        assert len(llm_result.biztech_github) == 1
        assert llm_result.biztech_github[0].base_score == 0.8

    @pytest.mark.asyncio
    async def test_skips_when_no_system_prompt(self) -> None:
        """Returns state unchanged when correlation_system_prompt is empty."""
        cfg = EnigmatologistConfig(
            scribe_queue_url="", correlation_system_prompt="", speculative_patterns=[]
        )
        facade_client = AsyncMock()
        config = _make_config(cfg=cfg, facade_client=facade_client)

        state = _empty_state()
        result = await assign_correlations_by_llm(state, config)
        assert result.get("llm_correlation_result") is None
        facade_client.send_message_with_retry.assert_not_called()

    @pytest.mark.asyncio
    async def test_filters_below_min_score(self) -> None:
        """Matches below min_llm_score are filtered out."""
        facade_client = AsyncMock()
        facade_client.send_message_with_retry.return_value = json.dumps(
            {
                "biztech_github": [
                    {"id": "PR-1", "score": 0.1, "reasoning": "weak match"},
                ],
                "jira": [],
            }
        )

        cfg = EnigmatologistConfig(
            scribe_queue_url="",
            correlation_system_prompt="test prompt",
            min_llm_score=0.3,
            speculative_patterns=[],
        )
        config = _make_config(cfg=cfg, facade_client=facade_client)

        state = _empty_state()
        result = await assign_correlations_by_llm(state, config)
        # No matches above threshold, so result should not be set
        assert result.get("llm_correlation_result") is None

    @pytest.mark.asyncio
    async def test_filters_low_confidence_speculative_reasoning(self) -> None:
        """Low-confidence matches with speculative reasoning are filtered out."""
        facade_client = AsyncMock()
        facade_client.send_message_with_retry.return_value = json.dumps(
            {
                "biztech_github": [
                    {
                        "id": "PR-1",
                        "score": 0.4,
                        "reasoning": "This could potentially be related",
                    },
                ],
                "jira": [],
            }
        )

        cfg = EnigmatologistConfig(
            scribe_queue_url="",
            correlation_system_prompt="test prompt",
            speculative_patterns=[r"\bcould\b", r"\bpotentially\b"],
        )
        config = _make_config(cfg=cfg, facade_client=facade_client)

        state = _empty_state()
        result = await assign_correlations_by_llm(state, config)
        assert result.get("llm_correlation_result") is None

    @pytest.mark.asyncio
    async def test_keeps_high_confidence_speculative_reasoning(self) -> None:
        """A confident match is kept even if its reasoning hedges.

        Regression test: the LLM scored PR-1 at 0.9 but phrased its reasoning with
        "could" — above speculative_max_score the score is trusted over wording,
        so the match must survive rather than be dropped as speculative.
        """
        facade_client = AsyncMock()
        facade_client.send_message_with_retry.return_value = json.dumps(
            {
                "biztech_github": [
                    {
                        "id": "PR-1",
                        "score": 0.9,
                        "reasoning": "The cert rotation could have caused the outage",
                    },
                ],
                "jira": [],
            }
        )

        cfg = EnigmatologistConfig(
            scribe_queue_url="",
            correlation_system_prompt="test prompt",
            speculative_patterns=[r"\bcould\b", r"\bpotentially\b"],
            speculative_max_score=0.6,
        )
        config = _make_config(cfg=cfg, facade_client=facade_client)

        state = _empty_state()
        result = await assign_correlations_by_llm(state, config)
        llm_result = result.get("llm_correlation_result")
        assert llm_result is not None
        assert len(llm_result.biztech_github) == 1
        assert llm_result.biztech_github[0].id == "PR-1"

    @pytest.mark.asyncio
    async def test_speculative_pattern_word_boundary(self) -> None:
        """Word-boundary patterns do not match when the word is part of a larger word."""
        facade_client = AsyncMock()
        facade_client.send_message_with_retry.return_value = json.dumps(
            {
                "biztech_github": [
                    {
                        "id": "PR-1",
                        "score": 0.8,
                        "reasoning": "The deployment changed display settings",
                    },
                ],
                "jira": [],
            }
        )

        # \bmay\b should NOT match "display" (contains "may" but not at word boundary)
        cfg = EnigmatologistConfig(
            scribe_queue_url="",
            correlation_system_prompt="test prompt",
            speculative_patterns=[r"\bmay\b"],
        )
        config = _make_config(cfg=cfg, facade_client=facade_client)

        state = _empty_state()
        result = await assign_correlations_by_llm(state, config)
        llm_result = result.get("llm_correlation_result")
        assert llm_result is not None
        assert len(llm_result.biztech_github) == 1

    @pytest.mark.asyncio
    async def test_speculative_pattern_case_insensitive(self) -> None:
        """Speculative patterns are matched case-insensitively."""
        facade_client = AsyncMock()
        facade_client.send_message_with_retry.return_value = json.dumps(
            {
                "biztech_github": [
                    {
                        "id": "PR-1",
                        "score": 0.5,
                        "reasoning": "This MAY have caused the issue",
                    },
                ],
                "jira": [],
            }
        )

        cfg = EnigmatologistConfig(
            scribe_queue_url="",
            correlation_system_prompt="test prompt",
            speculative_patterns=[r"\bmay\b"],
        )
        config = _make_config(cfg=cfg, facade_client=facade_client)

        state = _empty_state()
        result = await assign_correlations_by_llm(state, config)
        assert result.get("llm_correlation_result") is None

    @pytest.mark.asyncio
    async def test_empty_speculative_patterns_passes_all(self) -> None:
        """An empty speculative_patterns list does not filter any matches."""
        facade_client = AsyncMock()
        facade_client.send_message_with_retry.return_value = json.dumps(
            {
                "biztech_github": [
                    {"id": "PR-1", "score": 0.8, "reasoning": "may potentially cause"},
                ],
                "jira": [],
            }
        )

        cfg = EnigmatologistConfig(
            scribe_queue_url="",
            correlation_system_prompt="test prompt",
            speculative_patterns=[],
        )
        config = _make_config(cfg=cfg, facade_client=facade_client)

        state = _empty_state()
        result = await assign_correlations_by_llm(state, config)
        llm_result = result.get("llm_correlation_result")
        assert llm_result is not None
        assert len(llm_result.biztech_github) == 1

    @pytest.mark.asyncio
    async def test_strips_markdown_code_fences(self) -> None:
        """Markdown code fences around JSON response are stripped."""
        facade_client = AsyncMock()
        facade_client.send_message_with_retry.return_value = '```json\n{"biztech_github": [{"id": "PR-1", "score": 0.8, "reasoning": "cert fix"}], "jira": []}\n```'

        cfg = EnigmatologistConfig(
            scribe_queue_url="",
            correlation_system_prompt="test prompt",
            speculative_patterns=[],
        )
        config = _make_config(cfg=cfg, facade_client=facade_client)

        state = _empty_state()
        result = await assign_correlations_by_llm(state, config)
        assert result["llm_correlation_result"] is not None
        assert result["llm_correlation_result"].biztech_github[0].id == "PR-1"

    @pytest.mark.asyncio
    async def test_facade_error_sets_none(self) -> None:
        """Facade client error sets llm_correlation_result to None."""
        facade_client = AsyncMock()
        facade_client.send_message_with_retry.side_effect = RuntimeError("LLM down")

        cfg = EnigmatologistConfig(
            scribe_queue_url="",
            correlation_system_prompt="test prompt",
            speculative_patterns=[],
        )
        config = _make_config(cfg=cfg, facade_client=facade_client)

        state = _empty_state()
        result = await assign_correlations_by_llm(state, config)
        assert result["llm_correlation_result"] is None

    @pytest.mark.asyncio
    async def test_no_config_uses_fallback_defaults(self) -> None:
        """When enigmatologist_config is None, fallback values are used."""
        facade_client = AsyncMock()
        facade_client.send_message_with_retry.return_value = json.dumps(
            {"biztech_github": [], "jira": []}
        )

        config: RunnableConfig = RunnableConfig(
            configurable={
                "enigmatologist_config": None,
                "api_base_url": "http://localhost:8080",
                "llm_client": facade_client,
            }
        )

        state = _empty_state()
        # cfg is None → system_prompt is "" → early return
        result = await assign_correlations_by_llm(state, config)
        assert result.get("llm_correlation_result") is None
        facade_client.send_message_with_retry.assert_not_called()
