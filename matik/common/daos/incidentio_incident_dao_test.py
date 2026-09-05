"""Tests for Incident.io Incident DAO."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError
from structlog.testing import capture_logs

from common.daos.base_dao import WriteOutcome
from common.daos.incidentio_incident_dao import (
    IncidentIOHashInfo,
    IncidentIOIncidentDAO,
)
from common.models.incidentio_incident import IncidentIOIncident


class TestIncidentIOIncidentDAO:
    """Test suite for IncidentIOIncidentDAO class."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> IncidentIOIncidentDAO:
        """Create DAO instance with mock engine."""
        return IncidentIOIncidentDAO(mock_engine)

    def test_constructor(self, mock_engine: MagicMock) -> None:
        """Test DAO constructor stores engine."""
        dao = IncidentIOIncidentDAO(mock_engine)
        assert dao._engine is mock_engine


class TestFindIncident:
    """Tests for find_incident method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> IncidentIOIncidentDAO:
        """Create DAO instance with mock engine."""
        return IncidentIOIncidentDAO(mock_engine)

    def test_find_incident_success(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test find returns incident when found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_row = MagicMock()
        mock_row._mapping = {
            "id": 1,
            "incident_id": "01K3H5K30V3TECAF9G2HD1X5ZB",
            "reference_id": "INC-1234",
            "severity": "Sev-1",
            "slack_channel_id": "C1234567890",
            "status": "closed",
            "visibility": "public",
            "created_at": datetime(2024, 1, 15, 10, 30, 0),
            "reported_at": datetime(2024, 1, 15, 10, 30, 0),
            "updated_at": datetime(2024, 1, 15, 12, 0, 0),
            "accepted_at": datetime(2024, 1, 15, 10, 35, 0),
            "declined_at": None,
            "canceled_at": None,
            "resolved_at": datetime(2024, 1, 15, 11, 30, 0),
            "impact_started_at": datetime(2024, 1, 15, 10, 0, 0),
            "closed_at": datetime(2024, 1, 15, 12, 0, 0),
            "impacted_parties": ["Guest", "Host"],
            "impacted_core_functions": ["Atrium"],
            "core_booking_hosting_flow_impacted": True,
            "affected_services": ["a4w-common", "biztech_alertpipelines"],
            "resolution_statement": "Rolled back deployment",
            "root_cause_service": "a4w-common",
            "root_cause_change_type": "Code Deploy",
            "root_cause_change_type_other": None,
            "root_cause_change_type_config": None,
            "environment": "Production",
            "detection_methods": ["alert"],
            "detection_link": "https://pagerduty.com/incident/123",
            "root_cause_summary": "Deployment caused memory leak",
            "description_summary": "Rolled back deployment",
        }
        result = MagicMock()
        result.fetchone.return_value = mock_row
        conn.execute.return_value = result

        incident = dao.find_incident(reference_id="INC-1234")

        assert incident is not None
        assert incident.id == 1
        assert incident.incident_id == "01K3H5K30V3TECAF9G2HD1X5ZB"
        assert incident.reference_id == "INC-1234"
        assert incident.severity == "Sev-1"
        assert incident.status == "closed"
        assert incident.impacted_parties == ["Guest", "Host"]

    def test_find_incident_not_found(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test find returns None when incident not found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchone.return_value = None
        conn.execute.return_value = result

        incident = dao.find_incident(reference_id="INC-9999")

        assert incident is None

    def test_find_incident_database_error(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test find returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database connection error")

        with capture_logs() as cap_logs:
            incident = dao.find_incident(reference_id="INC-1234")

        assert incident is None
        error_logs = [
            log for log in cap_logs if log.get("event") == "error finding incident"
        ]
        assert len(error_logs) == 1
        assert error_logs[0]["exc_info"] is True


class TestUpdateLlmFields:
    """Tests for update_llm_fields method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> IncidentIOIncidentDAO:
        """Create DAO instance with mock engine."""
        return IncidentIOIncidentDAO(mock_engine)

    def test_update_llm_fields_success(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test update_llm_fields returns True when row is updated."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.rowcount = 1
        conn.execute.return_value = result

        success = dao.update_llm_fields(
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            root_cause_summary="Deployment caused memory leak",
            root_cause_summary_hash="hash123abc",
            description_summary="Database pool exhaustion incident",
            description_hash="desc456",
        )

        assert success is WriteOutcome.WRITTEN
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_update_llm_fields_no_match(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test update_llm_fields returns BASE_NOT_FOUND when no row matches."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.rowcount = 0
        conn.execute.return_value = result

        success = dao.update_llm_fields(
            incident_id="NONEXISTENT-ID",
            root_cause_summary="Root cause",
            root_cause_summary_hash="hash",
            description_summary="Description",
            description_hash="desc_hash",
        )

        assert success is WriteOutcome.BASE_NOT_FOUND

    def test_update_llm_fields_database_error(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test update_llm_fields returns ERROR on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Update error")

        success = dao.update_llm_fields(
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            root_cause_summary="Root cause",
            root_cause_summary_hash="hash",
            description_summary="Description",
            description_hash="desc_hash",
        )

        assert success is WriteOutcome.ERROR

    def test_update_llm_fields_with_none_values(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """All-None values returns WRITTEN without hitting the DB."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        success = dao.update_llm_fields(
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            root_cause_summary=None,
            root_cause_summary_hash=None,
            description_summary=None,
            description_hash=None,
        )

        assert success is WriteOutcome.WRITTEN
        conn.execute.assert_not_called()

    def test_update_llm_fields_forwards_entered_at_to_guard(
        self, dao: IncidentIOIncidentDAO
    ) -> None:
        """``entered_at`` is a pass-through knob (ADR 024): when a caller has
        one (e.g. a future message-driven caller of this convenience
        wrapper), it must reach ``update_llm_fields_from_message`` so the
        staleness guard actually applies — the previous version of this
        method had no such parameter and was always unconditional."""
        with patch.object(
            dao, "update_llm_fields_from_message", return_value=WriteOutcome.WRITTEN
        ) as mock_update:
            dao.update_llm_fields(
                incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
                root_cause_summary="Root cause",
                root_cause_summary_hash="hash",
                description_summary="Description",
                description_hash="desc_hash",
                entered_at=datetime(2026, 1, 1),
            )

        mock_update.assert_called_once_with(
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            root_cause_summary="Root cause",
            root_cause_summary_hash="hash",
            description_summary="Description",
            description_hash="desc_hash",
            entered_at=datetime(2026, 1, 1),
        )

    def test_update_llm_fields_defaults_entered_at_to_none(
        self, dao: IncidentIOIncidentDAO
    ) -> None:
        """No ``entered_at`` passed -> forwarded as ``None`` -> unconditional
        write, preserving this method's pre-ADR-024 behavior for existing
        callers that don't have a message envelope to source one from."""
        with patch.object(
            dao, "update_llm_fields_from_message", return_value=WriteOutcome.WRITTEN
        ) as mock_update:
            dao.update_llm_fields(
                incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
                root_cause_summary="Root cause",
                root_cause_summary_hash="hash",
                description_summary="Description",
                description_hash="desc_hash",
            )

        assert mock_update.call_args.kwargs["entered_at"] is None


class TestFindIncidentsByReferenceIds:
    """Tests for find_incidents_by_reference_ids method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> IncidentIOIncidentDAO:
        return IncidentIOIncidentDAO(mock_engine)

    def _make_mock_row(self, reference_id: str, incident_id: str) -> MagicMock:
        mock_row = MagicMock()
        mock_row._mapping = {
            "id": 1,
            "incident_id": incident_id,
            "reference_id": reference_id,
            "severity": "Sev-1",
            "slack_channel_id": "C123",
            "status": "open",
            "visibility": "public",
            "created_at": datetime(2024, 1, 15, 10, 30, 0),
            "reported_at": datetime(2024, 1, 15, 10, 30, 0),
            "updated_at": datetime(2024, 1, 15, 10, 30, 0),
            "accepted_at": None,
            "declined_at": None,
            "canceled_at": None,
            "resolved_at": None,
            "impact_started_at": None,
            "closed_at": None,
            "impacted_parties": None,
            "impacted_core_functions": None,
            "core_booking_hosting_flow_impacted": None,
            "affected_services": None,
            "resolution_statement": None,
            "root_cause_service": None,
            "root_cause_change_type": None,
            "root_cause_change_type_other": None,
            "root_cause_change_type_config": None,
            "environment": None,
            "detection_methods": None,
            "detection_link": None,
            "primary_team": None,
            "primary_slack_handle": None,
            "summary": None,
            "root_cause_summary_hash": None,
            "root_cause_summary": None,
            "description_summary": None,
        }
        return mock_row

    def test_returns_matching_incidents(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test returns list of incidents for matching reference_ids."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [
            self._make_mock_row("INC-1", "ID-1"),
            self._make_mock_row("INC-2", "ID-2"),
        ]

        result = dao.find_incidents_by_reference_ids(["INC-1", "INC-2"])

        assert len(result) == 2
        assert result[0].reference_id == "INC-1"
        assert result[1].reference_id == "INC-2"

    def test_returns_empty_list_for_empty_input(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test returns empty list without hitting the database for empty input."""
        result = dao.find_incidents_by_reference_ids([])

        assert result == []
        mock_engine.connect.assert_not_called()

    def test_returns_empty_list_when_none_found(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test returns empty list when no incidents match."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []

        result = dao.find_incidents_by_reference_ids(["INC-nonexistent"])

        assert result == []

    def test_returns_empty_list_on_database_error(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test returns empty list on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        result = dao.find_incidents_by_reference_ids(["INC-1", "INC-2"])

        assert result == []

    def test_records_metrics_on_success(self, mock_engine: MagicMock) -> None:
        """Test find_incidents_by_reference_ids records metrics on success."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = IncidentIOIncidentDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []

        dao.find_incidents_by_reference_ids(["INC-1"])

        mock_metrics.start_query.assert_called_with("select", "incidentio_incidents")
        record_fn.assert_called_with(None)

    def test_records_metrics_on_error(self, mock_engine: MagicMock) -> None:
        """Test find_incidents_by_reference_ids records metrics on database error."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = IncidentIOIncidentDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Database error")
        conn.execute.side_effect = error

        dao.find_incidents_by_reference_ids(["INC-1"])

        record_fn.assert_called_once_with(error)

    def test_handles_metrics_exception(self, mock_engine: MagicMock) -> None:
        """Test find_incidents_by_reference_ids handles metrics exception gracefully."""
        mock_metrics = MagicMock()
        mock_metrics.start_query.return_value.side_effect = Exception("Metrics error")
        dao = IncidentIOIncidentDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        result = dao.find_incidents_by_reference_ids(["INC-1"])

        assert result == []


class TestInsertNewIncident:
    """Tests for insert_new_incident method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> IncidentIOIncidentDAO:
        """Create DAO instance with mock engine."""
        return IncidentIOIncidentDAO(mock_engine)

    def test_insert_success(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test successful insert returns new ID."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.lastrowid = 123
        conn.execute.return_value = result

        incident = IncidentIOIncident(
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            reference_id="INC-1234",
            severity="Sev-1",
            slack_channel_id="C1234567890",
            status="open",
            visibility="public",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            reported_at=datetime(2024, 1, 15, 10, 30, 0),
            updated_at=datetime(2024, 1, 15, 10, 30, 0),
        )
        incident_id = dao.insert_new_incident(incident)

        assert incident_id == 123
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_insert_database_error(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test insert returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Duplicate key error")

        incident = IncidentIOIncident(
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            reference_id="INC-1234",
            severity="Sev-1",
            slack_channel_id="C1234567890",
            status="open",
            visibility="public",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            reported_at=datetime(2024, 1, 15, 10, 30, 0),
            updated_at=datetime(2024, 1, 15, 10, 30, 0),
        )
        incident_id = dao.insert_new_incident(incident)

        assert incident_id is None


class TestUpdateIncident:
    """Tests for update_incident method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> IncidentIOIncidentDAO:
        """Create DAO instance with mock engine."""
        return IncidentIOIncidentDAO(mock_engine)

    def test_update_success(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test successful update returns incident ID."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        conn.execute.return_value = result

        incident = IncidentIOIncident(
            id=1,
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            reference_id="INC-1234",
            severity="Sev-2",
            slack_channel_id="C1234567890",
            status="closed",
            visibility="public",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            reported_at=datetime(2024, 1, 15, 10, 30, 0),
            updated_at=datetime(2024, 1, 15, 12, 0, 0),
        )
        incident_id = dao.update_incident(incident)

        assert incident_id == 1
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_update_database_error(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test update returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Update error")

        incident = IncidentIOIncident(
            id=1,
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            reference_id="INC-1234",
            severity="Sev-2",
            slack_channel_id="C1234567890",
            status="closed",
            visibility="public",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            reported_at=datetime(2024, 1, 15, 10, 30, 0),
            updated_at=datetime(2024, 1, 15, 12, 0, 0),
        )
        incident_id = dao.update_incident(incident)

        assert incident_id is None

    def test_update_records_metrics_on_success(self, mock_engine: MagicMock) -> None:
        """Test update_incident records metrics on success."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = IncidentIOIncidentDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value = MagicMock()

        incident = IncidentIOIncident(
            id=1,
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            reference_id="INC-1234",
            severity="Sev-2",
            slack_channel_id="C1234567890",
            status="closed",
            visibility="public",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            reported_at=datetime(2024, 1, 15, 10, 30, 0),
            updated_at=datetime(2024, 1, 15, 12, 0, 0),
        )
        dao.update_incident(incident)

        mock_metrics.start_query.assert_called_with("update", "incidentio_incidents")
        record_fn.assert_called_with(None)

    def test_update_records_metrics_on_error(self, mock_engine: MagicMock) -> None:
        """Test update_incident records metrics on database error."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = IncidentIOIncidentDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Update error")
        conn.execute.side_effect = error

        incident = IncidentIOIncident(
            id=1,
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            reference_id="INC-1234",
            severity="Sev-2",
            slack_channel_id="C1234567890",
            status="closed",
            visibility="public",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            reported_at=datetime(2024, 1, 15, 10, 30, 0),
            updated_at=datetime(2024, 1, 15, 12, 0, 0),
        )
        dao.update_incident(incident)

        record_fn.assert_called_once_with(error)

    def test_update_handles_metrics_exception(self, mock_engine: MagicMock) -> None:
        """Test update_incident handles metrics exception gracefully."""
        mock_metrics = MagicMock()
        mock_metrics.start_query.return_value.side_effect = Exception("Metrics error")
        dao = IncidentIOIncidentDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Update error")

        incident = IncidentIOIncident(
            id=1,
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            reference_id="INC-1234",
            severity="Sev-2",
            slack_channel_id="C1234567890",
            status="closed",
            visibility="public",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            reported_at=datetime(2024, 1, 15, 10, 30, 0),
            updated_at=datetime(2024, 1, 15, 12, 0, 0),
        )

        result = dao.update_incident(incident)

        assert result is None


class TestInsertOrUpdateIncident:
    """Tests for insert_or_update_incident method (upsert)."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> IncidentIOIncidentDAO:
        """Create DAO instance with mock engine."""
        return IncidentIOIncidentDAO(mock_engine)

    def test_insert_when_not_exists(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test upsert inserts when incident doesn't exist."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        # First call: find returns None (not found)
        find_result = MagicMock()
        find_result.fetchone.return_value = None

        # Second call: insert returns new ID
        insert_result = MagicMock()
        insert_result.lastrowid = 123

        conn.execute.side_effect = [find_result, insert_result]

        incident = IncidentIOIncident(
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            reference_id="INC-1234",
            severity="Sev-1",
            slack_channel_id="C1234567890",
            status="open",
            visibility="public",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            reported_at=datetime(2024, 1, 15, 10, 30, 0),
            updated_at=datetime(2024, 1, 15, 10, 30, 0),
        )
        incident_id = dao.insert_or_update_incident(incident)

        assert incident_id == 123
        assert conn.execute.call_count == 2

    def test_update_when_exists(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test upsert updates when incident exists."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        # First call: find returns existing incident
        mock_row = MagicMock()
        mock_row._mapping = {
            "id": 1,
            "incident_id": "01K3H5K30V3TECAF9G2HD1X5ZB",
            "reference_id": "INC-1234",
            "severity": "Sev-1",
            "slack_channel_id": "C1234567890",
            "status": "open",
            "visibility": "public",
            "created_at": datetime(2024, 1, 15, 10, 30, 0),
            "reported_at": datetime(2024, 1, 15, 10, 30, 0),
            "updated_at": datetime(2024, 1, 15, 10, 30, 0),
            "accepted_at": None,
            "declined_at": None,
            "canceled_at": None,
            "resolved_at": None,
            "impact_started_at": None,
            "closed_at": None,
            "impacted_parties": None,
            "impacted_core_functions": None,
            "core_booking_hosting_flow_impacted": None,
            "affected_services": None,
            "resolution_statement": None,
            "root_cause_service": None,
            "root_cause_change_type": None,
            "root_cause_change_type_other": None,
            "root_cause_change_type_config": None,
            "environment": None,
            "detection_methods": None,
            "detection_link": None,
            "root_cause_summary": None,
            "description_summary": None,
        }
        find_result = MagicMock()
        find_result.fetchone.return_value = mock_row

        # Second call: update succeeds
        update_result = MagicMock()

        conn.execute.side_effect = [find_result, update_result]

        incident = IncidentIOIncident(
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            reference_id="INC-1234",
            severity="Sev-2",
            slack_channel_id="C1234567890",
            status="closed",
            visibility="public",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            reported_at=datetime(2024, 1, 15, 10, 30, 0),
            updated_at=datetime(2024, 1, 15, 12, 0, 0),
        )
        incident_id = dao.insert_or_update_incident(incident)

        assert incident_id == 1
        assert incident.id == 1  # ID should be updated from existing
        assert conn.execute.call_count == 2


class TestUpsertIncidentsBatch:
    """Tests for upsert_incidents_batch method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> IncidentIOIncidentDAO:
        """Create DAO instance with mock engine."""
        return IncidentIOIncidentDAO(mock_engine)

    def test_batch_empty_list(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test batch upsert with empty list returns 0."""
        result = dao.upsert_incidents_batch([])

        assert result == 0

    def test_batch_single_chunk(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test batch upsert with less than BATCH_SIZE incidents."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.rowcount = 5
        conn.execute.return_value = result

        incidents = [
            IncidentIOIncident(
                incident_id=f"ID-{i}",
                reference_id=f"INC-{i}",
                severity="Sev-1",
                slack_channel_id="C1234567890",
                status="open",
                visibility="public",
                created_at=datetime(2024, 1, 15, 10, 30, 0),
                reported_at=datetime(2024, 1, 15, 10, 30, 0),
                updated_at=datetime(2024, 1, 15, 10, 30, 0),
            )
            for i in range(5)
        ]
        affected = dao.upsert_incidents_batch(incidents)

        assert affected == 5
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_batch_multiple_chunks(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test batch upsert with more than BATCH_SIZE incidents."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result1 = MagicMock()
        result1.rowcount = 500
        result2 = MagicMock()
        result2.rowcount = 100
        conn.execute.side_effect = [result1, result2]

        incidents = [
            IncidentIOIncident(
                incident_id=f"ID-{i}",
                reference_id=f"INC-{i}",
                severity="Sev-1",
                slack_channel_id="C1234567890",
                status="open",
                visibility="public",
                created_at=datetime(2024, 1, 15, 10, 30, 0),
                reported_at=datetime(2024, 1, 15, 10, 30, 0),
                updated_at=datetime(2024, 1, 15, 10, 30, 0),
            )
            for i in range(600)
        ]
        affected = dao.upsert_incidents_batch(incidents)

        assert affected == 600
        assert conn.execute.call_count == 2
        assert conn.commit.call_count == 2

    def test_batch_error_in_chunk(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test batch upsert returns None when a chunk fails."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        incidents = [
            IncidentIOIncident(
                incident_id=f"ID-{i}",
                reference_id=f"INC-{i}",
                severity="Sev-1",
                slack_channel_id="C1234567890",
                status="open",
                visibility="public",
                created_at=datetime(2024, 1, 15, 10, 30, 0),
                reported_at=datetime(2024, 1, 15, 10, 30, 0),
                updated_at=datetime(2024, 1, 15, 10, 30, 0),
            )
            for i in range(5)
        ]
        affected = dao.upsert_incidents_batch(incidents)

        assert affected is None

    @patch("common.utils.retry_utils.time.sleep")
    def test_batch_retries_transient_connection_error(
        self,
        mock_sleep: MagicMock,
        dao: IncidentIOIncidentDAO,
        mock_engine: MagicMock,
    ) -> None:
        """A transient 'lost connection' error is retried on a fresh connection."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        good = MagicMock()
        good.rowcount = 5
        orig = Exception(2013, "Lost connection to MySQL server during query")
        conn.execute.side_effect = [OperationalError("INSERT", {}, orig), good]

        incidents = [
            IncidentIOIncident(
                incident_id="ID-1",
                reference_id="INC-1",
                severity="Sev-1",
                slack_channel_id="C1234567890",
                status="open",
                visibility="public",
                created_at=datetime(2024, 1, 15, 10, 30, 0),
                reported_at=datetime(2024, 1, 15, 10, 30, 0),
                updated_at=datetime(2024, 1, 15, 10, 30, 0),
            )
        ]
        affected = dao.upsert_incidents_batch(incidents)

        assert affected == 5
        # Retried on a brand-new connection.
        assert mock_engine.connect.call_count == 2
        assert conn.execute.call_count == 2
        mock_sleep.assert_called_once()


class TestUpsertBatch:
    """Tests for _upsert_batch method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> IncidentIOIncidentDAO:
        """Create DAO instance with mock engine."""
        return IncidentIOIncidentDAO(mock_engine)

    def test_upsert_batch_success(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test _upsert_batch returns rowcount on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.rowcount = 3
        conn.execute.return_value = result

        incidents = [
            IncidentIOIncident(
                incident_id=f"ID-{i}",
                reference_id=f"INC-{i}",
                severity="Sev-1",
                slack_channel_id="C1234567890",
                status="open",
                visibility="public",
                created_at=datetime(2024, 1, 15, 10, 30, 0),
                reported_at=datetime(2024, 1, 15, 10, 30, 0),
                updated_at=datetime(2024, 1, 15, 10, 30, 0),
            )
            for i in range(3)
        ]
        affected = dao._upsert_batch(incidents)

        assert affected == 3

    def test_upsert_batch_empty(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test _upsert_batch returns 0 for empty list."""
        affected = dao._upsert_batch([])

        assert affected == 0

    def test_upsert_batch_database_error(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test _upsert_batch returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        incidents = [
            IncidentIOIncident(
                incident_id="ID-1",
                reference_id="INC-1",
                severity="Sev-1",
                slack_channel_id="C1234567890",
                status="open",
                visibility="public",
                created_at=datetime(2024, 1, 15, 10, 30, 0),
                reported_at=datetime(2024, 1, 15, 10, 30, 0),
                updated_at=datetime(2024, 1, 15, 10, 30, 0),
            )
        ]
        affected = dao._upsert_batch(incidents)

        assert affected is None


class TestGetLlmDataByIncidentIds:
    """Tests for get_llm_data_by_incident_ids method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> IncidentIOIncidentDAO:
        """Create DAO instance with mock engine."""
        return IncidentIOIncidentDAO(mock_engine)

    def test_get_llm_data_success(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_llm_data_by_incident_ids returns dict of LLM data."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        # Mock rows returned from database
        mock_row1 = MagicMock()
        mock_row1.incident_id = "ID-1"
        mock_row1.root_cause_summary_hash = "abc123"
        mock_row1.root_cause_summary = "Root cause 1"
        mock_row1.description_summary = "Resolution 1"

        mock_row2 = MagicMock()
        mock_row2.incident_id = "ID-2"
        mock_row2.root_cause_summary_hash = "def456"
        mock_row2.root_cause_summary = "Root cause 2"
        mock_row2.description_summary = None

        mock_row3 = MagicMock()
        mock_row3.incident_id = "ID-3"
        mock_row3.root_cause_summary_hash = None
        mock_row3.root_cause_summary = None
        mock_row3.description_summary = None

        conn.execute.return_value.fetchall.return_value = [
            mock_row1,
            mock_row2,
            mock_row3,
        ]

        result = dao.get_llm_data_by_incident_ids(["ID-1", "ID-2", "ID-3"])

        assert result["ID-1"]["root_cause_summary_hash"] == "abc123"
        assert result["ID-1"]["root_cause_summary"] == "Root cause 1"
        assert result["ID-1"]["description_summary"] == "Resolution 1"
        assert result["ID-2"]["root_cause_summary_hash"] == "def456"
        assert result["ID-2"]["description_summary"] is None
        assert result["ID-3"]["root_cause_summary_hash"] is None

    def test_get_llm_data_empty_list(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_llm_data_by_incident_ids returns empty dict for empty list."""
        result = dao.get_llm_data_by_incident_ids([])
        assert result == {}

    def test_get_llm_data_no_matches(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_llm_data_by_incident_ids returns empty dict when no incidents found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []

        result = dao.get_llm_data_by_incident_ids(["ID-nonexistent"])

        assert result == {}

    def test_get_llm_data_partial_match(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_llm_data_by_incident_ids returns only matched incidents."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        # Only one incident found out of three requested
        mock_row = MagicMock()
        mock_row.incident_id = "ID-1"
        mock_row.root_cause_summary_hash = "abc123"
        mock_row.root_cause_summary = "Root cause"
        mock_row.description_summary = "Resolution"

        conn.execute.return_value.fetchall.return_value = [mock_row]

        result = dao.get_llm_data_by_incident_ids(["ID-1", "ID-2", "ID-3"])

        # Only ID-1 is in result since it was found in DB
        assert len(result) == 1
        assert result["ID-1"]["root_cause_summary_hash"] == "abc123"

    def test_get_llm_data_database_error(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_llm_data_by_incident_ids returns empty dict on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        result = dao.get_llm_data_by_incident_ids(["ID-1", "ID-2"])

        assert result == {}


class TestDAOWithMetrics:
    """Tests for DAO operations with metrics instrumentation."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def mock_metrics(self) -> MagicMock:
        """Create mock DBMetrics."""
        metrics = MagicMock()
        mock_record = MagicMock()
        metrics.start_query.return_value = mock_record
        return metrics

    @pytest.fixture
    def dao_with_metrics(
        self, mock_engine: MagicMock, mock_metrics: MagicMock
    ) -> IncidentIOIncidentDAO:
        """Create DAO instance with mock engine and metrics."""
        return IncidentIOIncidentDAO(mock_engine, mock_metrics)

    def test_find_incident_records_metrics_on_success(
        self,
        dao_with_metrics: IncidentIOIncidentDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test find_incident records metrics on successful query."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_row = MagicMock()
        mock_row._mapping = {
            "id": 1,
            "incident_id": "ID-1",
            "reference_id": "INC-1",
            "severity": "Sev-1",
            "slack_channel_id": "C123",
            "status": "open",
            "visibility": "public",
            "created_at": datetime(2024, 1, 15, 10, 30, 0),
            "reported_at": datetime(2024, 1, 15, 10, 30, 0),
            "updated_at": datetime(2024, 1, 15, 10, 30, 0),
            "accepted_at": None,
            "declined_at": None,
            "canceled_at": None,
            "resolved_at": None,
            "impact_started_at": None,
            "closed_at": None,
            "impacted_parties": None,
            "impacted_core_functions": None,
            "core_booking_hosting_flow_impacted": None,
            "affected_services": None,
            "resolution_statement": None,
            "root_cause_service": None,
            "primary_team": None,
            "primary_slack_handle": None,
            "summary": None,
            "root_cause_summary_hash": None,
            "root_cause_summary": None,
            "description_summary": None,
        }
        conn.execute.return_value.fetchone.return_value = mock_row

        result = dao_with_metrics.find_incident("INC-1")

        assert result is not None
        mock_metrics.start_query.assert_called_once_with(
            "select", "incidentio_incidents"
        )
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_find_incident_records_metrics_on_not_found(
        self,
        dao_with_metrics: IncidentIOIncidentDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test find_incident records metrics when not found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.return_value = None

        result = dao_with_metrics.find_incident("INC-notfound")

        assert result is None
        mock_metrics.start_query.assert_called_once()
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_find_incident_records_metrics_on_error(
        self,
        dao_with_metrics: IncidentIOIncidentDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test find_incident records metrics on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        result = dao_with_metrics.find_incident("INC-1")

        assert result is None
        mock_metrics.start_query.assert_called_once()
        mock_record = mock_metrics.start_query.return_value
        mock_record.assert_called_once()
        # Error is passed to record function
        assert isinstance(mock_record.call_args[0][0], Exception)

    def test_find_incident_handles_metrics_exception(
        self,
        dao_with_metrics: IncidentIOIncidentDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test find_incident handles exception from metrics recording."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")
        mock_metrics.start_query.return_value.side_effect = Exception("Metrics error")

        # Should not raise, just log warning
        result = dao_with_metrics.find_incident("INC-1")

        assert result is None

    def test_get_llm_data_records_metrics(
        self,
        dao_with_metrics: IncidentIOIncidentDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test get_llm_data_by_incident_ids records metrics."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_row = MagicMock()
        mock_row.incident_id = "ID-1"
        mock_row.root_cause_summary_hash = "abc"
        mock_row.root_cause_summary = "cause"
        mock_row.description_summary = "resolution"
        conn.execute.return_value.fetchall.return_value = [mock_row]

        result = dao_with_metrics.get_llm_data_by_incident_ids(["ID-1"])

        assert len(result) == 1
        mock_metrics.start_query.assert_called_once()
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_get_llm_data_records_metrics_on_error(
        self,
        dao_with_metrics: IncidentIOIncidentDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test get_llm_data_by_incident_ids records metrics on error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        result = dao_with_metrics.get_llm_data_by_incident_ids(["ID-1"])

        assert result == {}
        mock_metrics.start_query.assert_called_once()

    def test_get_llm_data_handles_metrics_exception(
        self,
        dao_with_metrics: IncidentIOIncidentDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test get_llm_data handles metrics exception gracefully."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")
        mock_metrics.start_query.return_value.side_effect = Exception("Metrics error")

        # Should not raise
        result = dao_with_metrics.get_llm_data_by_incident_ids(["ID-1"])

        assert result == {}

    def test_insert_or_update_records_metrics(
        self,
        dao_with_metrics: IncidentIOIncidentDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test insert_or_update_incident records metrics via delegated methods."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        # First call: find returns None (not found)
        find_result = MagicMock()
        find_result.fetchone.return_value = None

        # Second call: insert returns new ID
        insert_result = MagicMock()
        insert_result.lastrowid = 1

        conn.execute.side_effect = [find_result, insert_result]

        incident = IncidentIOIncident(
            incident_id="ID-1",
            reference_id="INC-1",
            severity="Sev-1",
            slack_channel_id="C123",
            status="open",
            visibility="public",
            created_at=datetime(2024, 1, 15),
            reported_at=datetime(2024, 1, 15),
            updated_at=datetime(2024, 1, 15),
        )

        result = dao_with_metrics.insert_or_update_incident(incident)

        assert result == 1
        # Metrics recorded by delegated methods (find_incident then insert_new_incident)
        assert mock_metrics.start_query.call_count == 2

    def test_insert_or_update_records_metrics_on_error(
        self,
        dao_with_metrics: IncidentIOIncidentDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test insert_or_update_incident records metrics on error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        # Error on find_incident call
        conn.execute.side_effect = Exception("Database error")

        incident = IncidentIOIncident(
            incident_id="ID-1",
            reference_id="INC-1",
            severity="Sev-1",
            slack_channel_id="C123",
            status="open",
            visibility="public",
            created_at=datetime(2024, 1, 15),
            reported_at=datetime(2024, 1, 15),
            updated_at=datetime(2024, 1, 15),
        )

        result = dao_with_metrics.insert_or_update_incident(incident)

        # find_incident returns None on error, so insert_new_incident is called and also fails
        assert result is None
        mock_record = mock_metrics.start_query.return_value
        assert mock_record.call_count >= 1

    def test_insert_or_update_handles_metrics_exception(
        self,
        dao_with_metrics: IncidentIOIncidentDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test insert_or_update handles metrics exception gracefully."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        # Database error on first call
        conn.execute.side_effect = Exception("Database error")
        # Metrics recording also fails
        mock_metrics.start_query.return_value.side_effect = Exception("Metrics error")

        incident = IncidentIOIncident(
            incident_id="ID-1",
            reference_id="INC-1",
            severity="Sev-1",
            slack_channel_id="C123",
            status="open",
            visibility="public",
            created_at=datetime(2024, 1, 15),
            reported_at=datetime(2024, 1, 15),
            updated_at=datetime(2024, 1, 15),
        )

        # Should not raise even when metrics recording fails
        result = dao_with_metrics.insert_or_update_incident(incident)

        assert result is None

    def test_update_llm_fields_no_match_records_metrics(
        self,
        dao_with_metrics: IncidentIOIncidentDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test update_llm_fields records metrics when rowcount==0."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result_mock = MagicMock()
        result_mock.rowcount = 0
        conn.execute.return_value = result_mock

        result = dao_with_metrics.update_llm_fields(
            incident_id="NONEXISTENT-ID",
            root_cause_summary="Root cause",
            root_cause_summary_hash="hash",
            description_summary="Description",
            description_hash="desc_hash",
        )

        assert result is WriteOutcome.BASE_NOT_FOUND
        mock_metrics.start_query.assert_called_with("update", "incidentio_incidents")
        mock_metrics.start_query.return_value.assert_called_with(None)

    def test_update_llm_fields_handles_metrics_exception(
        self,
        dao_with_metrics: IncidentIOIncidentDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test update_llm_fields handles metrics exception gracefully."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")
        mock_metrics.start_query.return_value.side_effect = Exception("Metrics error")

        result = dao_with_metrics.update_llm_fields(
            incident_id="ID-1",
            root_cause_summary="Root cause",
            root_cause_summary_hash="hash",
            description_summary="Description",
            description_hash="desc_hash",
        )

        assert result is WriteOutcome.ERROR

    def test_update_llm_fields_records_metrics_on_success(
        self,
        dao_with_metrics: IncidentIOIncidentDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test update_llm_fields records metrics on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result_mock = MagicMock()
        result_mock.rowcount = 1
        conn.execute.return_value = result_mock

        dao_with_metrics.update_llm_fields(
            incident_id="ID-1",
            root_cause_summary="Root cause",
            root_cause_summary_hash="hash123",
            description_summary="Description",
            description_hash="desc_hash",
        )

        mock_metrics.start_query.assert_called_with("update", "incidentio_incidents")
        mock_metrics.start_query.return_value.assert_called_with(None)

    def test_update_llm_fields_records_metrics_on_error(
        self,
        dao_with_metrics: IncidentIOIncidentDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test update_llm_fields records metrics on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        db_error = Exception("Database error")
        conn.execute.side_effect = db_error

        dao_with_metrics.update_llm_fields(
            incident_id="ID-1",
            root_cause_summary="Root cause",
            root_cause_summary_hash="hash123",
            description_summary="Description",
            description_hash="desc_hash",
        )

        mock_metrics.start_query.return_value.assert_called_with(db_error)

    def test_upsert_batch_records_metrics(
        self,
        dao_with_metrics: IncidentIOIncidentDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test upsert_incidents_batch records metrics."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result_mock = MagicMock()
        result_mock.rowcount = 2
        conn.execute.return_value = result_mock

        incidents = [
            IncidentIOIncident(
                incident_id=f"ID-{i}",
                reference_id=f"INC-{i}",
                severity="Sev-1",
                slack_channel_id="C123",
                status="open",
                visibility="public",
                created_at=datetime(2024, 1, 15),
                reported_at=datetime(2024, 1, 15),
                updated_at=datetime(2024, 1, 15),
            )
            for i in range(2)
        ]

        result = dao_with_metrics.upsert_incidents_batch(incidents)

        assert result == 2
        mock_metrics.start_query.assert_called()

    def test_upsert_batch_records_metrics_on_error(
        self,
        dao_with_metrics: IncidentIOIncidentDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test upsert_incidents_batch records metrics on error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        incidents = [
            IncidentIOIncident(
                incident_id="ID-1",
                reference_id="INC-1",
                severity="Sev-1",
                slack_channel_id="C123",
                status="open",
                visibility="public",
                created_at=datetime(2024, 1, 15),
                reported_at=datetime(2024, 1, 15),
                updated_at=datetime(2024, 1, 15),
            )
        ]

        result = dao_with_metrics.upsert_incidents_batch(incidents)

        assert result is None

    def test_upsert_batch_handles_metrics_exception(
        self,
        dao_with_metrics: IncidentIOIncidentDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test upsert_incidents_batch handles metrics exception."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")
        mock_metrics.start_query.return_value.side_effect = Exception("Metrics error")

        incidents = [
            IncidentIOIncident(
                incident_id="ID-1",
                reference_id="INC-1",
                severity="Sev-1",
                slack_channel_id="C123",
                status="open",
                visibility="public",
                created_at=datetime(2024, 1, 15),
                reported_at=datetime(2024, 1, 15),
                updated_at=datetime(2024, 1, 15),
            )
        ]

        # Should not raise
        dao_with_metrics.upsert_incidents_batch(incidents)


class TestUpdateLLMFields:
    """Tests for IncidentIOIncidentDAO.update_llm_fields."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> IncidentIOIncidentDAO:
        return IncidentIOIncidentDAO(mock_engine)

    def test_success_returns_true(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Successful update returns True."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.rowcount = 1

        result = dao.update_llm_fields(
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            root_cause_summary="VPN certificate expired",
            root_cause_summary_hash="abc123",
            description_summary="Brief description",
            description_hash="def456",
        )
        assert result is WriteOutcome.WRITTEN

    def test_null_llm_fields_returns_true(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """All-None fields returns WRITTEN without hitting the DB."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        result = dao.update_llm_fields(
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            root_cause_summary=None,
            root_cause_summary_hash=None,
            description_summary=None,
            description_hash=None,
        )
        assert result is WriteOutcome.WRITTEN
        conn.execute.assert_not_called()

    def test_zero_rowcount_returns_false(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Returns BASE_NOT_FOUND when 0 rows updated — base record not yet present."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.rowcount = 0

        result = dao.update_llm_fields(
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            root_cause_summary="summary",
            root_cause_summary_hash="abc",
            description_summary="desc",
            description_hash="def",
        )
        assert result is WriteOutcome.BASE_NOT_FOUND

    def test_db_error_returns_none(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """DB exception returns ERROR without raising."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB connection lost")

        result = dao.update_llm_fields(
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            root_cause_summary="summary",
            root_cause_summary_hash="abc",
            description_summary="desc",
            description_hash="def",
        )
        assert result is WriteOutcome.ERROR


class TestFindIncidentById:
    """Tests for find_incident_by_id method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> IncidentIOIncidentDAO:
        return IncidentIOIncidentDAO(mock_engine)

    def _make_mock_row(self) -> MagicMock:
        mock_row = MagicMock()
        mock_row._mapping = {
            "id": 1,
            "incident_id": "01K3H5K30V3TECAF9G2HD1X5ZB",
            "reference_id": "INC-1234",
            "severity": "Sev-1",
            "slack_channel_id": "C123",
            "status": "open",
            "visibility": "public",
            "created_at": datetime(2024, 1, 15, 10, 30, 0),
            "reported_at": datetime(2024, 1, 15, 10, 30, 0),
            "updated_at": datetime(2024, 1, 15, 10, 30, 0),
            "accepted_at": None,
            "declined_at": None,
            "canceled_at": None,
            "resolved_at": None,
            "impact_started_at": None,
            "closed_at": None,
            "impacted_parties": None,
            "impacted_core_functions": None,
            "core_booking_hosting_flow_impacted": None,
            "affected_services": None,
            "resolution_statement": None,
            "root_cause_service": None,
            "root_cause_change_type": None,
            "root_cause_change_type_other": None,
            "root_cause_change_type_config": None,
            "environment": None,
            "detection_methods": None,
            "detection_link": None,
            "root_cause_summary": None,
            "root_cause_summary_hash": None,
            "description_summary": None,
            "description_hash": None,
        }
        return mock_row

    def test_find_by_id_success(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test find returns incident when found by incident_id."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchone.return_value = self._make_mock_row()
        conn.execute.return_value = result

        incident = dao.find_incident_by_id("01K3H5K30V3TECAF9G2HD1X5ZB")

        assert incident is not None
        assert incident.incident_id == "01K3H5K30V3TECAF9G2HD1X5ZB"
        assert incident.reference_id == "INC-1234"

    def test_find_by_id_not_found(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test find returns None when no incident matches incident_id."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.return_value = None

        incident = dao.find_incident_by_id("NONEXISTENT-ID")

        assert incident is None

    def test_find_by_id_database_error(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Test find returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        incident = dao.find_incident_by_id("01K3H5K30V3TECAF9G2HD1X5ZB")

        assert incident is None

    def test_find_by_id_records_metrics_on_success(
        self, mock_engine: MagicMock
    ) -> None:
        """Test find_incident_by_id records metrics on success."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = IncidentIOIncidentDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.return_value = self._make_mock_row()

        dao.find_incident_by_id("01K3H5K30V3TECAF9G2HD1X5ZB")

        mock_metrics.start_query.assert_called_with("select", "incidentio_incidents")
        record_fn.assert_called_with(None)

    def test_find_by_id_records_metrics_on_not_found(
        self, mock_engine: MagicMock
    ) -> None:
        """Test find_incident_by_id records metrics when not found."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = IncidentIOIncidentDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.return_value = None

        dao.find_incident_by_id("NONEXISTENT-ID")

        record_fn.assert_called_with(None)

    def test_find_by_id_records_metrics_on_error(self, mock_engine: MagicMock) -> None:
        """Test find_incident_by_id records metrics on database error."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = IncidentIOIncidentDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Database error")
        conn.execute.side_effect = error

        dao.find_incident_by_id("01K3H5K30V3TECAF9G2HD1X5ZB")

        record_fn.assert_called_once_with(error)

    def test_find_by_id_handles_metrics_exception(self, mock_engine: MagicMock) -> None:
        """Test find_incident_by_id handles exception from metrics recording."""
        mock_metrics = MagicMock()
        mock_metrics.start_query.return_value.side_effect = Exception("Metrics error")
        dao = IncidentIOIncidentDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        result = dao.find_incident_by_id("01K3H5K30V3TECAF9G2HD1X5ZB")

        assert result is None


class TestGetIncidentHashesByIds:
    """Tests for get_incident_hashes_by_ids — hash-only batch fetch for the Enricher.

    Uses IncidentIOHashInfo dataclass (not the full LLM data dict), batches at 500,
    and returns None on database error so callers can return HTTP 500.
    """

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> IncidentIOIncidentDAO:
        """Create DAO instance with mock engine."""
        return IncidentIOIncidentDAO(mock_engine)

    def _make_row(
        self,
        incident_id: str,
        root_cause_summary_hash: str | None = "rcsh_abc",
        description_hash: str | None = "dh_abc",
    ) -> MagicMock:
        """Build a mock DB result row with the columns selected by get_incident_hashes_by_ids."""
        row = MagicMock()
        row.incident_id = incident_id
        row.root_cause_summary_hash = root_cause_summary_hash
        row.description_hash = description_hash
        return row

    def test_empty_input_returns_empty_dict(self, dao: IncidentIOIncidentDAO) -> None:
        """Empty input returns {} without hitting the DB."""
        result = dao.get_incident_hashes_by_ids([])
        assert result == {}

    def test_returns_hash_info_keyed_by_incident_id(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Results are IncidentIOHashInfo instances keyed by incident_id."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [
            self._make_row(
                "INC-1", root_cause_summary_hash="rcsh_1", description_hash="dh_1"
            ),
        ]

        result = dao.get_incident_hashes_by_ids(["INC-1"])

        assert result is not None
        assert "INC-1" in result
        info = result["INC-1"]
        assert isinstance(info, IncidentIOHashInfo)
        assert info.incident_id == "INC-1"
        assert info.root_cause_summary_hash == "rcsh_1"
        assert info.description_hash == "dh_1"

    def test_multiple_incidents_returned_correctly(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Multiple incidents appear in the result dict."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [
            self._make_row("INC-1", description_hash="dh_1"),
            self._make_row("INC-2", description_hash="dh_2"),
        ]

        result = dao.get_incident_hashes_by_ids(["INC-1", "INC-2"])

        assert result is not None
        assert len(result) == 2
        assert result["INC-1"].description_hash == "dh_1"
        assert result["INC-2"].description_hash == "dh_2"

    def test_incident_not_in_db_absent_from_result(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Incidents not in the DB are simply absent from the result dict."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []

        result = dao.get_incident_hashes_by_ids(["INC-999"])

        assert result == {}

    def test_null_hashes_preserved(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """NULL hash columns in the DB are returned as None, not filtered out."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [
            self._make_row(
                "INC-1", root_cause_summary_hash=None, description_hash=None
            ),
        ]

        result = dao.get_incident_hashes_by_ids(["INC-1"])

        assert result is not None
        assert result["INC-1"].root_cause_summary_hash is None
        assert result["INC-1"].description_hash is None

    def test_database_error_returns_none(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """Returns None on database error so the caller can return HTTP 500."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB connection lost")

        result = dao.get_incident_hashes_by_ids(["INC-1"])

        assert result is None

    def test_metrics_recorded_on_success(self, mock_engine: MagicMock) -> None:
        """DBMetrics.start_query is called and record(None) is invoked on success."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = IncidentIOIncidentDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []

        dao.get_incident_hashes_by_ids(["INC-1"])

        mock_metrics.start_query.assert_called_with("select", "incidentio_incidents")
        record_fn.assert_called_with(None)

    def test_metrics_recorded_on_error(self, mock_engine: MagicMock) -> None:
        """record(exception) is called when a database error occurs."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = IncidentIOIncidentDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("DB error")
        conn.execute.side_effect = error

        dao.get_incident_hashes_by_ids(["INC-1"])

        record_fn.assert_called_once_with(error)

    def test_handles_metrics_exception_on_error(self, mock_engine: MagicMock) -> None:
        """Metrics recording exception is swallowed; None is still returned."""
        mock_metrics = MagicMock()
        mock_metrics.start_query.return_value.side_effect = Exception("Metrics error")
        dao = IncidentIOIncidentDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB error")

        result = dao.get_incident_hashes_by_ids(["INC-1"])

        assert result is None


class TestGetIncidentChannelSummaryHashesByReferenceIds:
    """Tests for get_incident_channel_summary_hashes_by_reference_ids.

    Hash-only batch fetch for the Enricher's incident_channel_summary source —
    keyed on reference_id (unlike get_incident_hashes_by_ids, which keys on
    incident_id), and returns a flat {reference_id: hash} dict rather than a
    dataclass since there is only one hash column for this source.
    """

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> IncidentIOIncidentDAO:
        """Create DAO instance with mock engine."""
        return IncidentIOIncidentDAO(mock_engine)

    def _make_row(
        self, reference_id: str, incident_channel_summary_hash: str | None
    ) -> MagicMock:
        row = MagicMock()
        row.reference_id = reference_id
        row.incident_channel_summary_hash = incident_channel_summary_hash
        return row

    def test_empty_input_returns_empty_dict(self, dao: IncidentIOIncidentDAO) -> None:
        """Empty input returns {} without hitting the DB."""
        result = dao.get_incident_channel_summary_hashes_by_reference_ids([])
        assert result == {}

    def test_returns_hash_keyed_by_reference_id(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [
            self._make_row("INC-1", "icsh_1"),
        ]

        result = dao.get_incident_channel_summary_hashes_by_reference_ids(["INC-1"])

        assert result == {"INC-1": "icsh_1"}

    def test_multiple_incidents_returned_correctly(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [
            self._make_row("INC-1", "icsh_1"),
            self._make_row("INC-2", "icsh_2"),
        ]

        result = dao.get_incident_channel_summary_hashes_by_reference_ids(
            ["INC-1", "INC-2"]
        )

        assert result == {"INC-1": "icsh_1", "INC-2": "icsh_2"}

    def test_incident_not_in_db_absent_from_result(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []

        result = dao.get_incident_channel_summary_hashes_by_reference_ids(["INC-999"])

        assert result == {}

    def test_null_hash_preserved(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        """NULL hash column in the DB is returned as None, not filtered out."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [
            self._make_row("INC-1", None),
        ]

        result = dao.get_incident_channel_summary_hashes_by_reference_ids(["INC-1"])

        assert result == {"INC-1": None}

    def test_database_error_returns_none(
        self, dao: IncidentIOIncidentDAO, mock_engine: MagicMock
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB connection lost")

        result = dao.get_incident_channel_summary_hashes_by_reference_ids(["INC-1"])

        assert result is None

    def test_metrics_recorded_on_success(self, mock_engine: MagicMock) -> None:
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = IncidentIOIncidentDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []

        dao.get_incident_channel_summary_hashes_by_reference_ids(["INC-1"])

        mock_metrics.start_query.assert_called_with("select", "incidentio_incidents")
        record_fn.assert_called_with(None)

    def test_metrics_recorded_on_error(self, mock_engine: MagicMock) -> None:
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = IncidentIOIncidentDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("DB error")
        conn.execute.side_effect = error

        dao.get_incident_channel_summary_hashes_by_reference_ids(["INC-1"])

        record_fn.assert_called_once_with(error)
