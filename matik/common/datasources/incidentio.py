"""Incident.io data source spec.

Registered on import (see ``common/datasources/__init__.py``). This is the
single declaration of Incident.io's write columns + Scribe routing; the
model-derived ``BaseUpsertDAO`` and the generic Scribe handler read it instead
of hand-maintained per-source column lists and handler classes.
"""

from __future__ import annotations

from common.datasources.registry import DataSourceSpec, register_source
from common.models.incidentio_incident import IncidentIOIncident
from common.models.scribe_messages import IncidentIOEnrichmentMessage


def _build_dao(engine, metrics=None):  # type: ignore[no-untyped-def]
    """Construct the Incident.io DAO. Imported lazily to avoid an import cycle
    (the DAO imports this spec via ``get_source``)."""
    from common.daos.incidentio_incident_dao import IncidentIOIncidentDAO

    return IncidentIOIncidentDAO(engine, metrics)


register_source(
    DataSourceSpec(
        source_type="incidentio",
        record_model=IncidentIOIncident,
        conflict_keys=["incident_id", "reference_id"],
        # Exclude the incident-channel-summary LLM/hash columns: they are
        # owned by the "incident_channel_summary" source's enrichment path.
        # A regular Incident.io re-upsert must never clobber or NULL them.
        # Also exclude the OTHER write-groups' guard timestamps
        # (incidentio_enrichment_entered_at, incident_channel_summary_entered_at):
        # neither is a field on IncidentIOEnrichmentMessage, so llm_columns'
        # auto-derivation can't exclude them the way it excludes
        # root_cause_summary/description_summary — they need the same manual
        # exclusion as the two columns above so a base upsert never resets a
        # sibling write-group's staleness guard.
        exclude_columns=[
            "incident_channel_summary",
            "incident_channel_summary_hash",
            "incidentio_enrichment_entered_at",
            "incident_channel_summary_entered_at",
        ],
        dao_factory=_build_dao,
        enrichment_message_model=IncidentIOEnrichmentMessage,
        enrichment_key="incident_id",
        enrichment_hook_target="record",
        record_finder="find_incident_by_id",
        base_entered_at_column="incidentio_base_entered_at",
        enrichment_entered_at_column="incidentio_enrichment_entered_at",
    )
)
