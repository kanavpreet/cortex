"""Tests for GHE pull request endpoints."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.deps import get_ghe_api_metrics, get_ghe_pr_dao
from api.routes.ghe_pr import router
from common.daos import PRHashInfo


@pytest.fixture
def app() -> FastAPI:
    """Create a FastAPI app with the GHE PR router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def mock_pr_dao() -> MagicMock:
    """Create a mock GHEPRDAO."""
    return MagicMock()


class TestGetPRHashesByRepo:
    """Tests for GET /v1/ghe/pr/hashes/{org_id}/{repo_id} endpoint."""

    def test_returns_hashes_successfully(
        self,
        app: FastAPI,
        mock_pr_dao: MagicMock,
    ) -> None:
        """Test successful retrieval of PR hashes."""
        mock_pr_dao.get_pr_hashes_by_repo_id.return_value = {
            "12345:67890:1": PRHashInfo(
                pull_request_id=1,
                repository_id=67890,
                description_hash="abc123",
                pull_request_summary="Summary 1",
            ),
            "12345:67890:2": PRHashInfo(
                pull_request_id=2,
                repository_id=67890,
                description_hash="def456",
                pull_request_summary="Summary 2",
            ),
        }
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        client = TestClient(app)

        response = client.get("/v1/ghe/pr/hashes/12345/67890")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert any(h["pull_request_id"] == 1 for h in data)
        assert any(h["description_hash"] == "abc123" for h in data)

    def test_returns_empty_list_when_no_prs(
        self,
        app: FastAPI,
        mock_pr_dao: MagicMock,
    ) -> None:
        """Test that empty list is returned when no PRs exist."""
        mock_pr_dao.get_pr_hashes_by_repo_id.return_value = {}
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        client = TestClient(app)

        response = client.get("/v1/ghe/pr/hashes/12345/67890")

        assert response.status_code == 200
        assert response.json() == []

    def test_returns_500_when_dao_fails(
        self,
        app: FastAPI,
        mock_pr_dao: MagicMock,
    ) -> None:
        """Test that 500 is returned when DAO fails."""
        mock_pr_dao.get_pr_hashes_by_repo_id.return_value = None
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        client = TestClient(app)

        response = client.get("/v1/ghe/pr/hashes/12345/67890")

        assert response.status_code == 500
        assert "Failed to get PR hashes" in response.json()["detail"]

    def test_parses_path_parameters_correctly(
        self,
        app: FastAPI,
        mock_pr_dao: MagicMock,
    ) -> None:
        """Test that org_id and repo_id are parsed correctly."""
        mock_pr_dao.get_pr_hashes_by_repo_id.return_value = {}
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        client = TestClient(app)

        client.get("/v1/ghe/pr/hashes/12345/67890")

        mock_pr_dao.get_pr_hashes_by_repo_id.assert_called_once_with(12345, 67890)

    def test_records_metrics_on_success(
        self,
        app: FastAPI,
        mock_pr_dao: MagicMock,
    ) -> None:
        """Test that metrics are recorded on successful retrieval."""
        mock_pr_dao.get_pr_hashes_by_repo_id.return_value = {}
        mock_metrics = MagicMock()
        mock_record = MagicMock()
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app)

        response = client.get("/v1/ghe/pr/hashes/12345/67890")

        assert response.status_code == 200
        mock_metrics.start_operation.assert_called_once_with(
            "get_hashes", "pull_request"
        )
        mock_record.assert_called_once_with(True)

    def test_records_metrics_on_dao_failure(
        self,
        app: FastAPI,
        mock_pr_dao: MagicMock,
    ) -> None:
        """Test that metrics record failure when DAO returns None."""
        mock_pr_dao.get_pr_hashes_by_repo_id.return_value = None
        mock_metrics = MagicMock()
        mock_record = MagicMock()
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app)

        response = client.get("/v1/ghe/pr/hashes/12345/67890")

        assert response.status_code == 500
        mock_record.assert_called_once_with(False)

    def test_records_metrics_on_exception(
        self,
        app: FastAPI,
        mock_pr_dao: MagicMock,
    ) -> None:
        """Test that metrics record failure when exception is raised."""
        mock_pr_dao.get_pr_hashes_by_repo_id.side_effect = Exception("Database error")
        mock_metrics = MagicMock()
        mock_record = MagicMock()
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/v1/ghe/pr/hashes/12345/67890")

        assert response.status_code == 500
        mock_record.assert_called_once_with(False)

    def test_continues_when_metrics_recording_fails_on_hash_failure(
        self,
        app: FastAPI,
        mock_pr_dao: MagicMock,
    ) -> None:
        """Test that operation continues even if metrics recording fails."""
        mock_pr_dao.get_pr_hashes_by_repo_id.return_value = None
        mock_metrics = MagicMock()
        mock_record = MagicMock(side_effect=Exception("Metrics error"))
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app)

        response = client.get("/v1/ghe/pr/hashes/12345/67890")

        assert response.status_code == 500
        assert "Failed to get PR hashes" in response.json()["detail"]

    def test_continues_when_metrics_recording_fails_on_hash_exception(
        self,
        app: FastAPI,
        mock_pr_dao: MagicMock,
    ) -> None:
        """Test that original exception propagates even if metrics recording fails."""
        mock_pr_dao.get_pr_hashes_by_repo_id.side_effect = Exception("Database error")
        mock_metrics = MagicMock()
        mock_record = MagicMock(side_effect=Exception("Metrics error"))
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/v1/ghe/pr/hashes/12345/67890")

        assert response.status_code == 500


