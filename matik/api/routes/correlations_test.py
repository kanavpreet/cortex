"""Tests for correlation endpoints."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.correlations import router
from api.routes.deps import get_reliability_correlation_dao


@pytest.fixture
def app() -> FastAPI:
    """Create a FastAPI app with the correlations router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def mock_correlation_dao() -> MagicMock:
    return MagicMock()


class TestGetCorrelationEvents:
    """Tests for GET /v1/correlation/reliability/events/{anchor_entity_id}."""

    def test_returns_events(
        self,
        app: FastAPI,
        mock_correlation_dao: MagicMock,
    ) -> None:
        from common.models.reliability_correlation import ReliabilityCorrelation

        corr = ReliabilityCorrelation(
            id=1,
            anchor_entity_id="INC-99",
            correlation_type="SERVICE_MATCH",
            entity_type="github_pr",
            entity_id="PR-1",
        )
        mock_correlation_dao.find_by_anchor.return_value = [corr]
        app.dependency_overrides[get_reliability_correlation_dao] = lambda: (
            mock_correlation_dao
        )
        client = TestClient(app)

        response = client.get("/v1/correlation/reliability/events/INC-99")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["entity_id"] == "PR-1"

    def test_deduplicates_preferring_service_match_over_llm(
        self,
        app: FastAPI,
        mock_correlation_dao: MagicMock,
    ) -> None:
        from common.models.reliability_correlation import ReliabilityCorrelation

        service_match = ReliabilityCorrelation(
            id=1,
            anchor_entity_id="INC-99",
            correlation_type="SERVICE_MATCH",
            entity_type="github_pr",
            entity_id="PR-1",
        )
        llm_match = ReliabilityCorrelation(
            id=2,
            anchor_entity_id="INC-99",
            correlation_type="LLM",
            entity_type="github_pr",
            entity_id="PR-1",
        )
        mock_correlation_dao.find_by_anchor.return_value = [service_match, llm_match]
        app.dependency_overrides[get_reliability_correlation_dao] = lambda: (
            mock_correlation_dao
        )
        client = TestClient(app)

        response = client.get("/v1/correlation/reliability/events/INC-99")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["correlation_type"] == "SERVICE_MATCH"

    def test_returns_llm_when_no_service_match(
        self,
        app: FastAPI,
        mock_correlation_dao: MagicMock,
    ) -> None:
        from common.models.reliability_correlation import ReliabilityCorrelation

        llm_match = ReliabilityCorrelation(
            id=1,
            anchor_entity_id="INC-99",
            correlation_type="LLM",
            entity_type="github_pr",
            entity_id="PR-1",
        )
        mock_correlation_dao.find_by_anchor.return_value = [llm_match]
        app.dependency_overrides[get_reliability_correlation_dao] = lambda: (
            mock_correlation_dao
        )
        client = TestClient(app)

        response = client.get("/v1/correlation/reliability/events/INC-99")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["correlation_type"] == "LLM"

    def test_returns_empty_list_when_none(
        self,
        app: FastAPI,
        mock_correlation_dao: MagicMock,
    ) -> None:
        mock_correlation_dao.find_by_anchor.return_value = []
        app.dependency_overrides[get_reliability_correlation_dao] = lambda: (
            mock_correlation_dao
        )
        client = TestClient(app)

        response = client.get("/v1/correlation/reliability/events/INC-MISSING")

        assert response.status_code == 200
        assert response.json() == []
