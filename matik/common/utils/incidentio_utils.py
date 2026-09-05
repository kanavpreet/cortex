"""Shared Incident.io payload mapping helpers.

The Incident.io REST API response and the public webhook payload (delivered via
Yoyo/Svix) share the same incident object schema. The historian's REST client
and the chronicler's webhook transformer both need to flatten that schema into
our `IncidentIOIncident` row model — so the mapping rules live here and are
imported by both.

`build_incident_from_payload` returns the populated record plus the raw
`name`, `summary`, and `resolution_statement` fields. Those three are not stored
on the DB row directly; they are forwarded to the Enricher to produce
`description_summary` and `root_cause_summary`.
"""

from datetime import datetime
from typing import Any

from common.models.incidentio_incident import IncidentIOIncident
from common.utils.datetime_utils import parse_timestamp_to_utc, utc_now_naive

# Mapping from Incident.io's named timestamps to our row columns.
INCIDENTIO_TIMESTAMP_NAME_MAP: dict[str, str] = {
    "Reported at": "reported_at",
    "Accepted at": "accepted_at",
    "Declined at": "declined_at",
    "Canceled at": "canceled_at",
    "Resolved at": "resolved_at",
    "Impact started at": "impact_started_at",
    "Closed at": "closed_at",
}

# Default-None scaffold for every column we extract from custom fields.
_CUSTOM_FIELD_DEFAULTS: dict[str, Any] = {
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
}


def _value_list(values: list[dict[str, Any]]) -> list[str] | None:
    """Collapse a custom-field `values` array into a list of strings.

    Handles the two list-typed shapes Incident.io returns:
    - `value_catalog_entry.name` (catalog references)
    - `value_option.value` (single-select / multi-select options)
    """
    result = [
        val
        for v in values
        if (
            val := (
                v.get("value_catalog_entry", {}).get("name")
                or v.get("value_option", {}).get("value")
            )
        )
    ]
    return result if result else None


def extract_timestamps(
    timestamps: list[dict[str, Any]] | None,
) -> dict[str, datetime | None]:
    """Pull named lifecycle timestamps out of the `incident_timestamp_values` array.

    Returns a dict keyed by `IncidentIOIncident` column names. Missing
    timestamps and zero-dates (year <= 1) come back as None.
    """
    result: dict[str, datetime | None] = dict.fromkeys(
        INCIDENTIO_TIMESTAMP_NAME_MAP.values()
    )
    if not timestamps:
        return result

    for ts in timestamps:
        name = ts.get("incident_timestamp", {}).get("name")
        if name not in INCIDENTIO_TIMESTAMP_NAME_MAP:
            continue
        raw = ts.get("value", {}).get("value")
        if not raw:
            continue
        parsed = parse_timestamp_to_utc(raw)
        if parsed and parsed.year > 1:
            result[INCIDENTIO_TIMESTAMP_NAME_MAP[name]] = parsed

    return result


def extract_custom_fields(
    entries: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Flatten Incident.io's `custom_field_entries` into our column shape.

    The set of recognized field names mirrors what the Incident.io tenant is
    configured with today. Unknown fields are ignored — adding a new one means
    updating this function in one place.
    """
    result: dict[str, Any] = dict(_CUSTOM_FIELD_DEFAULTS)
    if not entries:
        return result

    for entry in entries:
        field_info = entry.get("custom_field") or {}
        field_name = (field_info.get("name") or "").strip()
        values = entry.get("values") or []
        if not values:
            continue
        first = values[0]

        if field_name == "Who is impacted?":
            result["impacted_parties"] = _value_list(values)
        elif field_name == "Core Functions/Services Impacted":
            result["impacted_core_functions"] = _value_list(values)
        elif field_name == "Core Booking/Hosting Flow Impacted":
            opt = (first.get("value_option") or {}).get("value")
            if opt == "Yes":
                result["core_booking_hosting_flow_impacted"] = True
            elif opt == "No":
                result["core_booking_hosting_flow_impacted"] = False
        elif field_name == "Affected Services":
            result["affected_services"] = _value_list(values)
        elif field_name == "Resolution Statement":
            result["resolution_statement"] = first.get("value_text")
        elif field_name == "Root Cause Service":
            result["root_cause_service"] = (first.get("value_catalog_entry") or {}).get(
                "name"
            )
        elif field_name == "Root Cause Change Type":
            result["root_cause_change_type"] = (first.get("value_option") or {}).get(
                "value"
            )
        elif field_name == "Root Cause Change Type Other":
            result["root_cause_change_type_other"] = first.get("value_text")
        elif field_name == "Root Cause Change Type Config":
            result["root_cause_change_type_config"] = (
                first.get("value_option") or {}
            ).get("value")
        elif field_name == "Environment":
            result["environment"] = (first.get("value_option") or {}).get("value")
        elif field_name == "Incident detection method":
            result["detection_methods"] = _value_list(values)
        elif field_name == "Detection":
            result["detection_link"] = first.get("value_link")

    return result


def build_incident_from_payload(
    raw: dict[str, Any],
) -> tuple[IncidentIOIncident, str | None, str | None, str | None]:
    """Build an `IncidentIOIncident` plus the raw fields the Enricher needs.

    The shape of `raw` matches both the Incident.io REST API incident object
    and the public webhook `payload[event_type]` body — so historian and
    chronicler can share this single builder.

    Returns:
        (incident, name, summary, resolution_statement)
    """
    timestamps = extract_timestamps(raw.get("incident_timestamp_values"))
    custom_fields = extract_custom_fields(raw.get("custom_field_entries"))

    created_at = parse_timestamp_to_utc(raw.get("created_at")) or utc_now_naive()
    reported_at = timestamps["reported_at"] or created_at
    updated_at = parse_timestamp_to_utc(raw.get("updated_at")) or created_at

    incident_status = raw.get("incident_status") or {}

    incident = IncidentIOIncident(
        incident_id=raw.get("id", ""),
        reference_id=raw.get("reference", ""),
        severity=(raw.get("severity") or {}).get("name", ""),
        slack_channel_id=raw.get("slack_channel_id", ""),
        status=incident_status.get("name", raw.get("status", "")),
        visibility=raw.get("visibility", ""),
        status_category=incident_status.get("category"),
        created_at=created_at,
        reported_at=reported_at,
        updated_at=updated_at,
        accepted_at=timestamps["accepted_at"],
        declined_at=timestamps["declined_at"],
        canceled_at=timestamps["canceled_at"],
        resolved_at=timestamps["resolved_at"],
        impact_started_at=timestamps["impact_started_at"],
        closed_at=timestamps["closed_at"],
        impacted_parties=custom_fields["impacted_parties"],
        impacted_core_functions=custom_fields["impacted_core_functions"],
        core_booking_hosting_flow_impacted=custom_fields[
            "core_booking_hosting_flow_impacted"
        ],
        affected_services=custom_fields["affected_services"],
        root_cause_service=custom_fields["root_cause_service"],
        root_cause_change_type=custom_fields["root_cause_change_type"],
        root_cause_change_type_other=custom_fields["root_cause_change_type_other"],
        root_cause_change_type_config=custom_fields["root_cause_change_type_config"],
        environment=custom_fields["environment"],
        detection_methods=custom_fields["detection_methods"],
        detection_link=custom_fields["detection_link"],
    )

    return (
        incident,
        raw.get("name"),
        raw.get("summary"),
        custom_fields["resolution_statement"],
    )
