"""Tests for JIRA endpoints."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.deps import get_jira_batch_tracker_dao, get_jira_issues_dao
from api.routes.jira import router
from common.models import JiraBatchTracker


@pytest.fixture
def app() -> FastAPI:
    """Create a FastAPI app with the JIRA router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def mock_issues_dao() -> MagicMock:
    """Create a mock JiraIssuesDAO."""
    return MagicMock()


@pytest.fixture
def mock_tracker_dao() -> MagicMock:
    """Create a mock JiraBatchTrackerDAO."""
    return MagicMock()


@pytest.fixture
def sample_tracker() -> JiraBatchTracker:
    """Create a sample batch tracker."""
    return JiraBatchTracker(
        ticket_type="tcmr",
        batch_start=datetime(2024, 1, 1, 0, 0, 0),
        batch_end=datetime(2024, 1, 15, 0, 0, 0),
        window_days=14,
        status="OK",
        error_message=None,
        last_processed_at=datetime(2024, 1, 15, 10, 0, 0),
        updated_at=datetime(2024, 1, 15, 10, 0, 0),
    )


class TestGetJiraIssueHashes:
    """Tests for POST /v1/jira/issues/hashes endpoint."""

    def test_returns_hashes_successfully(
        self,
        app: FastAPI,
        mock_issues_dao: MagicMock,
    ) -> None:
        """Test successful hash retrieval."""
        from common.daos.jira_issues_dao import JiraHashInfo

        mock_issues_dao.get_issue_hashes_by_keys.return_value = {
            "TCMR-123": JiraHashInfo(
                issue_key="TCMR-123",
                summary_hash="abc123",
                comments_hash="def456",
                issue_summary="Test summary",
                issue_comments_summary="Test comments",
            ),
        }
        app.dependency_overrides[get_jira_issues_dao] = lambda: mock_issues_dao
        client = TestClient(app)

        response = client.post(
            "/v1/jira/issues/hashes", json={"issue_keys": ["TCMR-123"]}
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["issue_key"] == "TCMR-123"
        assert data[0]["summary_hash"] == "abc123"
        assert data[0]["comments_hash"] == "def456"
        assert data[0]["issue_summary"] == "Test summary"
        assert data[0]["issue_comments_summary"] == "Test comments"

    def test_returns_empty_list_for_empty_keys(
        self,
        app: FastAPI,
        mock_issues_dao: MagicMock,
    ) -> None:
        """Test that empty issue_keys returns empty list without calling DAO."""
        app.dependency_overrides[get_jira_issues_dao] = lambda: mock_issues_dao
        client = TestClient(app)

        response = client.post("/v1/jira/issues/hashes", json={"issue_keys": []})

        assert response.status_code == 200
        assert response.json() == []
        mock_issues_dao.get_issue_hashes_by_keys.assert_not_called()

    def test_returns_500_when_dao_fails(
        self,
        app: FastAPI,
        mock_issues_dao: MagicMock,
    ) -> None:
        """Test that 500 is returned when DAO returns None."""
        mock_issues_dao.get_issue_hashes_by_keys.return_value = None
        app.dependency_overrides[get_jira_issues_dao] = lambda: mock_issues_dao
        client = TestClient(app)

        response = client.post(
            "/v1/jira/issues/hashes", json={"issue_keys": ["TCMR-123"]}
        )

        assert response.status_code == 500
        assert "Failed to fetch issue hashes" in response.json()["detail"]


class TestGetJiraIssues:
    """Tests for GET /v1/jira/issues endpoint."""

    def test_returns_issues_successfully(
        self,
        app: FastAPI,
        mock_issues_dao: MagicMock,
    ) -> None:
        """Test successful issue retrieval by time range."""
        from common.models import JiraIssueRecord

        mock_issue = JiraIssueRecord(
            issue_id="10001",
            issue_key="TCMR-123",
            ticket_type="tcmr",
            summary="Test issue",
            status_name="Open",
            created_at=datetime(2024, 1, 15, 10, 0, 0),
        )
        mock_issues_dao.find_issues_by_time_range.return_value = [mock_issue]
        app.dependency_overrides[get_jira_issues_dao] = lambda: mock_issues_dao
        client = TestClient(app)

        response = client.get(
            "/v1/jira/issues",
            params={
                "start_time": "2024-01-01T00:00:00Z",
                "end_time": "2024-01-31T23:59:59Z",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["issue_key"] == "TCMR-123"

    def test_filters_by_services(
        self,
        app: FastAPI,
        mock_issues_dao: MagicMock,
    ) -> None:
        """Test that services parameter is parsed and passed to DAO."""
        mock_issues_dao.find_issues_by_time_range.return_value = []
        app.dependency_overrides[get_jira_issues_dao] = lambda: mock_issues_dao
        client = TestClient(app)

        response = client.get(
            "/v1/jira/issues",
            params={
                "start_time": "2024-01-01T00:00:00Z",
                "end_time": "2024-01-31T23:59:59Z",
                "services": "svc-a, svc-b",
            },
        )

        assert response.status_code == 200
        call_args = mock_issues_dao.find_issues_by_time_range.call_args
        assert call_args[0][2] == ["svc-a", "svc-b"]

    def test_passes_time_field_to_dao(
        self,
        app: FastAPI,
        mock_issues_dao: MagicMock,
    ) -> None:
        """Test that time_field parameter is forwarded to DAO."""
        mock_issues_dao.find_issues_by_time_range.return_value = []
        app.dependency_overrides[get_jira_issues_dao] = lambda: mock_issues_dao
        client = TestClient(app)

        response = client.get(
            "/v1/jira/issues",
            params={
                "start_time": "2024-01-01T00:00:00Z",
                "end_time": "2024-01-31T23:59:59Z",
                "time_field": "closed_at",
            },
        )

        assert response.status_code == 200
        call_args = mock_issues_dao.find_issues_by_time_range.call_args
        assert call_args[0][3] == "closed_at"

    def test_returns_400_for_invalid_time_field(
        self,
        app: FastAPI,
        mock_issues_dao: MagicMock,
    ) -> None:
        """Test that 400 is returned for invalid time_field."""
        mock_issues_dao.find_issues_by_time_range.side_effect = ValueError(
            "Invalid time_field 'invalid_field'"
        )
        app.dependency_overrides[get_jira_issues_dao] = lambda: mock_issues_dao
        client = TestClient(app)

        response = client.get(
            "/v1/jira/issues",
            params={
                "start_time": "2024-01-01T00:00:00Z",
                "end_time": "2024-01-31T23:59:59Z",
                "time_field": "invalid_field",
            },
        )

        assert response.status_code == 400

    def test_returns_500_when_dao_fails(
        self,
        app: FastAPI,
        mock_issues_dao: MagicMock,
    ) -> None:
        """Test that 500 is returned when DAO returns None."""
        mock_issues_dao.find_issues_by_time_range.return_value = None
        app.dependency_overrides[get_jira_issues_dao] = lambda: mock_issues_dao
        client = TestClient(app)

        response = client.get(
            "/v1/jira/issues",
            params={
                "start_time": "2024-01-01T00:00:00Z",
                "end_time": "2024-01-31T23:59:59Z",
            },
        )

        assert response.status_code == 500
        assert "Failed to query JIRA issues" in response.json()["detail"]

    def test_defaults_services_to_none(
        self,
        app: FastAPI,
        mock_issues_dao: MagicMock,
    ) -> None:
        """Test that services defaults to None when not provided."""
        mock_issues_dao.find_issues_by_time_range.return_value = []
        app.dependency_overrides[get_jira_issues_dao] = lambda: mock_issues_dao
        client = TestClient(app)

        response = client.get(
            "/v1/jira/issues",
            params={
                "start_time": "2024-01-01T00:00:00Z",
                "end_time": "2024-01-31T23:59:59Z",
            },
        )

        assert response.status_code == 200
        call_args = mock_issues_dao.find_issues_by_time_range.call_args
        assert call_args[0][2] is None


class TestGetBatchTracker:
    """Tests for GET /v1/jira/batch/tracker/{ticket_type} endpoint."""

    def test_returns_tracker_successfully(
        self,
        app: FastAPI,
        mock_tracker_dao: MagicMock,
        sample_tracker: JiraBatchTracker,
    ) -> None:
        """Test successful tracker retrieval."""
        mock_tracker_dao.get_tracker_by_type.return_value = sample_tracker
        app.dependency_overrides[get_jira_batch_tracker_dao] = lambda: mock_tracker_dao
        client = TestClient(app)

        response = client.get("/v1/jira/batch/tracker/tcmr")

        assert response.status_code == 200
        data = response.json()
        assert data["ticket_type"] == "tcmr"
        assert data["status"] == "OK"
        assert data["window_days"] == 14

    def test_returns_404_when_tracker_not_found(
        self,
        app: FastAPI,
        mock_tracker_dao: MagicMock,
    ) -> None:
        """Test that 404 is returned when tracker is not found."""
        mock_tracker_dao.get_tracker_by_type.return_value = None
        app.dependency_overrides[get_jira_batch_tracker_dao] = lambda: mock_tracker_dao
        client = TestClient(app)

        response = client.get("/v1/jira/batch/tracker/tcmr")

        assert response.status_code == 404
        assert "batch tracker not found" in response.json()["detail"]

    def test_returns_400_for_invalid_ticket_type(
        self,
        app: FastAPI,
        mock_tracker_dao: MagicMock,
    ) -> None:
        """Test that 400 is returned for invalid ticket type."""
        app.dependency_overrides[get_jira_batch_tracker_dao] = lambda: mock_tracker_dao
        client = TestClient(app)

        response = client.get("/v1/jira/batch/tracker/invalid")

        assert response.status_code == 400


class TestPostBatchTracker:
    """Tests for POST /v1/jira/batch/tracker/{ticket_type} endpoint."""

    def test_updates_tracker_successfully(
        self,
        app: FastAPI,
        mock_tracker_dao: MagicMock,
    ) -> None:
        """Test successful tracker update."""
        mock_tracker_dao.update_tracker.return_value = True
        app.dependency_overrides[get_jira_batch_tracker_dao] = lambda: mock_tracker_dao
        client = TestClient(app)

        request = {
            "ticket_type": "tcmr",
            "batch_start": "2024-01-01T00:00:00Z",
            "batch_end": "2024-01-15T00:00:00Z",
            "window_days": 14,
            "status": "OK",
            "updated_at": "2024-01-15T10:00:00Z",
        }

        response = client.post("/v1/jira/batch/tracker/tcmr", json=request)

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "batch tracker updated successfully"
        assert data["ticket_type"] == "tcmr"
        assert data["status"] == "OK"

    def test_returns_400_when_ticket_type_mismatch(
        self,
        app: FastAPI,
        mock_tracker_dao: MagicMock,
    ) -> None:
        """Test that 400 is returned when ticket_type in URL doesn't match body."""
        app.dependency_overrides[get_jira_batch_tracker_dao] = lambda: mock_tracker_dao
        client = TestClient(app)

        request = {
            "ticket_type": "operational",  # Different from URL
            "batch_start": "2024-01-01T00:00:00Z",
            "batch_end": "2024-01-15T00:00:00Z",
            "window_days": 14,
            "updated_at": "2024-01-15T10:00:00Z",
        }

        response = client.post("/v1/jira/batch/tracker/tcmr", json=request)

        assert response.status_code == 400
        assert "must match" in response.json()["detail"]

    def test_returns_400_for_invalid_ticket_type(
        self,
        app: FastAPI,
        mock_tracker_dao: MagicMock,
    ) -> None:
        """Test that 400 is returned for invalid ticket type."""
        app.dependency_overrides[get_jira_batch_tracker_dao] = lambda: mock_tracker_dao
        client = TestClient(app)

        request = {
            "ticket_type": "invalid",
            "batch_start": "2024-01-01T00:00:00Z",
            "batch_end": "2024-01-15T00:00:00Z",
            "window_days": 14,
            "updated_at": "2024-01-15T10:00:00Z",
        }

        response = client.post("/v1/jira/batch/tracker/invalid", json=request)

        assert response.status_code == 400

    def test_returns_500_when_update_fails(
        self,
        app: FastAPI,
        mock_tracker_dao: MagicMock,
    ) -> None:
        """Test that 500 is returned when update fails."""
        mock_tracker_dao.update_tracker.return_value = False
        app.dependency_overrides[get_jira_batch_tracker_dao] = lambda: mock_tracker_dao
        client = TestClient(app)

        request = {
            "ticket_type": "tcmr",
            "batch_start": "2024-01-01T00:00:00Z",
            "batch_end": "2024-01-15T00:00:00Z",
            "window_days": 14,
            "updated_at": "2024-01-15T10:00:00Z",
        }

        response = client.post("/v1/jira/batch/tracker/tcmr", json=request)

        assert response.status_code == 500
        assert "Failed to update batch tracker" in response.json()["detail"]
