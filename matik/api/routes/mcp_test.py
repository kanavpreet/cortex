"""Tests for MCP source data endpoints."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.deps import (
    get_ghe_pr_dao,
    get_incidentio_incident_dao,
    get_jira_issues_dao,
    get_reliability_correlation_dao,
    get_reliability_correlation_group_dao,
)
from api.routes.mcp import _normalize_incident_reference_id, router
from common.constants import GHE_BASE_URL, INCIDENTIO_BASE_URL, JIRA_BROWSE_URL
from common.daos.ghe_pr_dao import GHEPullRequestWithRepo
from common.models.ghe_pr import GHEPullRequest
from common.models.incidentio_incident import IncidentIOIncident
from common.models.jira_issue_record import JiraIssueRecord
from common.models.reliability_correlation import ReliabilityCorrelation
from common.models.reliability_correlation_group import ReliabilityCorrelationGroup


@pytest.fixture
def app() -> FastAPI:
    """Create a FastAPI app with the MCP router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def mock_incident_dao() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_jira_dao() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_pr_dao() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_correlation_dao() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_correlation_group_dao() -> MagicMock:
    return MagicMock()


class TestGetCorrelationGroup:
    """Tests for GET /v1/mcp/correlation/reliability/group/{anchor_entity_id}."""

    def test_returns_group_when_found(
        self, app: FastAPI, mock_correlation_group_dao: MagicMock
    ) -> None:
        """Test returns correlation group when found."""
        group = ReliabilityCorrelationGroup(
            anchor_entity_id="INC-99",
            anchor_type="incident",
            correlation_timestamp=datetime(2024, 1, 15, 10, 30, 0),
            feedback_state="NEUTRAL",
            review_status=False,
        )
        mock_correlation_group_dao.find_group.return_value = group
        app.dependency_overrides[get_reliability_correlation_group_dao] = lambda: (
            mock_correlation_group_dao
        )
        client = TestClient(app)

        response = client.get("/v1/mcp/correlation/reliability/group/INC-99")

        assert response.status_code == 200
        data = response.json()
        assert data["anchor_entity_id"] == "INC-99"
        assert data["anchor_type"] == "incident"

    def test_returns_null_when_not_found(
        self, app: FastAPI, mock_correlation_group_dao: MagicMock
    ) -> None:
        """Test returns null when no group exists for anchor."""
        mock_correlation_group_dao.find_group.return_value = None
        app.dependency_overrides[get_reliability_correlation_group_dao] = lambda: (
            mock_correlation_group_dao
        )
        client = TestClient(app)

        response = client.get("/v1/mcp/correlation/reliability/group/INC-nonexistent")

        assert response.status_code == 200
        assert response.json() is None

    def test_operation_id_is_set(self, app: FastAPI) -> None:
        """Test that the endpoint has the expected operationId for MCP discovery."""
        openapi = app.openapi()
        path = openapi["paths"].get(
            "/v1/mcp/correlation/reliability/group/{anchor_entity_id}", {}
        )
        assert path.get("get", {}).get("operationId") == "mcp_get_correlation_group"


class TestGetCorrelationEvents:
    """Tests for GET /v1/mcp/correlation/reliability/events/{anchor_entity_id}."""

    def test_returns_events_when_found(
        self, app: FastAPI, mock_correlation_dao: MagicMock
    ) -> None:
        """Test returns correlation events for anchor entity."""
        events = [
            ReliabilityCorrelation(
                anchor_entity_id="INC-99",
                correlation_type="SERVICE_MATCH",
                entity_type="github_pr",
                entity_id="12345",
            ),
            ReliabilityCorrelation(
                anchor_entity_id="INC-99",
                correlation_type="LLM",
                entity_type="jira_tcmr",
                entity_id="TCMR-100",
            ),
        ]
        mock_correlation_dao.find_by_anchor.return_value = events
        app.dependency_overrides[get_reliability_correlation_dao] = lambda: (
            mock_correlation_dao
        )
        client = TestClient(app)

        response = client.get("/v1/mcp/correlation/reliability/events/INC-99")

        assert response.status_code == 200
        data = response.json()
        assert len(data["events"]) == 2
        assert data["events"][0]["entity_type"] == "github_pr"
        assert data["events"][0]["entity_id"] == "12345"
        assert data["events"][1]["entity_type"] == "jira_tcmr"
        assert data["events"][1]["entity_id"] == "TCMR-100"

    def test_returns_empty_list_when_none_found(
        self, app: FastAPI, mock_correlation_dao: MagicMock
    ) -> None:
        """Test returns empty list when no events exist for anchor."""
        mock_correlation_dao.find_by_anchor.return_value = []
        app.dependency_overrides[get_reliability_correlation_dao] = lambda: (
            mock_correlation_dao
        )
        client = TestClient(app)

        response = client.get("/v1/mcp/correlation/reliability/events/INC-nonexistent")

        assert response.status_code == 200
        assert response.json() == {"events": []}

    def test_operation_id_is_set(self, app: FastAPI) -> None:
        """Test that the endpoint has the expected operationId for MCP discovery."""
        openapi = app.openapi()
        path = openapi["paths"].get(
            "/v1/mcp/correlation/reliability/events/{anchor_entity_id}", {}
        )
        assert path.get("get", {}).get("operationId") == "mcp_get_correlation_events"


