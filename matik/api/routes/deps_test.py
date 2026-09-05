"""Tests for dependency injection functions."""

from unittest.mock import MagicMock

import pytest

from api.routes.deps import (
    get_db_metrics,
    get_engine,
    get_enigmatologist_config,
    get_ghe_api_metrics,
    get_ghe_org_crawl_tracker_dao,
    get_ghe_pr_dao,
    get_ghe_pr_tracker_dao,
    get_incidentio_api_metrics,
    get_incidentio_incident_dao,
    get_incidentio_tracker_dao,
    get_jira_batch_tracker_dao,
    get_jira_issues_dao,
    get_reliability_correlation_dao,
    get_reliability_correlation_group_dao,
    get_sqs_client,
)
from common.daos import (
    GHEPRDAO,
    GHEOrgCrawlTrackerDAO,
    GHEPRTrackerDAO,
    IncidentIOIncidentDAO,
    IncidentIOTrackerDAO,
    JiraBatchTrackerDAO,
    JiraIssuesDAO,
    ReliabilityCorrelationDAO,
    ReliabilityCorrelationGroupDAO,
)


@pytest.fixture
def mock_engine() -> MagicMock:
    """Create a mock database engine."""
    return MagicMock()


@pytest.fixture
def mock_request_with_engine(mock_engine: MagicMock) -> MagicMock:
    """Create a mock request with an initialized engine."""
    request = MagicMock()
    request.app.state.engine = mock_engine
    return request


@pytest.fixture
def mock_request_without_engine() -> MagicMock:
    """Create a mock request without an engine."""
    request = MagicMock()
    request.app.state.engine = None
    return request


class TestGetEngine:
    """Tests for get_engine dependency."""

    def test_returns_engine_when_initialized(
        self, mock_request_with_engine: MagicMock, mock_engine: MagicMock
    ) -> None:
        """Test that engine is returned when properly initialized."""
        result = get_engine(mock_request_with_engine)

        assert result == mock_engine

    def test_raises_runtime_error_when_engine_not_initialized(
        self, mock_request_without_engine: MagicMock
    ) -> None:
        """Test that RuntimeError is raised when engine is None."""
        with pytest.raises(RuntimeError, match="Database engine not initialized"):
            get_engine(mock_request_without_engine)


class TestDAOFactories:
    """Tests for DAO factory functions."""

    def test_get_ghe_pr_dao_returns_dao(self, mock_engine: MagicMock) -> None:
        """Test that GHEPRDAO is created with engine."""
        result = get_ghe_pr_dao(mock_engine, metrics=None)

        assert isinstance(result, GHEPRDAO)

    def test_get_ghe_pr_tracker_dao_returns_dao(self, mock_engine: MagicMock) -> None:
        """Test that GHEPRTrackerDAO is created with engine."""
        result = get_ghe_pr_tracker_dao(mock_engine, metrics=None)

        assert isinstance(result, GHEPRTrackerDAO)

    def test_get_ghe_org_crawl_tracker_dao_returns_dao(
        self, mock_engine: MagicMock
    ) -> None:
        """Test that GHEOrgCrawlTrackerDAO is created with engine."""
        result = get_ghe_org_crawl_tracker_dao(mock_engine, metrics=None)

        assert isinstance(result, GHEOrgCrawlTrackerDAO)

    def test_get_jira_issues_dao_returns_dao(self, mock_engine: MagicMock) -> None:
        """Test that JiraIssuesDAO is created with engine."""
        result = get_jira_issues_dao(mock_engine, metrics=None)

        assert isinstance(result, JiraIssuesDAO)

    def test_get_jira_batch_tracker_dao_returns_dao(
        self, mock_engine: MagicMock
    ) -> None:
        """Test that JiraBatchTrackerDAO is created with engine."""
        result = get_jira_batch_tracker_dao(mock_engine, metrics=None)

        assert isinstance(result, JiraBatchTrackerDAO)


class TestGetDBMetrics:
    """Tests for get_db_metrics dependency."""

    def test_returns_metrics_when_initialized(self) -> None:
        """Test that db_metrics is returned when properly initialized."""
        mock_metrics = MagicMock()
        request = MagicMock()
        request.app.state.db_metrics = mock_metrics

        result = get_db_metrics(request)

        assert result == mock_metrics

    def test_returns_none_when_metrics_is_none(self) -> None:
        """Test that None is returned when metrics is None."""
        request = MagicMock()
        request.app.state.db_metrics = None

        result = get_db_metrics(request)

        assert result is None

    def test_returns_none_when_no_db_metrics_attr(self) -> None:
        """Test that None is returned when db_metrics attr doesn't exist."""
        request = MagicMock(spec=["app"])
        request.app = MagicMock(spec=["state"])
        request.app.state = MagicMock(spec=[])  # No db_metrics attribute

        result = get_db_metrics(request)

        assert result is None


