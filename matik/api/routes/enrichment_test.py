"""Tests for unified enrichment hash endpoints."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.deps import (
    get_ghe_pr_dao,
    get_incidentio_incident_dao,
    get_jira_issues_dao,
)
from api.routes.enrichment import router
from common.daos.ghe_pr_dao import PRHashInfo
from common.daos.incidentio_incident_dao import IncidentIOHashInfo
from common.daos.jira_issues_dao import JiraHashInfo

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> FastAPI:
    """Create a FastAPI app with only the enrichment router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def mock_incidentio_dao() -> MagicMock:
    """Mock IncidentIOIncidentDAO."""
    return MagicMock()


@pytest.fixture
def mock_jira_dao() -> MagicMock:
    """Mock JiraIssuesDAO."""
    return MagicMock()


@pytest.fixture
def mock_ghe_pr_dao() -> MagicMock:
    """Mock GHEPRDAO."""
    return MagicMock()


@pytest.fixture
def client(
    app: FastAPI,
    mock_incidentio_dao: MagicMock,
    mock_jira_dao: MagicMock,
    mock_ghe_pr_dao: MagicMock,
) -> TestClient:
    """TestClient with all three DAOs overridden."""
    app.dependency_overrides[get_incidentio_incident_dao] = lambda: mock_incidentio_dao
    app.dependency_overrides[get_jira_issues_dao] = lambda: mock_jira_dao
    app.dependency_overrides[get_ghe_pr_dao] = lambda: mock_ghe_pr_dao
    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /v1/enrichment/hashes/batch
# ---------------------------------------------------------------------------


