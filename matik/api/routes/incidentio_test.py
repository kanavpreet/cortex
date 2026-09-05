"""Tests for Incident.io incident endpoints."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.deps import (
    get_incidentio_incident_dao,
    get_incidentio_tracker_dao,
)
from api.routes.incidentio import router
from common.models.incidentio_incident import IncidentIOIncident
from common.models.incidentio_tracker import IncidentIOTracker


@pytest.fixture
def app() -> FastAPI:
    """Create a FastAPI app with the incidentio router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def mock_incident_dao() -> MagicMock:
    """Create a mock IncidentIOIncidentDAO."""
    return MagicMock()


@pytest.fixture
def mock_tracker_dao() -> MagicMock:
    """Create a mock IncidentIOTrackerDAO."""
    return MagicMock()


class TestGetLastRecorded:
    """Tests for GET /v1/incidentio/incident/tracker/lastrecorded endpoint."""

    def test_returns_tracker_when_found(
        self, app: FastAPI, mock_tracker_dao: MagicMock
    ) -> None:
        """Test that endpoint returns tracker when found."""
        tracker = IncidentIOTracker(
            timestamp=datetime(2024, 1, 15, 10, 30, 0),
            status="OK",
            initial_sync_complete=True,
        )
        mock_tracker_dao.find_last_recorded.return_value = tracker
        app.dependency_overrides[get_incidentio_tracker_dao] = lambda: mock_tracker_dao
        client = TestClient(app)

        response = client.get("/v1/incidentio/incident/tracker/lastrecorded")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "OK"
        assert data["initial_sync_complete"] is True

    def test_returns_null_when_not_found(
        self, app: FastAPI, mock_tracker_dao: MagicMock
    ) -> None:
        """Test that endpoint returns null when tracker not found."""
        mock_tracker_dao.find_last_recorded.return_value = None
        app.dependency_overrides[get_incidentio_tracker_dao] = lambda: mock_tracker_dao
        client = TestClient(app)

        response = client.get("/v1/incidentio/incident/tracker/lastrecorded")

        assert response.status_code == 200
        assert response.json() is None


class TestPostLastRecorded:
    """Tests for POST /v1/incidentio/incident/tracker/lastrecorded endpoint."""

    def test_updates_tracker_success(
        self, app: FastAPI, mock_tracker_dao: MagicMock
    ) -> None:
        """Test that endpoint updates tracker successfully."""
        mock_tracker_dao.update_tracker.return_value = True
        app.dependency_overrides[get_incidentio_tracker_dao] = lambda: mock_tracker_dao
        client = TestClient(app)

        response = client.post(
            "/v1/incidentio/incident/tracker/lastrecorded",
            json={
                "timestamp": "2024-01-15T10:30:00",
                "status": "OK",
                "initial_sync_complete": True,
            },
        )

        assert response.status_code == 200
        assert response.json()["message"] == "tracker updated successfully"

    def test_returns_500_on_failure(
        self, app: FastAPI, mock_tracker_dao: MagicMock
    ) -> None:
        """Test that endpoint returns 500 when update fails."""
        mock_tracker_dao.update_tracker.return_value = False
        app.dependency_overrides[get_incidentio_tracker_dao] = lambda: mock_tracker_dao
        client = TestClient(app)

        response = client.post(
            "/v1/incidentio/incident/tracker/lastrecorded",
            json={
                "timestamp": "2024-01-15T10:30:00",
                "status": "OK",
            },
        )

        assert response.status_code == 500
        assert "Failed to update tracker" in response.json()["detail"]


class TestGetIncident:
    """Tests for GET /v1/incidentio/incident/{reference_id} endpoint."""

    def test_returns_incident_when_found(
        self, app: FastAPI, mock_incident_dao: MagicMock
    ) -> None:
        """Test that endpoint returns incident when found."""
        incident = IncidentIOIncident(
            id=1,
            incident_id="ID-123",
            reference_id="INC-123",
            severity="Sev-1",
            slack_channel_id="C123456",
            status="open",
            visibility="public",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            reported_at=datetime(2024, 1, 15, 10, 30, 0),
            updated_at=datetime(2024, 1, 15, 10, 30, 0),
        )
        mock_incident_dao.find_incident.return_value = incident
        app.dependency_overrides[get_incidentio_incident_dao] = lambda: (
            mock_incident_dao
        )
        client = TestClient(app)

        response = client.get("/v1/incidentio/incident/INC-123")

        assert response.status_code == 200
        data = response.json()
        assert data["incident_id"] == "ID-123"
        assert data["reference_id"] == "INC-123"

    def test_returns_404_when_not_found(
        self, app: FastAPI, mock_incident_dao: MagicMock
    ) -> None:
        """Test that endpoint returns 404 when incident not found."""
        mock_incident_dao.find_incident.return_value = None
        app.dependency_overrides[get_incidentio_incident_dao] = lambda: (
            mock_incident_dao
        )
        client = TestClient(app)

        response = client.get("/v1/incidentio/incident/INC-nonexistent")

        assert response.status_code == 404
        assert "Incident not found" in response.json()["detail"]


class TestGetIncidentLlmData:
    """Tests for POST /v1/incidentio/incident/hashes endpoint."""

    def test_returns_llm_data(self, app: FastAPI, mock_incident_dao: MagicMock) -> None:
        """Test that endpoint returns LLM data for given incident IDs."""
        mock_incident_dao.get_llm_data_by_incident_ids.return_value = {
            "ID-1": {
                "root_cause_summary_hash": "abc123",
                "root_cause_summary": "Root cause 1",
                "description_summary": "Resolution 1",
            },
            "ID-2": {
                "root_cause_summary_hash": "def456",
                "root_cause_summary": "Root cause 2",
                "description_summary": None,
            },
            "ID-3": {
                "root_cause_summary_hash": None,
                "root_cause_summary": None,
                "description_summary": None,
            },
        }
        app.dependency_overrides[get_incidentio_incident_dao] = lambda: (
            mock_incident_dao
        )
        client = TestClient(app)

        response = client.post(
            "/v1/incidentio/incident/hashes",
            json={"incident_ids": ["ID-1", "ID-2", "ID-3"]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ID-1"]["root_cause_summary_hash"] == "abc123"
        assert data["ID-1"]["root_cause_summary"] == "Root cause 1"
        assert data["ID-2"]["description_summary"] is None
        assert data["ID-3"]["root_cause_summary_hash"] is None

    def test_returns_empty_for_empty_list(
        self, app: FastAPI, mock_incident_dao: MagicMock
    ) -> None:
        """Test that endpoint returns empty dict for empty list."""
        app.dependency_overrides[get_incidentio_incident_dao] = lambda: (
            mock_incident_dao
        )
        client = TestClient(app)

        response = client.post(
            "/v1/incidentio/incident/hashes", json={"incident_ids": []}
        )

        assert response.status_code == 200
        assert response.json() == {}