class TestGetGHEAPIMetrics:
    """Tests for get_ghe_api_metrics dependency."""

    def test_returns_metrics_when_initialized(self) -> None:
        """Test that ghe_api_metrics is returned when properly initialized."""
        mock_metrics = MagicMock()
        request = MagicMock()
        request.app.state.ghe_api_metrics = mock_metrics

        result = get_ghe_api_metrics(request)

        assert result == mock_metrics

    def test_returns_none_when_metrics_is_none(self) -> None:
        """Test that None is returned when metrics is None."""
        request = MagicMock()
        request.app.state.ghe_api_metrics = None

        result = get_ghe_api_metrics(request)

        assert result is None

    def test_returns_none_when_no_ghe_api_metrics_attr(self) -> None:
        """Test that None is returned when ghe_api_metrics attr doesn't exist."""
        request = MagicMock(spec=["app"])
        request.app = MagicMock(spec=["state"])
        request.app.state = MagicMock(spec=[])  # No ghe_api_metrics attribute

        result = get_ghe_api_metrics(request)

        assert result is None


class TestGetEnigmatologistConfig:
    """Tests for get_enigmatologist_config dependency."""

    def test_returns_config_when_set(self) -> None:
        mock_config = MagicMock()
        request = MagicMock()
        request.app.state.enigmatologist_config = mock_config

        result = get_enigmatologist_config(request)
        assert result == mock_config

    def test_returns_none_when_attr_missing(self) -> None:
        request = MagicMock(spec=["app"])
        request.app = MagicMock(spec=["state"])
        request.app.state = MagicMock(spec=[])

        result = get_enigmatologist_config(request)
        assert result is None


class TestGetSQSClient:
    """Tests for get_sqs_client dependency."""

    def test_returns_client_when_set(self) -> None:
        mock_client = MagicMock()
        request = MagicMock()
        request.app.state.sqs_client = mock_client

        result = get_sqs_client(request)
        assert result == mock_client

    def test_returns_none_when_attr_missing(self) -> None:
        request = MagicMock(spec=["app"])
        request.app = MagicMock(spec=["state"])
        request.app.state = MagicMock(spec=[])

        result = get_sqs_client(request)
        assert result is None


class TestGetIncidentIOAPIMetrics:
    """Tests for get_incidentio_api_metrics dependency."""

    def test_returns_metrics_when_initialized(self) -> None:
        mock_metrics = MagicMock()
        request = MagicMock()
        request.app.state.incidentio_api_metrics = mock_metrics

        result = get_incidentio_api_metrics(request)
        assert result == mock_metrics

    def test_returns_none_when_metrics_is_none(self) -> None:
        request = MagicMock()
        request.app.state.incidentio_api_metrics = None

        result = get_incidentio_api_metrics(request)
        assert result is None

    def test_returns_none_when_attr_missing(self) -> None:
        request = MagicMock(spec=["app"])
        request.app = MagicMock(spec=["state"])
        request.app.state = MagicMock(spec=[])

        result = get_incidentio_api_metrics(request)
        assert result is None


class TestNewDAOFactories:
    """Tests for new DAO factory functions."""

    def test_get_incidentio_incident_dao_returns_dao(
        self, mock_engine: MagicMock
    ) -> None:
        result = get_incidentio_incident_dao(mock_engine, metrics=None)
        assert isinstance(result, IncidentIOIncidentDAO)

    def test_get_incidentio_tracker_dao_returns_dao(
        self, mock_engine: MagicMock
    ) -> None:
        result = get_incidentio_tracker_dao(mock_engine, metrics=None)
        assert isinstance(result, IncidentIOTrackerDAO)

    def test_get_reliability_correlation_group_dao_returns_dao(
        self, mock_engine: MagicMock
    ) -> None:
        result = get_reliability_correlation_group_dao(mock_engine, metrics=None)
        assert isinstance(result, ReliabilityCorrelationGroupDAO)

    def test_get_reliability_correlation_dao_returns_dao(
        self, mock_engine: MagicMock
    ) -> None:
        result = get_reliability_correlation_dao(mock_engine, metrics=None)
        assert isinstance(result, ReliabilityCorrelationDAO)
