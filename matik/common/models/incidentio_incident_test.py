"""Unit tests for IncidentIOIncident model."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from common.models.incidentio_incident import IncidentIOIncident


class TestIncidentIOIncident:
    """Test suite for IncidentIOIncident SQLModel model."""

    def test_valid_instantiation_minimal(self) -> None:
        """Test creating IncidentIOIncident with minimal required fields."""
        incident = IncidentIOIncident(
            incident_id="INC123",
            reference_id="INC-123",
            severity="Sev-1",
            slack_channel_id="C123456",
            status="open",
            visibility="public",
            created_at=datetime(2024, 1, 9, 10, 0),
            reported_at=datetime(2024, 1, 9, 10, 0),
            updated_at=datetime(2024, 1, 9, 11, 0),
        )
        assert incident.incident_id == "INC123"
        assert incident.reference_id == "INC-123"
        assert incident.severity == "Sev-1"
        assert incident.slack_channel_id == "C123456"
        assert incident.status == "open"
        assert incident.visibility == "public"
        assert incident.id is None  # Default for auto-increment PK

        # Optional timestamps default to None
        assert incident.accepted_at is None
        assert incident.declined_at is None
        assert incident.canceled_at is None
        assert incident.resolved_at is None
        assert incident.impact_started_at is None
        assert incident.closed_at is None

        # Optional custom fields default to None
        assert incident.impacted_parties is None
        assert incident.impacted_core_functions is None
        assert incident.core_booking_hosting_flow_impacted is None
        assert incident.affected_services is None
        assert incident.root_cause_service is None
        assert incident.root_cause_change_type is None
        assert incident.root_cause_change_type_other is None
        assert incident.root_cause_change_type_config is None
        assert incident.environment is None
        assert incident.detection_methods is None
        assert incident.detection_link is None

        # LLM-generated fields default to None
        assert incident.root_cause_summary is None
        assert incident.description_summary is None
        assert incident.root_cause_summary_hash is None

        # Incident channel summary fields default to None
        assert incident.incident_channel_summary is None
        assert incident.incident_channel_summary_hash is None

    def test_with_all_timestamp_lifecycle_fields(self) -> None:
        """Test creating IncidentIOIncident with all lifecycle timestamps."""
        incident = IncidentIOIncident(
            incident_id="INC124",
            reference_id="INC-124",
            severity="Sev-2",
            slack_channel_id="C789",
            status="closed",
            visibility="private",
            created_at=datetime(2024, 1, 9, 10, 0),
            reported_at=datetime(2024, 1, 9, 10, 0),
            updated_at=datetime(2024, 1, 9, 18, 0),
            accepted_at=datetime(2024, 1, 9, 10, 30),
            resolved_at=datetime(2024, 1, 9, 15, 0),
            closed_at=datetime(2024, 1, 9, 16, 0),
            declined_at=None,
        )
        assert incident.accepted_at == datetime(2024, 1, 9, 10, 30)
        assert incident.resolved_at == datetime(2024, 1, 9, 15, 0)
        assert incident.closed_at == datetime(2024, 1, 9, 16, 0)
        assert incident.declined_at is None

    def test_with_custom_fields(self) -> None:
        """Test creating IncidentIOIncident with custom fields."""
        incident = IncidentIOIncident(
            incident_id="INC125",
            reference_id="INC-125",
            severity="Sev-1",
            slack_channel_id="C111",
            status="investigating",
            visibility="public",
            created_at=datetime(2024, 1, 9, 10, 0),
            reported_at=datetime(2024, 1, 9, 10, 0),
            updated_at=datetime(2024, 1, 9, 11, 0),
            impacted_parties=["Guest", "Host"],
            core_booking_hosting_flow_impacted=True,
            affected_services=["a4w-common", "payment-service"],
            root_cause_service="payment-service",
            root_cause_change_type="Code Deploy",
            environment="Production",
            detection_methods=["alert", "manual"],
            detection_link="https://pagerduty.com/incidents/123",
        )
        assert incident.impacted_parties == ["Guest", "Host"]
        assert incident.core_booking_hosting_flow_impacted is True
        assert incident.affected_services == ["a4w-common", "payment-service"]
        assert incident.root_cause_service == "payment-service"
        assert incident.root_cause_change_type == "Code Deploy"
        assert incident.environment == "Production"
        assert incident.detection_methods == ["alert", "manual"]
        assert incident.detection_link == "https://pagerduty.com/incidents/123"

    def test_with_llm_generated_fields(self) -> None:
        """Test creating IncidentIOIncident with LLM-generated summaries."""
        incident = IncidentIOIncident(
            incident_id="INC126",
            reference_id="INC-126",
            severity="Sev-2",
            slack_channel_id="C222",
            status="closed",
            visibility="public",
            created_at=datetime(2024, 1, 9, 10, 0),
            reported_at=datetime(2024, 1, 9, 10, 0),
            updated_at=datetime(2024, 1, 9, 18, 0),
            root_cause_summary="Database connection pool exhausted",
            description_summary="Increased connection pool size",
            root_cause_summary_hash="abc123def456",
        )
        assert incident.root_cause_summary == "Database connection pool exhausted"
        assert incident.description_summary == "Increased connection pool size"
        assert incident.root_cause_summary_hash == "abc123def456"

    def test_with_incident_channel_summary_fields(self) -> None:
        """Test creating IncidentIOIncident with incident channel summary fields."""
        incident = IncidentIOIncident(
            incident_id="INC130",
            reference_id="INC-130",
            severity="Sev-2",
            slack_channel_id="C230",
            status="investigating",
            visibility="public",
            created_at=datetime(2024, 1, 9, 10, 0),
            reported_at=datetime(2024, 1, 9, 10, 0),
            updated_at=datetime(2024, 1, 9, 18, 0),
            incident_channel_summary="LLM-condensed channel summary",
            incident_channel_summary_hash="def789ghi012",
        )
        assert incident.incident_channel_summary == "LLM-condensed channel summary"
        assert incident.incident_channel_summary_hash == "def789ghi012"

    def test_required_fields_missing(self) -> None:
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            IncidentIOIncident.model_validate({"incident_id": "INC123"})
        error_str = str(exc_info.value)
        assert "reference_id" in error_str
        assert "severity" in error_str
        assert "slack_channel_id" in error_str
        assert "status" in error_str
        assert "visibility" in error_str
        assert "created_at" in error_str
        assert "reported_at" in error_str
        assert "updated_at" in error_str

    def test_timestamp_parsing_from_string(self) -> None:
        """Test timestamp fields parse ISO 8601 strings correctly."""
        incident = IncidentIOIncident.model_validate(
            {
                "incident_id": "INC127",
                "reference_id": "INC-127",
                "severity": "Sev-3",
                "slack_channel_id": "C333",
                "status": "open",
                "visibility": "public",
                "created_at": "2024-01-09T10:30:00Z",
                "reported_at": "2024-01-09T10:30:00Z",
                "updated_at": "2024-01-09T11:00:00Z",
                "accepted_at": "2024-01-09T10:45:00Z",
                "resolved_at": "2024-01-09T11:00:00Z",
            }
        )
        assert isinstance(incident.created_at, datetime)
        assert incident.created_at.tzinfo is None  # Naive UTC
        assert incident.created_at == datetime(2024, 1, 9, 10, 30)
        assert incident.reported_at == datetime(2024, 1, 9, 10, 30)
        assert incident.updated_at == datetime(2024, 1, 9, 11, 0)
        assert incident.accepted_at == datetime(2024, 1, 9, 10, 45)
        assert incident.resolved_at == datetime(2024, 1, 9, 11, 0)

    def test_timestamp_parsing_from_timezone_aware_datetime(self) -> None:
        """Test timestamps convert timezone-aware datetime to naive UTC."""
        incident = IncidentIOIncident.model_validate(
            {
                "incident_id": "INC128",
                "reference_id": "INC-128",
                "severity": "Sev-1",
                "slack_channel_id": "C444",
                "status": "open",
                "visibility": "public",
                "created_at": datetime(2024, 1, 9, 10, 30, 0, tzinfo=UTC),
                "reported_at": datetime(2024, 1, 9, 10, 30, 0, tzinfo=UTC),
                "updated_at": datetime(2024, 1, 9, 10, 30, 0, tzinfo=UTC),
            }
        )
        assert incident.created_at.tzinfo is None
        assert incident.created_at == datetime(2024, 1, 9, 10, 30, 0)

    def test_severity_values(self) -> None:
        """Test different severity values."""
        for severity in ["Sev-1", "Sev-2", "Sev-3", "Sev-4"]:
            incident = IncidentIOIncident(
                incident_id="INC-SEV",
                reference_id=f"INC-SEV-{severity}",
                severity=severity,
                slack_channel_id="C555",
                status="open",
                visibility="public",
                created_at=datetime(2024, 1, 9, 10, 0),
                reported_at=datetime(2024, 1, 9, 10, 0),
                updated_at=datetime(2024, 1, 9, 11, 0),
            )
            assert incident.severity == severity

    def test_status_values(self) -> None:
        """Test different status values."""
        for status in ["open", "investigating", "closed", "declined"]:
            incident = IncidentIOIncident(
                incident_id="INC-STATUS",
                reference_id=f"INC-STATUS-{status}",
                severity="Sev-2",
                slack_channel_id="C666",
                status=status,
                visibility="public",
                created_at=datetime(2024, 1, 9, 10, 0),
                reported_at=datetime(2024, 1, 9, 10, 0),
                updated_at=datetime(2024, 1, 9, 11, 0),
            )
            assert incident.status == status

    def test_visibility_values(self) -> None:
        """Test different visibility values."""
        for visibility in ["public", "private"]:
            incident = IncidentIOIncident(
                incident_id="INC-VIS",
                reference_id=f"INC-VIS-{visibility}",
                severity="Sev-2",
                slack_channel_id="C777",
                status="open",
                visibility=visibility,
                created_at=datetime(2024, 1, 9, 10, 0),
                reported_at=datetime(2024, 1, 9, 10, 0),
                updated_at=datetime(2024, 1, 9, 11, 0),
            )
            assert incident.visibility == visibility

    def test_boolean_field(self) -> None:
        """Test core_booking_hosting_flow_impacted boolean field."""
        incident1 = IncidentIOIncident(
            incident_id="INC-BOOL-1",
            reference_id="INC-BOOL-1",
            severity="Sev-1",
            slack_channel_id="C888",
            status="open",
            visibility="public",
            created_at=datetime(2024, 1, 9, 10, 0),
            reported_at=datetime(2024, 1, 9, 10, 0),
            updated_at=datetime(2024, 1, 9, 11, 0),
            core_booking_hosting_flow_impacted=True,
        )
        assert incident1.core_booking_hosting_flow_impacted is True

        incident2 = IncidentIOIncident(
            incident_id="INC-BOOL-2",
            reference_id="INC-BOOL-2",
            severity="Sev-3",
            slack_channel_id="C999",
            status="open",
            visibility="public",
            created_at=datetime(2024, 1, 9, 10, 0),
            reported_at=datetime(2024, 1, 9, 10, 0),
            updated_at=datetime(2024, 1, 9, 11, 0),
            core_booking_hosting_flow_impacted=False,
        )
        assert incident2.core_booking_hosting_flow_impacted is False

    def test_orm_mode(self) -> None:
        """Test that model can be created from dict-like objects."""
        data = {
            "id": 1,
            "incident_id": "INC999",
            "reference_id": "INC-999",
            "severity": "Sev-1",
            "slack_channel_id": "C000",
            "status": "closed",
            "visibility": "public",
            "created_at": datetime(2024, 1, 9, 10, 0),
            "reported_at": datetime(2024, 1, 9, 10, 0),
            "updated_at": datetime(2024, 1, 9, 18, 0),
            "accepted_at": datetime(2024, 1, 9, 10, 30),
            "resolved_at": datetime(2024, 1, 9, 15, 0),
            "closed_at": datetime(2024, 1, 9, 16, 0),
            "declined_at": None,
            "impacted_parties": ["Guest", "Host", "Airfam"],
            "core_booking_hosting_flow_impacted": True,
            "affected_services": ["service-a", "service-b"],
            "root_cause_service": "service-a",
            "root_cause_change_type": "Code Deploy",
            "environment": "Production",
            "detection_methods": ["alert"],
            "detection_link": "https://example.com",
            "root_cause_summary": "Test root cause",
            "description_summary": "Test resolution",
            "root_cause_summary_hash": "abc123",
        }
        incident = IncidentIOIncident.model_validate(data)
        assert incident.id == 1
        assert incident.incident_id == "INC999"
        assert incident.severity == "Sev-1"
        assert incident.impacted_parties == ["Guest", "Host", "Airfam"]
        assert incident.core_booking_hosting_flow_impacted is True
        assert incident.description_summary == "Test resolution"
