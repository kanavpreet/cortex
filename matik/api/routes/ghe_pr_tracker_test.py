"""Tests for GHE PR tracker endpoints."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.deps import get_ghe_api_metrics, get_ghe_pr_tracker_dao
from api.routes.ghe_pr_tracker import router
from common.models import GHEPRTracker


@pytest.fixture
def app() -> FastAPI:
    """Create a FastAPI app with the GHE PR tracker router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def mock_dao() -> MagicMock:
    """Create a mock GHEPRTrackerDAO."""
    return MagicMock()


@pytest.fixture
def sample_tracker() -> GHEPRTracker:
    """Create a sample tracker."""
    return GHEPRTracker(
        id=1,
        org_id=12345,
        repo_id=67890,
        cutoff_date=datetime(2024, 1, 15, 10, 0, 0),
        prs_crawled_count=100,
        created_at=datetime(2024, 1, 1, 0, 0, 0),
        updated_at=datetime(2024, 1, 15, 10, 0, 0),
    )


class TestGetTrackerCutoff:
    """Tests for GET /v1/ghe/tracker/{org_id}/{repo_id} endpoint."""

    def test_returns_cutoff_when_tracker_exists(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that cutoff is returned when tracker exists."""
        mock_dao.get_tracker_cutoff.return_value = datetime(2024, 1, 15, 10, 0, 0)
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        client = TestClient(app)

        response = client.get("/v1/ghe/tracker/12345/67890")

        assert response.status_code == 200
        data = response.json()
        assert data["org_id"] == 12345
        assert data["repo_id"] == 67890
        assert data["exists"] is True
        assert data["cutoff_date"] is not None

    def test_returns_exists_false_when_no_tracker(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that exists=false is returned when no tracker exists."""
        mock_dao.get_tracker_cutoff.return_value = None
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        client = TestClient(app)

        response = client.get("/v1/ghe/tracker/12345/67890")

        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is False
        assert data["cutoff_date"] is None

    def test_parses_path_parameters_correctly(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that org_id and repo_id are parsed correctly."""
        mock_dao.get_tracker_cutoff.return_value = None
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        client = TestClient(app)

        client.get("/v1/ghe/tracker/12345/67890")

        mock_dao.get_tracker_cutoff.assert_called_once_with(12345, 67890)

    def test_records_metrics_on_success(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that metrics are recorded on successful retrieval."""
        mock_dao.get_tracker_cutoff.return_value = None
        mock_metrics = MagicMock()
        mock_record = MagicMock()
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app)

        response = client.get("/v1/ghe/tracker/12345/67890")

        assert response.status_code == 200
        mock_metrics.start_operation.assert_called_once_with("get", "tracker")
        mock_record.assert_called_once_with(True)

    def test_records_metrics_on_exception(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that metrics record failure when exception is raised."""
        mock_dao.get_tracker_cutoff.side_effect = Exception("Database error")
        mock_metrics = MagicMock()
        mock_record = MagicMock()
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/v1/ghe/tracker/12345/67890")

        assert response.status_code == 500
        mock_record.assert_called_once_with(False)

    def test_continues_when_metrics_recording_fails_on_exception(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that original exception propagates even if metrics recording fails."""
        mock_dao.get_tracker_cutoff.side_effect = Exception("Database error")
        mock_metrics = MagicMock()
        mock_record = MagicMock(side_effect=Exception("Metrics error"))
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/v1/ghe/tracker/12345/67890")

        # Original exception should propagate
        assert response.status_code == 500


class TestUpdateTracker:
    """Tests for POST /v1/ghe/tracker endpoint."""

    def test_upserts_tracker_successfully(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test successful tracker upsert."""
        mock_dao.upsert_tracker.return_value = True
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        client = TestClient(app)

        request = {
            "org_id": 12345,
            "repo_id": 67890,
            "cutoff_date": "2024-01-15T10:00:00Z",
            "prs_crawled_count": 100,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-15T10:00:00Z",
        }

        response = client.post("/v1/ghe/tracker", json=request)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["org_id"] == 12345
        assert data["repo_id"] == 67890

    def test_returns_400_when_org_id_is_zero(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that 400 is returned when org_id is zero."""
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        client = TestClient(app)

        request = {
            "org_id": 0,
            "repo_id": 67890,
            "cutoff_date": "2024-01-15T10:00:00Z",
            "prs_crawled_count": 100,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-15T10:00:00Z",
        }

        response = client.post("/v1/ghe/tracker", json=request)

        assert response.status_code == 400
        assert "org_id and repo_id are required" in response.json()["detail"]

    def test_returns_400_when_repo_id_is_zero(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that 400 is returned when repo_id is zero."""
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        client = TestClient(app)

        request = {
            "org_id": 12345,
            "repo_id": 0,
            "cutoff_date": "2024-01-15T10:00:00Z",
            "prs_crawled_count": 100,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-15T10:00:00Z",
        }

        response = client.post("/v1/ghe/tracker", json=request)

        assert response.status_code == 400
        assert "org_id and repo_id are required" in response.json()["detail"]

    def test_returns_500_when_upsert_fails(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that 500 is returned when upsert fails."""
        mock_dao.upsert_tracker.return_value = False
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        client = TestClient(app)

        request = {
            "org_id": 12345,
            "repo_id": 67890,
            "cutoff_date": "2024-01-15T10:00:00Z",
            "prs_crawled_count": 100,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-15T10:00:00Z",
        }

        response = client.post("/v1/ghe/tracker", json=request)

        assert response.status_code == 500
        assert "Failed to upsert tracker" in response.json()["detail"]

    def test_records_metrics_on_success(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that metrics are recorded on successful upsert."""
        mock_dao.upsert_tracker.return_value = True
        mock_metrics = MagicMock()
        mock_record = MagicMock()
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app)

        request = {
            "org_id": 12345,
            "repo_id": 67890,
            "cutoff_date": "2024-01-15T10:00:00Z",
            "prs_crawled_count": 100,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-15T10:00:00Z",
        }

        response = client.post("/v1/ghe/tracker", json=request)

        assert response.status_code == 200
        mock_metrics.start_operation.assert_called_once_with("upsert", "tracker")
        mock_record.assert_called_once_with(True)

    def test_records_metrics_on_upsert_failure(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that metrics record failure when upsert returns False."""
        mock_dao.upsert_tracker.return_value = False
        mock_metrics = MagicMock()
        mock_record = MagicMock()
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app)

        request = {
            "org_id": 12345,
            "repo_id": 67890,
            "cutoff_date": "2024-01-15T10:00:00Z",
            "prs_crawled_count": 100,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-15T10:00:00Z",
        }

        response = client.post("/v1/ghe/tracker", json=request)

        assert response.status_code == 500
        mock_record.assert_called_once_with(False)

    def test_records_metrics_on_exception(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that metrics record failure when exception is raised."""
        mock_dao.upsert_tracker.side_effect = Exception("Database error")
        mock_metrics = MagicMock()
        mock_record = MagicMock()
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app, raise_server_exceptions=False)

        request = {
            "org_id": 12345,
            "repo_id": 67890,
            "cutoff_date": "2024-01-15T10:00:00Z",
            "prs_crawled_count": 100,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-15T10:00:00Z",
        }

        response = client.post("/v1/ghe/tracker", json=request)

        assert response.status_code == 500
        mock_record.assert_called_once_with(False)

    def test_continues_when_metrics_recording_fails_on_upsert_failure(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that operation continues even if metrics recording fails."""
        mock_dao.upsert_tracker.return_value = False
        mock_metrics = MagicMock()
        mock_record = MagicMock(side_effect=Exception("Metrics error"))
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app)

        request = {
            "org_id": 12345,
            "repo_id": 67890,
            "cutoff_date": "2024-01-15T10:00:00Z",
            "prs_crawled_count": 100,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-15T10:00:00Z",
        }

        response = client.post("/v1/ghe/tracker", json=request)

        # Original error should still be returned
        assert response.status_code == 500
        assert "Failed to upsert tracker" in response.json()["detail"]

    def test_continues_when_metrics_recording_fails_on_exception(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that original exception propagates even if metrics recording fails."""
        mock_dao.upsert_tracker.side_effect = Exception("Database error")
        mock_metrics = MagicMock()
        mock_record = MagicMock(side_effect=Exception("Metrics error"))
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app, raise_server_exceptions=False)

        request = {
            "org_id": 12345,
            "repo_id": 67890,
            "cutoff_date": "2024-01-15T10:00:00Z",
            "prs_crawled_count": 100,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-15T10:00:00Z",
        }

        response = client.post("/v1/ghe/tracker", json=request)

        # Original exception should propagate
        assert response.status_code == 500


class TestGetAllTrackers:
    """Tests for GET /v1/ghe/trackers endpoint."""

    def test_returns_all_trackers(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
        sample_tracker: GHEPRTracker,
    ) -> None:
        """Test successful retrieval of all trackers."""
        mock_dao.get_all_trackers.return_value = [sample_tracker]
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        client = TestClient(app)

        response = client.get("/v1/ghe/trackers")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["org_id"] == 12345
        assert data[0]["repo_id"] == 67890

    def test_returns_empty_list_when_no_trackers(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that empty list is returned when no trackers exist."""
        mock_dao.get_all_trackers.return_value = []
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        client = TestClient(app)

        response = client.get("/v1/ghe/trackers")

        assert response.status_code == 200
        assert response.json() == []

    def test_returns_500_when_dao_fails(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that 500 is returned when DAO fails."""
        mock_dao.get_all_trackers.return_value = None
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        client = TestClient(app)

        response = client.get("/v1/ghe/trackers")

        assert response.status_code == 500
        assert "Failed to get all trackers" in response.json()["detail"]

    def test_records_metrics_on_success(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that metrics are recorded on successful retrieval."""
        mock_dao.get_all_trackers.return_value = []
        mock_metrics = MagicMock()
        mock_record = MagicMock()
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app)

        response = client.get("/v1/ghe/trackers")

        assert response.status_code == 200
        mock_metrics.start_operation.assert_called_once_with("get_all", "tracker")
        mock_record.assert_called_once_with(True)

    def test_records_metrics_on_dao_failure(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that metrics record failure when DAO returns None."""
        mock_dao.get_all_trackers.return_value = None
        mock_metrics = MagicMock()
        mock_record = MagicMock()
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app)

        response = client.get("/v1/ghe/trackers")

        assert response.status_code == 500
        mock_record.assert_called_once_with(False)

    def test_records_metrics_on_exception(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that metrics record failure when exception is raised."""
        mock_dao.get_all_trackers.side_effect = Exception("Database error")
        mock_metrics = MagicMock()
        mock_record = MagicMock()
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/v1/ghe/trackers")

        assert response.status_code == 500
        mock_record.assert_called_once_with(False)

    def test_continues_when_metrics_recording_fails_on_dao_failure(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that operation continues even if metrics recording fails."""
        mock_dao.get_all_trackers.return_value = None
        mock_metrics = MagicMock()
        mock_record = MagicMock(side_effect=Exception("Metrics error"))
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app)

        response = client.get("/v1/ghe/trackers")

        # Original error should still be returned
        assert response.status_code == 500
        assert "Failed to get all trackers" in response.json()["detail"]

    def test_continues_when_metrics_recording_fails_on_exception(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
    ) -> None:
        """Test that original exception propagates even if metrics recording fails."""
        mock_dao.get_all_trackers.side_effect = Exception("Database error")
        mock_metrics = MagicMock()
        mock_record = MagicMock(side_effect=Exception("Metrics error"))
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_tracker_dao] = lambda: mock_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/v1/ghe/trackers")

        # Original exception should propagate
        assert response.status_code == 500
