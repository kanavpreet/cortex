"""Tests for GHE org crawl tracker endpoints."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.deps import get_ghe_api_metrics, get_ghe_org_crawl_tracker_dao
from api.routes.ghe_org_crawl_tracker import router


@pytest.fixture
def app() -> FastAPI:
    """Create a FastAPI app with the org crawl tracker router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def mock_dao() -> MagicMock:
    """Create a mock GHEOrgCrawlTrackerDAO."""
    return MagicMock()


def _override(app: FastAPI, mock_dao: MagicMock) -> TestClient:
    app.dependency_overrides[get_ghe_org_crawl_tracker_dao] = lambda: mock_dao
    app.dependency_overrides[get_ghe_api_metrics] = lambda: None
    return TestClient(app)


class TestGetOrgWatermark:
    """Tests for GET /v1/ghe/org-crawl-tracker/{org_id}."""

    def test_returns_watermark_when_exists(
        self, app: FastAPI, mock_dao: MagicMock
    ) -> None:
        mock_dao.get_last_crawled_at.return_value = datetime(2024, 1, 15, 10, 0, 0)
        client = _override(app, mock_dao)

        response = client.get("/v1/ghe/org-crawl-tracker/12345")

        assert response.status_code == 200
        data = response.json()
        assert data["org_id"] == 12345
        assert data["exists"] is True
        assert data["last_crawled_at"] is not None
        mock_dao.get_last_crawled_at.assert_called_once_with(12345)

    def test_returns_exists_false_when_absent(
        self, app: FastAPI, mock_dao: MagicMock
    ) -> None:
        mock_dao.get_last_crawled_at.return_value = None
        client = _override(app, mock_dao)

        response = client.get("/v1/ghe/org-crawl-tracker/12345")

        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is False
        assert data["last_crawled_at"] is None


class TestUpdateOrgWatermark:
    """Tests for POST /v1/ghe/org-crawl-tracker."""

    def test_upserts_watermark(self, app: FastAPI, mock_dao: MagicMock) -> None:
        mock_dao.upsert_watermark.return_value = True
        client = _override(app, mock_dao)

        response = client.post(
            "/v1/ghe/org-crawl-tracker",
            json={
                "org_id": 12345,
                "last_crawled_at": "2024-01-15T10:00:00",
            },
        )

        assert response.status_code == 200
        assert response.json()["success"] is True
        mock_dao.upsert_watermark.assert_called_once()

    def test_rejects_zero_org_id(self, app: FastAPI, mock_dao: MagicMock) -> None:
        client = _override(app, mock_dao)

        response = client.post(
            "/v1/ghe/org-crawl-tracker",
            json={"org_id": 0, "last_crawled_at": "2024-01-15T10:00:00"},
        )

        assert response.status_code == 400
        mock_dao.upsert_watermark.assert_not_called()

    def test_returns_500_when_dao_fails(
        self, app: FastAPI, mock_dao: MagicMock
    ) -> None:
        mock_dao.upsert_watermark.return_value = False
        client = _override(app, mock_dao)

        response = client.post(
            "/v1/ghe/org-crawl-tracker",
            json={"org_id": 12345, "last_crawled_at": "2024-01-15T10:00:00"},
        )

        assert response.status_code == 500


