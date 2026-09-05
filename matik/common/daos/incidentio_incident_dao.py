"""Data Access Object for Incident.io incidents table."""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Engine, insert, select, update

from common.daos.base_dao import BATCH_SIZE, BaseUpsertDAO, WriteOutcome
from common.models.incidentio_incident import IncidentIOIncident
from common.utils import log_utils
from common.utils.datetime_utils import parse_timestamp_to_utc
from common.utils.retry_utils import execute_with_retry

if TYPE_CHECKING:
    from common.metrics import DBMetrics

# Re-exported for backward compatibility (previously defined in this module).
__all__ = [
    "BATCH_SIZE",
    "IncidentIOHashInfo",
    "IncidentIOIncidentDAO",
]


logger = log_utils.get_logger(__name__)

# Get table from SQLModel class
# Note: __table__ is dynamically created by SQLModel when table=True
incidentio_incidents_table = IncidentIOIncident.__table__  # type: ignore[attr-defined]


@dataclass
class IncidentIOHashInfo:
    """Hash columns for an Incident.io incident, used by the Enricher for change detection.

    Two independent hashes are tracked per incident because two separate LLM enrichments
    can change independently:
      - root_cause_summary_hash: SHA256 of (summary) — gates root_cause_summary generation
      - description_hash:        SHA256 of (summary || resolution_statement) — gates
                                 resolution_summary generation
    """

    incident_id: str
    root_cause_summary_hash: str | None
    description_hash: str | None