class TestGetIncidentsByReferenceIds:
    """Tests for POST /v1/mcp/incidentio/incidents."""

    def _make_incident(self, reference_id: str) -> IncidentIOIncident:
        return IncidentIOIncident(
            incident_id=f"ID-{reference_id}",
            reference_id=reference_id,
            severity="Sev-1",
            slack_channel_id="C123",
            status="open",
            visibility="public",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            reported_at=datetime(2024, 1, 15, 10, 30, 0),
        )

    def test_returns_incidents_for_reference_ids(
        self, app: FastAPI, mock_incident_dao: MagicMock
    ) -> None:
        """Test returns incidents matching the provided reference_ids."""
        mock_incident_dao.find_incidents_by_reference_ids.return_value = [
            self._make_incident("INC-1"),
            self._make_incident("INC-2"),
        ]
        app.dependency_overrides[get_incidentio_incident_dao] = lambda: (
            mock_incident_dao
        )
        client = TestClient(app)

        response = client.get(
            "/v1/mcp/incidentio/incidents",
            params=[("reference_ids", "INC-1"), ("reference_ids", "INC-2")],
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["incidents"]) == 2
        assert data["incidents"][0]["reference_id"] == "INC-1"
        assert data["incidents"][0]["url"] == f"{INCIDENTIO_BASE_URL}/1"
        assert data["incidents"][1]["reference_id"] == "INC-2"
        assert data["incidents"][1]["url"] == f"{INCIDENTIO_BASE_URL}/2"

    def test_returns_400_when_no_reference_ids_provided(
        self, app: FastAPI, mock_incident_dao: MagicMock
    ) -> None:
        """Test returns 400 with descriptive error when no reference_ids provided."""
        app.dependency_overrides[get_incidentio_incident_dao] = lambda: (
            mock_incident_dao
        )
        client = TestClient(app)

        response = client.get("/v1/mcp/incidentio/incidents")

        assert response.status_code == 400
        assert "reference_ids is required" in response.json()["detail"]
        mock_incident_dao.find_incidents_by_reference_ids.assert_not_called()

    def test_operation_id_is_set(self, app: FastAPI) -> None:
        """Test that the endpoint has the expected operationId for MCP discovery."""
        openapi = app.openapi()
        path = openapi["paths"].get("/v1/mcp/incidentio/incidents", {})
        assert path.get("get", {}).get("operationId") == "mcp_get_incidentio_incidents"