class TestBatchFetchHashes:
    """Tests for POST /v1/enrichment/hashes/batch."""

    def test_invalid_source_type_returns_400(self, client: TestClient) -> None:
        """Unknown source_type is rejected with 400."""
        response = client.post(
            "/v1/enrichment/hashes/batch",
            json={"source_type": "unknown", "entity_ids": [{"incident_id": "INC-1"}]},
        )
        assert response.status_code == 400
        assert "Invalid source_type" in response.json()["detail"]
        assert "unknown" in response.json()["detail"]

    def test_empty_entity_ids_returns_empty_results(self, client: TestClient) -> None:
        """Empty entity_ids list returns {} results without hitting any DAO."""
        response = client.post(
            "/v1/enrichment/hashes/batch",
            json={"source_type": "incidentio", "entity_ids": []},
        )
        assert response.status_code == 200
        assert response.json() == {"results": {}}

    # -- incidentio --

    def test_incidentio_returns_hashes_keyed_by_canonical_key(
        self,
        client: TestClient,
        mock_incidentio_dao: MagicMock,
    ) -> None:
        """Incident hashes are returned keyed by json.dumps(entity_id, sort_keys=True)."""
        mock_incidentio_dao.get_incident_hashes_by_ids.return_value = {
            "INC-1": IncidentIOHashInfo(
                incident_id="INC-1",
                root_cause_summary_hash="rcsh_abc",
                description_hash="dh_abc",
            )
        }

        response = client.post(
            "/v1/enrichment/hashes/batch",
            json={
                "source_type": "incidentio",
                "entity_ids": [{"incident_id": "INC-1"}],
            },
        )

        assert response.status_code == 200
        results = response.json()["results"]
        # Canonical key is json.dumps({"incident_id": "INC-1"}, sort_keys=True)
        canon = '{"incident_id": "INC-1"}'
        assert canon in results
        assert results[canon]["root_cause_summary_hash"] == "rcsh_abc"
        assert results[canon]["description_hash"] == "dh_abc"
        # LLM summaries are NOT included — only hash columns
        assert "root_cause_summary" not in results[canon]
        assert "description_summary" not in results[canon]

    def test_incidentio_missing_entity_excluded_from_results(
        self,
        client: TestClient,
        mock_incidentio_dao: MagicMock,
    ) -> None:
        """Entities not in the DB are simply absent from the results dict."""
        # DB only has INC-1, but INC-999 is requested too
        mock_incidentio_dao.get_incident_hashes_by_ids.return_value = {
            "INC-1": IncidentIOHashInfo(
                incident_id="INC-1",
                root_cause_summary_hash="rcsh_abc",
                description_hash="dh_abc",
            )
        }

        response = client.post(
            "/v1/enrichment/hashes/batch",
            json={
                "source_type": "incidentio",
                "entity_ids": [
                    {"incident_id": "INC-1"},
                    {"incident_id": "INC-999"},
                ],
            },
        )

        assert response.status_code == 200
        results = response.json()["results"]
        assert '{"incident_id": "INC-1"}' in results
        assert '{"incident_id": "INC-999"}' not in results

    def test_incidentio_dao_error_returns_500(
        self,
        client: TestClient,
        mock_incidentio_dao: MagicMock,
    ) -> None:
        """DAO returning None (database error) causes a 500."""
        mock_incidentio_dao.get_incident_hashes_by_ids.return_value = None

        response = client.post(
            "/v1/enrichment/hashes/batch",
            json={
                "source_type": "incidentio",
                "entity_ids": [{"incident_id": "INC-1"}],
            },
        )

        assert response.status_code == 500

    # -- jira --

    def test_jira_returns_hashes_keyed_by_canonical_key(
        self,
        client: TestClient,
        mock_jira_dao: MagicMock,
    ) -> None:
        """JIRA hashes returned with summary_hash and comments_hash columns."""
        mock_jira_dao.get_issue_hashes_by_keys.return_value = {
            "OPS-123": JiraHashInfo(
                issue_key="OPS-123",
                summary_hash="sh_abc",
                comments_hash="ch_abc",
                issue_summary="Issue summary",
                issue_comments_summary="Comments summary",
            )
        }

        response = client.post(
            "/v1/enrichment/hashes/batch",
            json={
                "source_type": "jira",
                "entity_ids": [{"issue_key": "OPS-123"}],
            },
        )

        assert response.status_code == 200
        results = response.json()["results"]
        canon = '{"issue_key": "OPS-123"}'
        assert canon in results
        assert results[canon]["summary_hash"] == "sh_abc"
        assert results[canon]["comments_hash"] == "ch_abc"
        # LLM summaries are NOT included — only hash columns
        assert "issue_summary" not in results[canon]

    def test_jira_dao_error_returns_500(
        self,
        client: TestClient,
        mock_jira_dao: MagicMock,
    ) -> None:
        """DAO returning None (database error) causes a 500."""
        mock_jira_dao.get_issue_hashes_by_keys.return_value = None

        response = client.post(
            "/v1/enrichment/hashes/batch",
            json={
                "source_type": "jira",
                "entity_ids": [{"issue_key": "OPS-1"}],
            },
        )

        assert response.status_code == 500

    # -- ghe_pr --

    def test_ghe_pr_returns_hashes_keyed_by_canonical_key(
        self,
        client: TestClient,
        mock_ghe_pr_dao: MagicMock,
    ) -> None:
        """GHE PR hashes returned with description_hash column."""
        # DAO key uses GitHub API IDs: "org_id:repo_id:pr_id"
        mock_ghe_pr_dao.get_pr_hashes_by_ids.return_value = {
            "10:20:30": PRHashInfo(
                pull_request_id=30,
                repository_id=20,
                description_hash="dh_xyz",
                pull_request_summary="PR summary",
            )
        }

        response = client.post(
            "/v1/enrichment/hashes/batch",
            json={
                "source_type": "ghe_pr",
                "entity_ids": [
                    {"org_id": 10, "repository_id": 20, "pull_request_id": 30}
                ],
            },
        )

        assert response.status_code == 200
        results = response.json()["results"]
        # Canonical key is json.dumps with sorted keys
        canon = '{"org_id": 10, "pull_request_id": 30, "repository_id": 20}'
        assert canon in results
        assert results[canon]["description_hash"] == "dh_xyz"
        # pull_request_summary and services are NOT included — only hash columns
        assert "pull_request_summary" not in results[canon]

    def test_ghe_pr_entity_not_found_excluded_from_results(
        self,
        client: TestClient,
        mock_ghe_pr_dao: MagicMock,
    ) -> None:
        """PRs not in the DB are absent from results."""
        mock_ghe_pr_dao.get_pr_hashes_by_ids.return_value = {}

        response = client.post(
            "/v1/enrichment/hashes/batch",
            json={
                "source_type": "ghe_pr",
                "entity_ids": [
                    {"org_id": 10, "repository_id": 20, "pull_request_id": 99}
                ],
            },
        )

        assert response.status_code == 200
        assert response.json() == {"results": {}}

    def test_ghe_pr_dao_error_returns_500(
        self,
        client: TestClient,
        mock_ghe_pr_dao: MagicMock,
    ) -> None:
        """DAO returning None (database error) causes a 500."""
        mock_ghe_pr_dao.get_pr_hashes_by_ids.return_value = None

        response = client.post(
            "/v1/enrichment/hashes/batch",
            json={
                "source_type": "ghe_pr",
                "entity_ids": [
                    {"org_id": 10, "repository_id": 20, "pull_request_id": 30}
                ],
            },
        )

        assert response.status_code == 500

    # -- incident_channel_summary --

    def test_incident_channel_summary_returns_hashes_keyed_by_canonical_key(
        self,
        client: TestClient,
        mock_incidentio_dao: MagicMock,
    ) -> None:
        """Channel summary hashes are keyed by reference_id, unlike incidentio's
        incident_id, and returned with only the incident_channel_summary_hash column."""
        mock_incidentio_dao.get_incident_channel_summary_hashes_by_reference_ids.return_value = {
            "INC-1": "icsh_abc",
        }

        response = client.post(
            "/v1/enrichment/hashes/batch",
            json={
                "source_type": "incident_channel_summary",
                "entity_ids": [{"reference_id": "INC-1"}],
            },
        )

        assert response.status_code == 200
        results = response.json()["results"]
        canon = '{"reference_id": "INC-1"}'
        assert canon in results
        assert results[canon]["incident_channel_summary_hash"] == "icsh_abc"

    def test_incident_channel_summary_missing_entity_excluded_from_results(
        self,
        client: TestClient,
        mock_incidentio_dao: MagicMock,
    ) -> None:
        """Entities not in the DB are simply absent from the results dict."""
        mock_incidentio_dao.get_incident_channel_summary_hashes_by_reference_ids.return_value = {
            "INC-1": "icsh_abc",
        }

        response = client.post(
            "/v1/enrichment/hashes/batch",
            json={
                "source_type": "incident_channel_summary",
                "entity_ids": [
                    {"reference_id": "INC-1"},
                    {"reference_id": "INC-999"},
                ],
            },
        )

        assert response.status_code == 200
        results = response.json()["results"]
        assert '{"reference_id": "INC-1"}' in results
        assert '{"reference_id": "INC-999"}' not in results

    def test_incident_channel_summary_dao_error_returns_500(
        self,
        client: TestClient,
        mock_incidentio_dao: MagicMock,
    ) -> None:
        """DAO returning None (database error) causes a 500."""
        mock_incidentio_dao.get_incident_channel_summary_hashes_by_reference_ids.return_value = None

        response = client.post(
            "/v1/enrichment/hashes/batch",
            json={
                "source_type": "incident_channel_summary",
                "entity_ids": [{"reference_id": "INC-1"}],
            },
        )

        assert response.status_code == 500

    def test_batch_passes_all_entity_ids_to_dao(
        self,
        client: TestClient,
        mock_incidentio_dao: MagicMock,
    ) -> None:
        """All entity_ids in the request are forwarded to the DAO in one call."""
        mock_incidentio_dao.get_incident_hashes_by_ids.return_value = {}

        client.post(
            "/v1/enrichment/hashes/batch",
            json={
                "source_type": "incidentio",
                "entity_ids": [
                    {"incident_id": "INC-1"},
                    {"incident_id": "INC-2"},
                    {"incident_id": "INC-3"},
                ],
            },
        )

        # DAO called once with all three IDs
        mock_incidentio_dao.get_incident_hashes_by_ids.assert_called_once_with(
            ["INC-1", "INC-2", "INC-3"]
        )


