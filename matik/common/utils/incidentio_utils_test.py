"""Tests for the shared Incident.io payload mapping helpers."""

from datetime import datetime
from typing import Any

from common.utils.incidentio_utils import (
    build_incident_from_payload,
    extract_custom_fields,
    extract_timestamps,
)


def _timestamp_entry(name: str, value: str | None) -> dict[str, Any]:
    return {
        "incident_timestamp": {"name": name},
        "value": {"value": value},
    }


def _custom_field_entry(
    name: str, values: list[dict[str, Any]] | None
) -> dict[str, Any]:
    return {"custom_field": {"name": name}, "values": values}


class TestExtractTimestamps:
    def test_none_input_returns_all_none(self) -> None:
        result = extract_timestamps(None)
        assert all(v is None for v in result.values())
        assert "reported_at" in result and "closed_at" in result

    def test_each_named_timestamp_parses(self) -> None:
        entries = [
            _timestamp_entry("Reported at", "2026-05-01T10:00:00.000Z"),
            _timestamp_entry("Accepted at", "2026-05-01T10:05:00.000Z"),
            _timestamp_entry("Resolved at", "2026-05-01T11:00:00.000Z"),
            _timestamp_entry("Closed at", "2026-05-01T12:00:00.000Z"),
        ]
        result = extract_timestamps(entries)
        assert result["reported_at"] == datetime(2026, 5, 1, 10, 0, 0)
        assert result["accepted_at"] == datetime(2026, 5, 1, 10, 5, 0)
        assert result["resolved_at"] == datetime(2026, 5, 1, 11, 0, 0)
        assert result["closed_at"] == datetime(2026, 5, 1, 12, 0, 0)
        assert result["declined_at"] is None

    def test_zero_date_is_skipped(self) -> None:
        # Year 1 timestamps come back from the API for unset lifecycle fields.
        entries = [_timestamp_entry("Closed at", "0001-01-01T00:00:00Z")]
        result = extract_timestamps(entries)
        assert result["closed_at"] is None

    def test_unknown_timestamp_name_is_ignored(self) -> None:
        entries = [_timestamp_entry("Made up at", "2026-05-01T10:00:00Z")]
        assert all(v is None for v in extract_timestamps(entries).values())

    def test_missing_value_is_skipped(self) -> None:
        entries = [_timestamp_entry("Reported at", None)]
        assert extract_timestamps(entries)["reported_at"] is None


class TestExtractCustomFields:
    def test_none_input_returns_all_defaults(self) -> None:
        result = extract_custom_fields(None)
        assert result["impacted_parties"] is None
        assert result["resolution_statement"] is None

    def test_list_typed_fields(self) -> None:
        entries = [
            _custom_field_entry(
                "Who is impacted?",
                [
                    {"value_option": {"value": "Guest"}},
                    {"value_option": {"value": "Host"}},
                ],
            ),
            _custom_field_entry(
                "Affected Services",
                [{"value_catalog_entry": {"name": "matik-historian"}}],
            ),
        ]
        result = extract_custom_fields(entries)
        assert result["impacted_parties"] == ["Guest", "Host"]
        assert result["affected_services"] == ["matik-historian"]

    def test_core_functions_services_impacted(self) -> None:
        entries = [
            _custom_field_entry(
                "Core Functions/Services Impacted",
                [{"value_option": {"value": "Atrium"}}],
            )
        ]
        assert extract_custom_fields(entries)["impacted_core_functions"] == ["Atrium"]

    def test_core_booking_hosting_flow_bool(self) -> None:
        yes = extract_custom_fields(
            [
                _custom_field_entry(
                    "Core Booking/Hosting Flow Impacted",
                    [{"value_option": {"value": "Yes"}}],
                )
            ]
        )
        no = extract_custom_fields(
            [
                _custom_field_entry(
                    "Core Booking/Hosting Flow Impacted",
                    [{"value_option": {"value": "No"}}],
                )
            ]
        )
        other = extract_custom_fields(
            [
                _custom_field_entry(
                    "Core Booking/Hosting Flow Impacted",
                    [{"value_option": {"value": "Maybe"}}],
                )
            ]
        )
        assert yes["core_booking_hosting_flow_impacted"] is True
        assert no["core_booking_hosting_flow_impacted"] is False
        assert other["core_booking_hosting_flow_impacted"] is None

    def test_text_fields(self) -> None:
        entries = [
            _custom_field_entry(
                "Resolution Statement", [{"value_text": "rolled back the bad deploy"}]
            ),
            _custom_field_entry(
                "Root Cause Change Type Other", [{"value_text": "manual config edit"}]
            ),
        ]
        result = extract_custom_fields(entries)
        assert result["resolution_statement"] == "rolled back the bad deploy"
        assert result["root_cause_change_type_other"] == "manual config edit"

    def test_option_fields(self) -> None:
        entries = [
            _custom_field_entry(
                "Root Cause Change Type", [{"value_option": {"value": "Code Deploy"}}]
            ),
            _custom_field_entry(
                "Root Cause Change Type Config",
                [{"value_option": {"value": "Sitar"}}],
            ),
            _custom_field_entry(
                "Environment", [{"value_option": {"value": "Production"}}]
            ),
        ]
        result = extract_custom_fields(entries)
        assert result["root_cause_change_type"] == "Code Deploy"
        assert result["root_cause_change_type_config"] == "Sitar"
        assert result["environment"] == "Production"

    def test_catalog_field(self) -> None:
        entries = [
            _custom_field_entry(
                "Root Cause Service",
                [{"value_catalog_entry": {"name": "a4w-common"}}],
            )
        ]
        assert extract_custom_fields(entries)["root_cause_service"] == "a4w-common"

    def test_detection_methods(self) -> None:
        entries = [
            _custom_field_entry(
                "Incident detection method",
                [
                    {"value_option": {"value": "alert"}},
                    {"value_option": {"value": "manual"}},
                ],
            )
        ]
        assert extract_custom_fields(entries)["detection_methods"] == [
            "alert",
            "manual",
        ]

    def test_detection_link(self) -> None:
        entries = [
            _custom_field_entry(
                "Detection", [{"value_link": "https://pagerduty/incident/123"}]
            )
        ]
        assert (
            extract_custom_fields(entries)["detection_link"]
            == "https://pagerduty/incident/123"
        )

    def test_unknown_field_is_ignored(self) -> None:
        entries = [_custom_field_entry("Made Up Field", [{"value_text": "x"}])]
        result = extract_custom_fields(entries)
        assert all(v is None for v in result.values())

    def test_empty_values_skipped(self) -> None:
        entries = [_custom_field_entry("Resolution Statement", [])]
        assert extract_custom_fields(entries)["resolution_statement"] is None