class TestGetGHEPR:
    """Tests for GET /v1/ghe/pr endpoint."""

    def test_returns_prs_for_time_range(
        self,
        app: FastAPI,
        mock_pr_dao: MagicMock,
    ) -> None:
        """Test successful retrieval of PRs within a time range."""
        mock_pr_dao.find_prs_by_time_range.return_value = []
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        client = TestClient(app)

        response = client.get(
            "/v1/ghe/pr",
            params={
                "start_time": "2024-01-01T00:00:00",
                "end_time": "2024-01-02T00:00:00",
            },
        )

        assert response.status_code == 200
        assert response.json() == []
        mock_pr_dao.find_prs_by_time_range.assert_called_once()

    def test_passes_services_filter(
        self,
        app: FastAPI,
        mock_pr_dao: MagicMock,
    ) -> None:
        """Test that comma-separated services string is split and forwarded."""
        mock_pr_dao.find_prs_by_time_range.return_value = []
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        client = TestClient(app)

        client.get(
            "/v1/ghe/pr",
            params={
                "start_time": "2024-01-01T00:00:00",
                "end_time": "2024-01-02T00:00:00",
                "services": "svc-a, svc-b",
            },
        )

        _, _, services_arg, _ = mock_pr_dao.find_prs_by_time_range.call_args.args
        assert services_arg == ["svc-a", "svc-b"]

    def test_returns_400_on_invalid_time_field(
        self,
        app: FastAPI,
        mock_pr_dao: MagicMock,
    ) -> None:
        """Test that ValueError from DAO is surfaced as 400."""
        mock_pr_dao.find_prs_by_time_range.side_effect = ValueError(
            "invalid time_field"
        )
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        client = TestClient(app)

        response = client.get(
            "/v1/ghe/pr",
            params={
                "start_time": "2024-01-01T00:00:00",
                "end_time": "2024-01-02T00:00:00",
                "time_field": "bad_column",
            },
        )

        assert response.status_code == 400
        assert "invalid time_field" in response.json()["detail"]

    def test_returns_500_when_dao_returns_none(
        self,
        app: FastAPI,
        mock_pr_dao: MagicMock,
    ) -> None:
        """Test that None return from DAO surfaces as 500."""
        mock_pr_dao.find_prs_by_time_range.return_value = None
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        client = TestClient(app)

        response = client.get(
            "/v1/ghe/pr",
            params={
                "start_time": "2024-01-01T00:00:00",
                "end_time": "2024-01-02T00:00:00",
            },
        )

        assert response.status_code == 500
        assert "Failed to query GHE prs" in response.json()["detail"]


class TestLookupPRServices:
    """Tests for POST /v1/ghe/pr/services/lookup endpoint."""

    def test_returns_services_successfully(
        self,
        app: FastAPI,
        mock_pr_dao: MagicMock,
    ) -> None:
        """Test successful PR services lookup."""
        mock_pr_dao.get_services_by_pr_identifiers.return_value = {
            ("airbnb", "treehouse", 42): ["treehouse-api", "treehouse-web"],
        }
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        client = TestClient(app)

        request = {"lookups": [{"org": "airbnb", "repo": "treehouse", "pr_number": 42}]}
        response = client.post("/v1/ghe/pr/services/lookup", json=request)

        assert response.status_code == 200
        data = response.json()
        assert data["airbnb:treehouse:42"] == ["treehouse-api", "treehouse-web"]

    def test_empty_lookups_returns_empty(
        self,
        app: FastAPI,
        mock_pr_dao: MagicMock,
    ) -> None:
        """Test that empty lookups list returns empty dict."""
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        client = TestClient(app)

        response = client.post("/v1/ghe/pr/services/lookup", json={"lookups": []})

        assert response.status_code == 200
        assert response.json() == {}

    def test_records_metrics_on_success(
        self,
        app: FastAPI,
        mock_pr_dao: MagicMock,
    ) -> None:
        """Test that metrics are recorded on successful lookup."""
        mock_pr_dao.get_services_by_pr_identifiers.return_value = {}
        mock_metrics = MagicMock()
        mock_record = MagicMock()
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app)

        request = {"lookups": [{"org": "airbnb", "repo": "treehouse", "pr_number": 1}]}
        response = client.post("/v1/ghe/pr/services/lookup", json=request)

        assert response.status_code == 200
        mock_metrics.start_operation.assert_called_once_with(
            "services_lookup", "pull_request"
        )
        mock_record.assert_called_once_with(True)

    def test_records_metrics_on_exception(
        self,
        app: FastAPI,
        mock_pr_dao: MagicMock,
    ) -> None:
        """Test that metrics record failure when exception is raised."""
        mock_pr_dao.get_services_by_pr_identifiers.side_effect = Exception("DB error")
        mock_metrics = MagicMock()
        mock_record = MagicMock()
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app, raise_server_exceptions=False)

        request = {"lookups": [{"org": "airbnb", "repo": "treehouse", "pr_number": 1}]}
        response = client.post("/v1/ghe/pr/services/lookup", json=request)

        assert response.status_code == 500
        mock_record.assert_called_once_with(False)

    def test_continues_when_metrics_recording_fails_on_exception(
        self,
        app: FastAPI,
        mock_pr_dao: MagicMock,
    ) -> None:
        """Test that original exception propagates even if metrics recording fails."""
        mock_pr_dao.get_services_by_pr_identifiers.side_effect = Exception("DB error")
        mock_metrics = MagicMock()
        mock_record = MagicMock(side_effect=Exception("Metrics error"))
        mock_metrics.start_operation.return_value = mock_record
        app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_pr_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        client = TestClient(app, raise_server_exceptions=False)

        request = {"lookups": [{"org": "airbnb", "repo": "treehouse", "pr_number": 1}]}
        response = client.post("/v1/ghe/pr/services/lookup", json=request)

        assert response.status_code == 500