class IncidentIOIncidentDAO(BaseUpsertDAO):
    """DAO for incidentio_incidents table with batch operations."""

    def __init__(
        self,
        engine: Engine,
        metrics: "DBMetrics | None" = None,
        source_type: str = "incidentio",
    ) -> None:
        """
        Initialize the DAO with a SQLAlchemy engine.

        Args:
            engine: SQLAlchemy engine for database operations
            metrics: Optional DBMetrics for query instrumentation
            source_type: Registered DataSourceSpec to bind this DAO to. Both
                "incidentio" and "incident_channel_summary" write to this same
                table via distinct specs (the latter only via its enrichment
                route, update_llm_fields_from_message); defaults to
                "incidentio" for backward compatibility.
        """
        from common.datasources.registry import get_source

        super().__init__(get_source(source_type), engine, metrics)

    def find_incident(self, reference_id: str) -> IncidentIOIncident | None:
        """
        Find an incident by reference_id (e.g., INC-123).

        Args:
            reference_id: Reference ID displayed across product (e.g., INC-1234)

        Returns:
            IncidentIOIncident if found, None if not found or on error
        """
        record = (
            self._metrics.start_query("select", "incidentio_incidents")
            if self._metrics
            else None
        )
        try:
            stmt = select(incidentio_incidents_table).where(
                incidentio_incidents_table.c.reference_id == reference_id,
            )

            with self._engine.connect() as conn:
                result = conn.execute(stmt).fetchone()

            if result is None:
                logger.debug("no incident found", reference_id=reference_id)
                if record:
                    record(None)
                return None

            if record:
                record(None)
            return IncidentIOIncident.model_validate(result._mapping)
        except Exception as e:
            logger.exception(
                "error finding incident", reference_id=reference_id, error=str(e)
            )
            if record:
                try:
                    record(e)
                except Exception:
                    logger.warning("Failed to record metrics")
            return None

    def find_incident_by_id(self, incident_id: str) -> IncidentIOIncident | None:
        """Find an incident by its internal incident_id.

        Args:
            incident_id: Internal Incident.io ID (e.g., 01K3H5K30V3TECAF9G2HD1X5ZB)

        Returns:
            IncidentIOIncident if found, None if not found or on error
        """
        record = (
            self._metrics.start_query("select", "incidentio_incidents")
            if self._metrics
            else None
        )
        try:
            stmt = select(incidentio_incidents_table).where(
                incidentio_incidents_table.c.incident_id == incident_id,
            )

            with self._engine.connect() as conn:
                result = conn.execute(stmt).fetchone()

            if result is None:
                logger.debug("no incident found", incident_id=incident_id)
                if record:
                    record(None)
                return None

            if record:
                record(None)
            return IncidentIOIncident.model_validate(result._mapping)
        except Exception as e:
            logger.exception(
                "error finding incident", incident_id=incident_id, error=str(e)
            )
            if record:
                try:
                    record(e)
                except Exception:
                    logger.warning("Failed to record metrics")
            return None

    def get_llm_data_by_incident_ids(
        self, incident_ids: list[str]
    ) -> dict[str, dict[str, str | None]]:
        """
        Get LLM data (hash and summaries) for a list of incident_ids.

        Args:
            incident_ids: List of incident IDs

        Returns:
            Dict mapping incident_id to dict with:
            - root_cause_summary_hash: str | None
            - root_cause_summary: str | None
            - description_summary: str | None
            - description_hash: str | None
        """
        if not incident_ids:
            return {}

        record = (
            self._metrics.start_query("select", "incidentio_incidents")
            if self._metrics
            else None
        )
        try:
            stmt = select(
                incidentio_incidents_table.c.incident_id,
                incidentio_incidents_table.c.root_cause_summary_hash,
                incidentio_incidents_table.c.root_cause_summary,
                incidentio_incidents_table.c.description_summary,
                incidentio_incidents_table.c.description_hash,
            ).where(
                incidentio_incidents_table.c.incident_id.in_(incident_ids),
            )

            with self._engine.connect() as conn:
                results = conn.execute(stmt).fetchall()

            if record:
                record(None)
            return {
                row.incident_id: {
                    "root_cause_summary_hash": row.root_cause_summary_hash,
                    "root_cause_summary": row.root_cause_summary,
                    "description_summary": row.description_summary,
                    "description_hash": row.description_hash,
                }
                for row in results
            }
        except Exception as e:
            logger.exception("error fetching LLM data", error=str(e))
            if record:
                try:
                    record(e)
                except Exception:
                    logger.warning("Failed to record metrics")
            return {}

    def get_incident_hashes_by_ids(
        self, incident_ids: list[str]
    ) -> dict[str, IncidentIOHashInfo] | None:
        """Get hash columns for a list of incidents, used by the Enricher for change detection.

        Returns only the two hash columns needed for enrichment decisions — no LLM summary
        text is included. Processes in batches of BATCH_SIZE to avoid large IN clauses.
        Keyed by incident_id for O(1) lookup.

        Returns None on database error (so the caller can return HTTP 500) rather than an
        empty dict, which would silently cause the Enricher to skip all enrichments.

        Args:
            incident_ids: List of Incident.io incident IDs (e.g., ["01JXXX", "01JYYY"]).

        Returns:
            Dict mapping incident_id -> IncidentIOHashInfo on success, None on error.
        """
        if not incident_ids:
            return {}

        hash_map: dict[str, IncidentIOHashInfo] = {}
        record = None

        try:
            # Process in batches to avoid MySQL max_allowed_packet limits on large IN clauses
            for i in range(0, len(incident_ids), BATCH_SIZE):
                chunk = incident_ids[i : i + BATCH_SIZE]

                stmt = select(
                    incidentio_incidents_table.c.incident_id,
                    incidentio_incidents_table.c.root_cause_summary_hash,
                    incidentio_incidents_table.c.description_hash,
                ).where(incidentio_incidents_table.c.incident_id.in_(chunk))

                record = (
                    self._metrics.start_query("select", "incidentio_incidents")
                    if self._metrics
                    else None
                )
                with self._engine.connect() as conn:
                    results = conn.execute(stmt).fetchall()
                if record:
                    record(None)

                for row in results:
                    hash_map[row.incident_id] = IncidentIOHashInfo(
                        incident_id=row.incident_id,
                        root_cause_summary_hash=row.root_cause_summary_hash,
                        description_hash=row.description_hash,
                    )

            logger.debug(
                "retrieved incident hashes",
                hash_count=len(hash_map),
                id_count=len(incident_ids),
            )
            return hash_map
        except Exception as e:
            if record:
                try:
                    record(e)
                except Exception:
                    logger.warning("Failed to record metrics")
            logger.exception("error fetching incident hashes", error=str(e))
            return None

    def get_incident_channel_summary_hashes_by_reference_ids(
        self, reference_ids: list[str]
    ) -> dict[str, str | None] | None:
        """Get incident_channel_summary_hash for a list of reference_ids.

        Used by the Enricher for change detection on the incident_channel_summary
        source (keyed on reference_id, unlike the incidentio hashes above which
        key on incident_id). Processes in batches of BATCH_SIZE to avoid large
        IN clauses.

        Returns None on database error (so the caller can return HTTP 500)
        rather than an empty dict, which would silently cause the Enricher to
        skip all enrichments.

        Args:
            reference_ids: List of Incident.io reference ids (e.g., ["INC-1234"]).

        Returns:
            Dict mapping reference_id -> incident_channel_summary_hash (or
            None if the column itself is NULL) for rows found; on success,
            None on error.
        """
        if not reference_ids:
            return {}

        hash_map: dict[str, str | None] = {}
        record = None

        try:
            for i in range(0, len(reference_ids), BATCH_SIZE):
                chunk = reference_ids[i : i + BATCH_SIZE]

                stmt = select(
                    incidentio_incidents_table.c.reference_id,
                    incidentio_incidents_table.c.incident_channel_summary_hash,
                ).where(incidentio_incidents_table.c.reference_id.in_(chunk))

                record = (
                    self._metrics.start_query("select", "incidentio_incidents")
                    if self._metrics
                    else None
                )
                with self._engine.connect() as conn:
                    results = conn.execute(stmt).fetchall()
                if record:
                    record(None)

                for row in results:
                    hash_map[row.reference_id] = row.incident_channel_summary_hash

            logger.debug(
                "retrieved incident channel summary hashes",
                hash_count=len(hash_map),
                id_count=len(reference_ids),
            )
            return hash_map
        except Exception as e:
            if record:
                try:
                    record(e)
                except Exception:
                    logger.warning("Failed to record metrics")
            logger.error("error fetching incident channel summary hashes", error=str(e))
            return None

    def find_incidents_by_reference_ids(
        self, reference_ids: list[str]
    ) -> list[IncidentIOIncident]:
        """
        Find incidents by a list of reference_ids.

        Args:
            reference_ids: List of reference IDs (e.g., ["INC-123", "INC-456"])

        Returns:
            List of IncidentIOIncident models, empty list if none found or on error
        """
        if not reference_ids:
            return []

        record = (
            self._metrics.start_query("select", "incidentio_incidents")
            if self._metrics
            else None
        )
        try:
            stmt = select(incidentio_incidents_table).where(
                incidentio_incidents_table.c.reference_id.in_(reference_ids),
            )

            with self._engine.connect() as conn:
                results = conn.execute(stmt).fetchall()

            if record:
                record(None)
            return [IncidentIOIncident.model_validate(row._mapping) for row in results]
        except Exception as e:
            logger.exception(
                "error finding incidents by reference_ids",
                count=len(reference_ids),
                error=str(e),
            )
            if record:
                try:
                    record(e)
                except Exception:
                    logger.warning("Failed to record metrics")
            return []

    def insert_new_incident(self, incident: IncidentIOIncident) -> int | None:
        """
        Insert a new incident record.

        Args:
            incident: IncidentIOIncident model to insert

        Returns:
            Auto-generated database ID on success, None on error
        """
        record = (
            self._metrics.start_query("insert", "incidentio_incidents")
            if self._metrics
            else None
        )
        try:
            # Normalize all timestamps to UTC (defensive, matches Go implementation)
            stmt = insert(incidentio_incidents_table).values(
                incident_id=incident.incident_id,
                reference_id=incident.reference_id,
                severity=incident.severity,
                slack_channel_id=incident.slack_channel_id,
                status=incident.status,
                visibility=incident.visibility,
                created_at=parse_timestamp_to_utc(incident.created_at),
                reported_at=parse_timestamp_to_utc(incident.reported_at),
                updated_at=parse_timestamp_to_utc(incident.updated_at),
                accepted_at=parse_timestamp_to_utc(incident.accepted_at),
                declined_at=parse_timestamp_to_utc(incident.declined_at),
                canceled_at=parse_timestamp_to_utc(incident.canceled_at),
                resolved_at=parse_timestamp_to_utc(incident.resolved_at),
                impact_started_at=parse_timestamp_to_utc(incident.impact_started_at),
                closed_at=parse_timestamp_to_utc(incident.closed_at),
                impacted_parties=incident.impacted_parties,
                impacted_core_functions=incident.impacted_core_functions,
                core_booking_hosting_flow_impacted=incident.core_booking_hosting_flow_impacted,
                affected_services=incident.affected_services,
                root_cause_service=incident.root_cause_service,
                root_cause_change_type=incident.root_cause_change_type,
                root_cause_change_type_other=incident.root_cause_change_type_other,
                root_cause_change_type_config=incident.root_cause_change_type_config,
                environment=incident.environment,
                detection_methods=incident.detection_methods,
                detection_link=incident.detection_link,
                status_category=incident.status_category,
                root_cause_summary=incident.root_cause_summary,
                root_cause_summary_hash=incident.root_cause_summary_hash,
                description_summary=incident.description_summary,
                description_hash=incident.description_hash,
            )

            result = execute_with_retry(
                self._engine, stmt, op="insert:incidentio_incidents"
            )

            logger.info(
                "inserted incident",
                reference_id=incident.reference_id,
                incident_id=incident.incident_id,
                id=result.lastrowid,
            )
            if record:
                record(None)
            return result.lastrowid
        except Exception as e:
            logger.exception(
                "error inserting incident",
                reference_id=incident.reference_id,
                incident_id=incident.incident_id,
                error=str(e),
            )
            if record:
                try:
                    record(e)
                except Exception:
                    logger.warning("Failed to record metrics")
            return None

    def update_incident(self, incident: IncidentIOIncident) -> int | None:
        """
        Update an existing incident record.

        Args:
            incident: IncidentIOIncident model with updated values (must have id set)

        Returns:
            Incident database ID on success, None on error
        """
        record = (
            self._metrics.start_query("update", "incidentio_incidents")
            if self._metrics
            else None
        )
        try:
            # Normalize all timestamps to UTC (defensive, matches Go implementation)
            stmt = (
                update(incidentio_incidents_table)
                .where(incidentio_incidents_table.c.id == incident.id)
                .values(
                    severity=incident.severity,
                    slack_channel_id=incident.slack_channel_id,
                    status=incident.status,
                    visibility=incident.visibility,
                    updated_at=parse_timestamp_to_utc(incident.updated_at),
                    accepted_at=parse_timestamp_to_utc(incident.accepted_at),
                    declined_at=parse_timestamp_to_utc(incident.declined_at),
                    canceled_at=parse_timestamp_to_utc(incident.canceled_at),
                    resolved_at=parse_timestamp_to_utc(incident.resolved_at),
                    impact_started_at=parse_timestamp_to_utc(
                        incident.impact_started_at
                    ),
                    closed_at=parse_timestamp_to_utc(incident.closed_at),
                    impacted_parties=incident.impacted_parties,
                    impacted_core_functions=incident.impacted_core_functions,
                    core_booking_hosting_flow_impacted=incident.core_booking_hosting_flow_impacted,
                    affected_services=incident.affected_services,
                    root_cause_service=incident.root_cause_service,
                    root_cause_change_type=incident.root_cause_change_type,
                    root_cause_change_type_other=incident.root_cause_change_type_other,
                    root_cause_change_type_config=incident.root_cause_change_type_config,
                    environment=incident.environment,
                    detection_methods=incident.detection_methods,
                    detection_link=incident.detection_link,
                    status_category=incident.status_category,
                    root_cause_summary=incident.root_cause_summary,
                    root_cause_summary_hash=incident.root_cause_summary_hash,
                    description_summary=incident.description_summary,
                    description_hash=incident.description_hash,
                )
            )

            execute_with_retry(self._engine, stmt, op="update:incidentio_incidents")

            logger.info(
                "updated incident", reference_id=incident.reference_id, id=incident.id
            )
            if record:
                record(None)
            return incident.id
        except Exception as e:
            logger.exception(
                "error updating incident",
                reference_id=incident.reference_id,
                id=incident.id,
                error=str(e),
            )
            if record:
                try:
                    record(e)
                except Exception:
                    logger.warning("Failed to record metrics")
            return None

    def insert_or_update_incident(self, incident: IncidentIOIncident) -> int | None:
        """
        Insert or update an incident (upsert).

        Checks if incident exists by reference_id. If not found, inserts.
        If found, updates.

        Args:
            incident: IncidentIOIncident model

        Returns:
            Incident database ID on success, None on error
        """
        existing = self.find_incident(incident.reference_id)

        if existing is None:
            return self.insert_new_incident(incident)

        # Update existing incident with database ID
        incident.id = existing.id
        return self.update_incident(incident)

    def upsert_incidents_batch(self, incidents: list[IncidentIOIncident]) -> int | None:
        """
        Batch upsert using INSERT ... ON DUPLICATE KEY UPDATE.

        Requires unique index on (incident_id, reference_id). Delegates to the
        spec-driven base ``upsert_batch``: rows are derived generically from the
        model and only the spec's ``update_columns`` are written on conflict —
        it never overwrites the four LLM-enriched columns.

        Args:
            incidents: List of IncidentIOIncident models to upsert

        Returns:
            Total number of affected rows on success, None on error
        """
        return self.upsert_batch(incidents)

    def _upsert_batch(self, incidents: list[IncidentIOIncident]) -> int | None:
        """Upsert a single chunk (<= BATCH_SIZE) using ON DUPLICATE KEY UPDATE.

        Thin wrapper over the shared base implementation, retained for callers
        (and tests) that exercise a single pre-chunked batch directly.

        Args:
            incidents: List of IncidentIOIncident models to upsert (<= BATCH_SIZE).

        Returns:
            Number of affected rows on success, None on error.
        """
        return self._upsert_chunk(incidents, self._spec.update_columns)

    def update_llm_fields(
        self,
        incident_id: str,
        root_cause_summary: str | None,
        root_cause_summary_hash: str | None,
        description_summary: str | None,
        description_hash: str | None,
        entered_at: datetime | None = None,
    ) -> WriteOutcome:
        """Update LLM-generated fields on an existing incident.

        Does NOT overwrite base fields (severity, status, timestamps, etc.).
        Identified by incident_id (the Incident.io internal ID). Delegates to the
        spec-driven base partial update (drops None values so a missing field
        never overwrites an existing one).

        Args:
            incident_id: Incident.io internal ID (e.g. 01K3H5K30V3TECAF9G2HD1X5ZB)
            root_cause_summary: LLM-generated root cause summary
            root_cause_summary_hash: SHA256 of summary + resolution_statement
            description_summary: LLM-generated description summary
            description_hash: SHA256 of incident name + summary
            entered_at: The enrichment write-group's staleness-guard timestamp
                (ADR 024), if this call has a message envelope to source one
                from. ``None`` (the default — no envelope available) makes
                the write unconditional regardless of the spec's staleness
                guard, same as before this parameter existed; callers that
                *do* have an ``entered_at`` should pass it so the guard
                applies here too.

        Returns:
            ``WriteOutcome.WRITTEN`` on success, ``WriteOutcome.BASE_NOT_FOUND``
            if no row matched, ``WriteOutcome.ERROR`` on database error.
        """
        return self.update_llm_fields_from_message(
            incident_id=incident_id,
            root_cause_summary=root_cause_summary,
            root_cause_summary_hash=root_cause_summary_hash,
            description_summary=description_summary,
            description_hash=description_hash,
            entered_at=entered_at,
        )