def _incident_payload(
    incident_id: str = "01FDAG4SAP5TYPT98WGR2N7W91",
    reference: str = "INC-123",
    severity_name: str = "Minor",
    status_name: str = "Closed",
    status_category: str = "closed",
    name: str = "Our database is sad",
    summary: str = "Our database is really really sad, and we don't know why yet.",
    created_at: str | None = "2021-08-17T13:28:57.801578Z",
    updated_at: str | None = "2021-08-17T14:00:00.000000Z",
    custom_fields: list[dict[str, Any]] | None = None,
    timestamp_values: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": incident_id,
        "reference": reference,
        "severity": {"name": severity_name},
        "incident_status": {"name": status_name, "category": status_category},
        "slack_channel_id": "C02AW36C1M5",
        "visibility": "public",
        "name": name,
        "summary": summary,
        "created_at": created_at,
        "updated_at": updated_at,
        "incident_timestamp_values": timestamp_values or [],
        "custom_field_entries": custom_fields or [],
    }


class TestBuildIncidentFromPayload:
    def test_happy_path_mapping(self) -> None:
        payload = _incident_payload(
            timestamp_values=[
                _timestamp_entry("Reported at", "2021-08-17T13:28:57Z"),
                _timestamp_entry("Closed at", "2021-08-17T14:00:00Z"),
            ],
            custom_fields=[
                _custom_field_entry(
                    "Affected Services",
                    [{"value_catalog_entry": {"name": "matik-historian"}}],
                ),
                _custom_field_entry(
                    "Resolution Statement", [{"value_text": "rolled back"}]
                ),
            ],
        )
        incident, name, summary, resolution = build_incident_from_payload(payload)
        assert incident.incident_id == "01FDAG4SAP5TYPT98WGR2N7W91"
        assert incident.reference_id == "INC-123"
        assert incident.severity == "Minor"
        assert incident.status == "Closed"
        assert incident.status_category == "closed"
        assert incident.slack_channel_id == "C02AW36C1M5"
        assert incident.visibility == "public"
        assert incident.affected_services == ["matik-historian"]
        assert incident.reported_at == datetime(2021, 8, 17, 13, 28, 57)
        assert incident.closed_at == datetime(2021, 8, 17, 14, 0, 0)
        assert name == "Our database is sad"
        assert summary is not None and summary.startswith("Our database is really")
        assert resolution == "rolled back"

    def test_missing_created_at_falls_back_to_now(self) -> None:
        payload = _incident_payload(created_at=None, updated_at=None)
        incident, _, _, _ = build_incident_from_payload(payload)
        # utc_now_naive() fallback — just confirm we got a real datetime.
        assert isinstance(incident.created_at, datetime)
        assert incident.updated_at == incident.created_at
        assert incident.reported_at == incident.created_at

    def test_missing_reported_at_falls_back_to_created_at(self) -> None:
        payload = _incident_payload(
            created_at="2026-01-02T03:04:05Z", timestamp_values=[]
        )
        incident, _, _, _ = build_incident_from_payload(payload)
        assert incident.reported_at == incident.created_at

    def test_falls_back_to_status_string_when_incident_status_missing(self) -> None:
        payload = _incident_payload()
        payload.pop("incident_status")
        payload["status"] = "closed"
        incident, _, _, _ = build_incident_from_payload(payload)
        assert incident.status == "closed"
        assert incident.status_category is None