class TestMetricsAndErrors:
    """Tests for metrics instrumentation and error propagation."""

    def _client(
        self,
        app: FastAPI,
        mock_dao: MagicMock,
        mock_metrics: MagicMock,
        *,
        raise_server: bool = True,
    ) -> TestClient:
        app.dependency_overrides[get_ghe_org_crawl_tracker_dao] = lambda: mock_dao
        app.dependency_overrides[get_ghe_api_metrics] = lambda: mock_metrics
        return TestClient(app, raise_server_exceptions=raise_server)

    def test_get_records_metrics_on_success(
        self, app: FastAPI, mock_dao: MagicMock
    ) -> None:
        mock_dao.get_last_crawled_at.return_value = datetime(2024, 1, 15, 10, 0, 0)
        mock_metrics = MagicMock()
        record = MagicMock()
        mock_metrics.start_operation.return_value = record
        client = self._client(app, mock_dao, mock_metrics)

        response = client.get("/v1/ghe/org-crawl-tracker/12345")

        assert response.status_code == 200
        mock_metrics.start_operation.assert_called_once_with("get", "org_crawl_tracker")
        record.assert_called_once_with(True)

    def test_get_records_metrics_on_exception(
        self, app: FastAPI, mock_dao: MagicMock
    ) -> None:
        mock_dao.get_last_crawled_at.side_effect = Exception("boom")
        mock_metrics = MagicMock()
        record = MagicMock()
        mock_metrics.start_operation.return_value = record
        client = self._client(app, mock_dao, mock_metrics, raise_server=False)

        response = client.get("/v1/ghe/org-crawl-tracker/12345")

        assert response.status_code == 500
        record.assert_called_once_with(False)

    def test_get_propagates_when_metrics_recording_fails(
        self, app: FastAPI, mock_dao: MagicMock
    ) -> None:
        mock_dao.get_last_crawled_at.side_effect = Exception("boom")
        mock_metrics = MagicMock()
        mock_metrics.start_operation.return_value = MagicMock(
            side_effect=Exception("metrics down")
        )
        client = self._client(app, mock_dao, mock_metrics, raise_server=False)

        response = client.get("/v1/ghe/org-crawl-tracker/12345")

        assert response.status_code == 500

    def test_post_records_metrics_on_success(
        self, app: FastAPI, mock_dao: MagicMock
    ) -> None:
        mock_dao.upsert_watermark.return_value = True
        mock_metrics = MagicMock()
        record = MagicMock()
        mock_metrics.start_operation.return_value = record
        client = self._client(app, mock_dao, mock_metrics)

        response = client.post(
            "/v1/ghe/org-crawl-tracker",
            json={"org_id": 12345, "last_crawled_at": "2024-01-15T10:00:00"},
        )

        assert response.status_code == 200
        mock_metrics.start_operation.assert_called_once_with(
            "upsert", "org_crawl_tracker"
        )
        record.assert_called_once_with(True)

    def test_post_records_metrics_when_upsert_fails(
        self, app: FastAPI, mock_dao: MagicMock
    ) -> None:
        mock_dao.upsert_watermark.return_value = False
        mock_metrics = MagicMock()
        record = MagicMock()
        mock_metrics.start_operation.return_value = record
        client = self._client(app, mock_dao, mock_metrics, raise_server=False)

        response = client.post(
            "/v1/ghe/org-crawl-tracker",
            json={"org_id": 12345, "last_crawled_at": "2024-01-15T10:00:00"},
        )

        assert response.status_code == 500
        record.assert_called_once_with(False)

    def test_post_continues_when_metrics_fail_on_upsert_failure(
        self, app: FastAPI, mock_dao: MagicMock
    ) -> None:
        """upsert returns False and the failure-metric recording itself raises."""
        mock_dao.upsert_watermark.return_value = False
        mock_metrics = MagicMock()
        mock_metrics.start_operation.return_value = MagicMock(
            side_effect=Exception("metrics down")
        )
        client = self._client(app, mock_dao, mock_metrics, raise_server=False)

        response = client.post(
            "/v1/ghe/org-crawl-tracker",
            json={"org_id": 12345, "last_crawled_at": "2024-01-15T10:00:00"},
        )

        assert response.status_code == 500

    def test_post_records_metrics_on_exception(
        self, app: FastAPI, mock_dao: MagicMock
    ) -> None:
        mock_dao.upsert_watermark.side_effect = Exception("boom")
        mock_metrics = MagicMock()
        record = MagicMock()
        mock_metrics.start_operation.return_value = record
        client = self._client(app, mock_dao, mock_metrics, raise_server=False)

        response = client.post(
            "/v1/ghe/org-crawl-tracker",
            json={"org_id": 12345, "last_crawled_at": "2024-01-15T10:00:00"},
        )

        assert response.status_code == 500
        record.assert_called_once_with(False)

    def test_post_propagates_when_metrics_recording_fails(
        self, app: FastAPI, mock_dao: MagicMock
    ) -> None:
        """upsert raises and the failure-metric recording itself raises too."""
        mock_dao.upsert_watermark.side_effect = Exception("boom")
        mock_metrics = MagicMock()
        mock_metrics.start_operation.return_value = MagicMock(
            side_effect=Exception("metrics down")
        )
        client = self._client(app, mock_dao, mock_metrics, raise_server=False)

        response = client.post(
            "/v1/ghe/org-crawl-tracker",
            json={"org_id": 12345, "last_crawled_at": "2024-01-15T10:00:00"},
        )

        assert response.status_code == 500