class TestNormalizeIncidentReferenceId:
    """Tests for _normalize_incident_reference_id."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("INC-5267", "INC-5267"),  # already canonical
            ("inc-5267", "INC-5267"),  # lowercase prefix
            ("5267", "INC-5267"),  # plain number
            (" INC-5267 ", "INC-5267"),  # surrounding whitespace
        ],
    )
    def test_normalizes_to_canonical_form(self, raw: str, expected: str) -> None:
        assert _normalize_incident_reference_id(raw) == expected

    def test_endpoint_accepts_plain_number(
        self, app: FastAPI, mock_incident_dao: MagicMock
    ) -> None:
        """Passing just the number (e.g. '5267') should query for 'INC-5267'."""
        mock_incident_dao.find_incidents_by_reference_ids.return_value = []
        app.dependency_overrides[get_incidentio_incident_dao] = lambda: (
            mock_incident_dao
        )
        client = TestClient(app)

        client.get("/v1/mcp/incidentio/incidents", params=[("reference_ids", "5267")])

        mock_incident_dao.find_incidents_by_reference_ids.assert_called_once_with(
            ["INC-5267"]
        )

    def test_endpoint_accepts_lowercase_prefix(
        self, app: FastAPI, mock_incident_dao: MagicMock
    ) -> None:
        """Passing 'inc-5267' should query for 'INC-5267'."""
        mock_incident_dao.find_incidents_by_reference_ids.return_value = []
        app.dependency_overrides[get_incidentio_incident_dao] = lambda: (
            mock_incident_dao
        )
        client = TestClient(app)

        client.get(
            "/v1/mcp/incidentio/incidents", params=[("reference_ids", "inc-5267")]
        )

        mock_incident_dao.find_incidents_by_reference_ids.assert_called_once_with(
            ["INC-5267"]
        )


class TestGetJiraIssuesByKeys:
    """Tests for POST /v1/mcp/jira/issues."""

    def _make_issue(self, issue_key: str) -> JiraIssueRecord:
        return JiraIssueRecord(
            issue_id="10001",
            issue_key=issue_key,
            ticket_type="tcmr",
            summary=f"Summary for {issue_key}",
            status_name="Open",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
        )

    def test_returns_issues_for_keys(
        self, app: FastAPI, mock_jira_dao: MagicMock
    ) -> None:
        """Test returns JIRA issues matching the provided issue_keys."""
        mock_jira_dao.find_issues_by_keys.return_value = [
            self._make_issue("TCMR-1"),
            self._make_issue("TCMR-2"),
        ]
        app.dependency_overrides[get_jira_issues_dao] = lambda: mock_jira_dao
        client = TestClient(app)

        response = client.get(
            "/v1/mcp/jira/issues",
            params=[("issue_keys", "TCMR-1"), ("issue_keys", "TCMR-2")],
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["issues"]) == 2
        assert data["issues"][0]["issue_key"] == "TCMR-1"
        assert data["issues"][0]["url"] == f"{JIRA_BROWSE_URL}/TCMR-1"
        assert data["issues"][1]["issue_key"] == "TCMR-2"
        assert data["issues"][1]["url"] == f"{JIRA_BROWSE_URL}/TCMR-2"

    def test_returns_400_when_no_issue_keys_provided(
        self, app: FastAPI, mock_jira_dao: MagicMock
    ) -> None:
        """Test returns 400 with descriptive error when no issue_keys provided."""
        app.dependency_overrides[get_jira_issues_dao] = lambda: mock_jira_dao
        client = TestClient(app)

        response = client.get("/v1/mcp/jira/issues")

        assert response.status_code == 400
        assert "issue_keys is required" in response.json()["detail"]
        mock_jira_dao.find_issues_by_keys.assert_not_called()

    def test_operation_id_is_set(self, app: FastAPI) -> None:
        """Test that the endpoint has the expected operationId for MCP discovery."""
        openapi = app.openapi()
        path = openapi["paths"].get("/v1/mcp/jira/issues", {})
        assert path.get("get", {}).get("operationId") == "mcp_get_jira_issues"


class TestGetPRsByIds:
    """Tests for POST /v1/mcp/ghe/prs."""

    def _make_pr(self, pull_request_id: int) -> GHEPullRequest:
        return GHEPullRequest(
            pull_request_id=pull_request_id,
            pull_request_number=pull_request_id,
            org_id=1,
            org_login="Airbnb",
            repo_id=100,
            repo_name="my-repo",
            title=f"PR {pull_request_id}",
            merged=True,
            state="closed",
            locked=False,
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            target_branch_name="main",
        )

    def test_returns_prs_for_ids(self, app: FastAPI, mock_pr_dao: MagicMock) -> None:
        """Test returns PRs matching the provided pull_request_ids."""
        mock_pr_dao.find_prs_with_repo_by_pull_request_ids.return_value = [
            GHEPullRequestWithRepo(
                pr=self._make_pr(101), org="Airbnb", repo_name="my-repo"
            ),
            GHEPullRequestWithRepo(
                pr=self._make_pr(102), org="Airbnb", repo_name="my-repo"
            ),
        ]
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        client = TestClient(app)

        response = client.get(
            "/v1/mcp/ghe/prs",
            params=[("pull_request_ids", 101), ("pull_request_ids", 102)],
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["pull_requests"]) == 2
        assert data["pull_requests"][0]["pull_request_id"] == 101
        assert data["pull_requests"][0]["org"] == "Airbnb"
        assert data["pull_requests"][0]["repo_name"] == "my-repo"
        assert (
            data["pull_requests"][0]["url"] == f"{GHE_BASE_URL}/Airbnb/my-repo/pull/101"
        )
        assert data["pull_requests"][1]["pull_request_id"] == 102
        assert data["pull_requests"][1]["org"] == "Airbnb"
        assert data["pull_requests"][1]["repo_name"] == "my-repo"
        assert (
            data["pull_requests"][1]["url"] == f"{GHE_BASE_URL}/Airbnb/my-repo/pull/102"
        )

    def test_returns_400_when_no_pull_request_ids_provided(
        self, app: FastAPI, mock_pr_dao: MagicMock
    ) -> None:
        """Test returns 400 with descriptive error when no pull_request_ids provided."""
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        client = TestClient(app)

        response = client.get("/v1/mcp/ghe/prs")

        assert response.status_code == 400
        assert "pull_request_ids is required" in response.json()["detail"]
        mock_pr_dao.find_prs_with_repo_by_pull_request_ids.assert_not_called()

    def test_operation_id_is_set(self, app: FastAPI) -> None:
        """Test that the endpoint has the expected operationId for MCP discovery."""
        openapi = app.openapi()
        path = openapi["paths"].get("/v1/mcp/ghe/prs", {})
        assert path.get("get", {}).get("operationId") == "mcp_get_ghe_prs"