# ---------------------------------------------------------------------------
# POST /v1/enrichment/hashes (single-entity fallback)
# ---------------------------------------------------------------------------


class TestFetchSingleHash:
    """Tests for POST /v1/enrichment/hashes."""

    def test_invalid_source_type_returns_400(self, client: TestClient) -> None:
        """Unknown source_type is rejected with 400."""
        response = client.post(
            "/v1/enrichment/hashes",
            json={"source_type": "bad_type", "entity_id": {"incident_id": "INC-1"}},
        )
        assert response.status_code == 400

    def test_incidentio_returns_flat_hash_dict(
        self,
        client: TestClient,
        mock_incidentio_dao: MagicMock,
    ) -> None:
        """Single-entity response is a flat {hash_col: value} dict (no 'results' wrapper)."""
        mock_incidentio_dao.get_incident_hashes_by_ids.return_value = {
            "INC-42": IncidentIOHashInfo(
                incident_id="INC-42",
                root_cause_summary_hash="rcsh_42",
                description_hash="dh_42",
            )
        }

        response = client.post(
            "/v1/enrichment/hashes",
            json={"source_type": "incidentio", "entity_id": {"incident_id": "INC-42"}},
        )

        assert response.status_code == 200
        body: dict[str, Any] = response.json()
        # Response is a flat dict, not wrapped in {"results": ...}
        assert "results" not in body
        assert body["root_cause_summary_hash"] == "rcsh_42"
        assert body["description_hash"] == "dh_42"

    def test_entity_not_found_returns_empty_dict(
        self,
        client: TestClient,
        mock_incidentio_dao: MagicMock,
    ) -> None:
        """Entity not in DB returns {} (Enricher treats missing hash as 'run enrichment')."""
        mock_incidentio_dao.get_incident_hashes_by_ids.return_value = {}

        response = client.post(
            "/v1/enrichment/hashes",
            json={"source_type": "incidentio", "entity_id": {"incident_id": "INC-999"}},
        )

        assert response.status_code == 200
        assert response.json() == {}

    def test_jira_returns_flat_hash_dict(
        self,
        client: TestClient,
        mock_jira_dao: MagicMock,
    ) -> None:
        """Single JIRA entity returns summary_hash and comments_hash."""
        mock_jira_dao.get_issue_hashes_by_keys.return_value = {
            "OPS-1": JiraHashInfo(
                issue_key="OPS-1",
                summary_hash="sh_1",
                comments_hash="ch_1",
                issue_summary=None,
                issue_comments_summary=None,
            )
        }

        response = client.post(
            "/v1/enrichment/hashes",
            json={"source_type": "jira", "entity_id": {"issue_key": "OPS-1"}},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["summary_hash"] == "sh_1"
        assert body["comments_hash"] == "ch_1"

    def test_ghe_pr_returns_flat_hash_dict(
        self,
        client: TestClient,
        mock_ghe_pr_dao: MagicMock,
    ) -> None:
        """Single GHE PR entity returns description_hash."""
        mock_ghe_pr_dao.get_pr_hashes_by_ids.return_value = {
            "5:6:7": PRHashInfo(
                pull_request_id=7,
                repository_id=6,
                description_hash="dh_5_6_7",
                pull_request_summary=None,
            )
        }

        response = client.post(
            "/v1/enrichment/hashes",
            json={
                "source_type": "ghe_pr",
                "entity_id": {"org_id": 5, "repository_id": 6, "pull_request_id": 7},
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["description_hash"] == "dh_5_6_7"

    def test_incident_channel_summary_returns_flat_hash_dict(
        self,
        client: TestClient,
        mock_incidentio_dao: MagicMock,
    ) -> None:
        """Single channel-summary entity returns incident_channel_summary_hash."""
        mock_incidentio_dao.get_incident_channel_summary_hashes_by_reference_ids.return_value = {
            "INC-42": "icsh_42",
        }

        response = client.post(
            "/v1/enrichment/hashes",
            json={
                "source_type": "incident_channel_summary",
                "entity_id": {"reference_id": "INC-42"},
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert "results" not in body
        assert body["incident_channel_summary_hash"] == "icsh_42"
