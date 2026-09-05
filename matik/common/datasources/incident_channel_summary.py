"""Incident channel summary data source spec (OpsBot on-demand feed).

Registered on import (see ``common/datasources/__init__.py``). Unlike the
other specs, this source owns no distinct table and has no base-write route
at all — it only ever patches the LLM/hash columns on the shared
``incidentio_incidents`` table via the enrichment path. The raw Slack-channel
text OpsBot posts is never persisted (it lives only in the Enricher's SQS
message), so there is no non-sensitive record to write ahead of enrichment,
unlike the incidentio/jira/ghe_pr sources. This keeps the feed decoupled from
the regular Incident.io ingestion path (see the incidentio spec's
``exclude_columns``) while reusing its DAO and table.

See https://jira.airbnb.biz/browse/ITE-797334.
"""

from __future__ import annotations

from common.datasources.registry import DataSourceSpec, register_source
from common.models.incidentio_incident import IncidentIOIncident
from common.models.scribe_messages import IncidentChannelSummaryEnrichmentMessage


def _build_dao(engine, metrics=None):  # type: ignore[no-untyped-def]
    """Construct the shared Incident.io DAO, bound to this spec's source_type.

    Imported lazily to avoid an import cycle (the DAO imports this spec via
    ``get_source``). Reuses ``IncidentIOIncidentDAO`` rather than a new DAO
    class — both sources write to the same table/model — but binds it to this
    spec (not "incidentio") so ``update_llm_fields_from_message`` reads the
    right ``enrichment_key``/``llm_columns``.
    """
    from common.daos.incidentio_incident_dao import IncidentIOIncidentDAO

    return IncidentIOIncidentDAO(
        engine, metrics, source_type="incident_channel_summary"
    )


register_source(
    DataSourceSpec(
        source_type="incident_channel_summary",
        record_model=IncidentIOIncident,
        # Unused for writes (this source never upserts a full record) but
        # required by the spec; matches the table's actual unique index.
        conflict_keys=["incident_id", "reference_id"],
        dao_factory=_build_dao,
        enrichment_message_model=IncidentChannelSummaryEnrichmentMessage,
        enrichment_key="reference_id",
        enrichment_hook_target="record",
        record_finder="find_incident",
        # No base_entered_at_column: this source has no base write.
        enrichment_entered_at_column="incident_channel_summary_entered_at",
    )
)
