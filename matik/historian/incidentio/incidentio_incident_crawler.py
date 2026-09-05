"""IncidentIO Incident Crawler for fetching and storing incidents.

The generic producer/consumer batch-dispatch machinery (plus ``CrawlerError`` /
``CrawlerResult``) lives in ``historian.base.crawler.BaseCrawler``; this module
implements only the Incident.io-specific hooks (tracker, cursor, page fetch, and
message publishing).
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from common.clients.incidentio_client import (
    IncidentIOClient,
    IncidentWithRawFields,
)
from common.clients.matik_api_client import MatikApiClient
from common.clients.sqs_publisher import SQSPublisher
from common.metrics import JobMetrics
from common.models.enricher_messages import EnrichmentRequest
from common.models.incidentio_config import (
    DEFAULT_CONSUMER_TIMEOUT_SECONDS,
    DEFAULT_WRITE_BATCH_SIZE,
    IncidentIOConfig,
)
from common.models.incidentio_tracker import IncidentIOTracker
from common.models.scribe_messages import IncidentIOBaseMessage
from common.queues.enrichment_publisher import EnrichmentPublisher
from common.utils import log_utils
from common.utils.datetime_utils import parse_date_string, utc_now_naive
from historian.base.crawler import BaseCrawler, CrawlerError

logger = log_utils.get_logger(__name__)


@dataclass
class FetchCursor:
    """Holds date filters for the Incident.io API.

    Only one of created_at_gte or updated_at_gte should be set:
    - created_at_gte: Used on initial sync (filter by creation date)
    - updated_at_gte: Used on subsequent syncs (filter by update date)
    """

    created_at_gte: datetime | None = None
    updated_at_gte: datetime | None = None


class IncidentIOIncidentCrawler(BaseCrawler[IncidentWithRawFields, IncidentIOTracker]):
    """Crawler for fetching incidents from Incident.io and storing them via API.

    Flow:
    1. Get tracker from API to determine sync state
    2. Check if tracker status is ERROR - abort if so
    3. Calculate date filter:
       - First run (initial_sync_complete=False): use created_at[gte] with config.start_date
       - Subsequent runs: use updated_at[gte] with now - config.lookback_days
    4. Fetch incidents from Incident.io API with the appropriate date filter
    5. Batch upsert incidents via API
    6. Update tracker with initial_sync_complete=True on success
    """

    # Preserve the historical batch-write-failure wording for this source.
    _WRITE_FAILURE_MESSAGE = "Failed to upsert some incidents to database"

    def __init__(
        self,
        incidentio_config: IncidentIOConfig,
        matik_client: MatikApiClient,
        incidentio_client: IncidentIOClient,
        job_metrics: JobMetrics | None = None,
        sqs_publisher: SQSPublisher | None = None,
        enrichment_publisher: EnrichmentPublisher | None = None,
    ) -> None:
        """Initialize the crawler.

        Args:
            incidentio_config: Configuration for Incident.io API
            matik_client: Client for Matik API calls
            incidentio_client: Client for Incident.io API calls
            job_metrics: JobMetrics for recording job execution metrics (optional)
            sqs_publisher: SQS publisher for writing incidents via Scribe (optional)
            enrichment_publisher: Publishes enrichment requests to SQS for the
                Enricher to process asynchronously (required for LLM summaries)
        """
        self._incidentio_config = incidentio_config

        self._matik_client = matik_client
        self._incidentio_client = incidentio_client
        self._job_metrics = job_metrics
        self._sqs_publisher = sqs_publisher
        self._enrichment_publisher = enrichment_publisher

    def _calculate_fetch_cursor(self, tracker: IncidentIOTracker) -> FetchCursor:
        """Calculate the date filter cursor based on tracker state.

        Logic:
        1. If last_updated_at_cursor exists (from failed run), use it for retry
           - initial_sync_complete=False → created_at filter
           - initial_sync_complete=True → updated_at filter
        2. Initial sync: use created_at filter with config.start_date
        3. Subsequent sync: use updated_at filter with lookback_days

        Args:
            tracker: Current tracker state

        Returns:
            FetchCursor with the appropriate date filter set
        """
        # If there's a saved cursor from a failed run, use it for retry
        if tracker.last_updated_at_cursor:
            if not tracker.initial_sync_complete:
                logger.info(
                    "Using saved cursor as created_at filter (initial sync retry)",
                    last_updated_at_cursor=tracker.last_updated_at_cursor.isoformat(),
                )
                return FetchCursor(created_at_gte=tracker.last_updated_at_cursor)
            else:
                logger.info(
                    "Using saved cursor as updated_at filter (subsequent sync retry)",
                    last_updated_at_cursor=tracker.last_updated_at_cursor.isoformat(),
                )
                return FetchCursor(updated_at_gte=tracker.last_updated_at_cursor)

        # Calculate cursor based on sync state
        if not tracker.initial_sync_complete:
            # First run: use config.start_date if set, as created_at filter
            if self._incidentio_config.start_date:
                cursor = parse_date_string(self._incidentio_config.start_date)
                logger.info(
                    "Initial sync: filtering by created_at >= start_date",
                    start_date=self._incidentio_config.start_date,
                )
                return FetchCursor(created_at_gte=cursor)
            else:
                logger.info("Initial sync: no start_date, fetching all incidents")
                return FetchCursor()
        else:
            # Subsequent run: use lookback_days as updated_at filter
            lookback_days = self._incidentio_config.lookback_days
            cursor = utc_now_naive() - timedelta(days=lookback_days)
            logger.info(
                "Subsequent sync: filtering by updated_at >= lookback",
                lookback_days=lookback_days,
                cursor=cursor.isoformat(),
            )
            return FetchCursor(updated_at_gte=cursor)

    def _get_tracker(self) -> IncidentIOTracker | None:
        """Get the current tracker from the API."""
        try:
            response_bytes = self._matik_client.get_request(
                "/v1/incidentio/incident/tracker/lastrecorded"
            )
            response = json.loads(response_bytes)
            if response is None:
                logger.warning("No tracker found in database")
                return None
            return IncidentIOTracker.model_validate(response)
        except Exception:
            logger.exception("Failed to get tracker from API")
            return None

    def _check_tracker_status(self, tracker: IncidentIOTracker) -> None:
        """Check if tracker is in ERROR state and raise if so.

        Args:
            tracker: The tracker to check

        Raises:
            CrawlerError: If tracker status is ERROR
        """
        if tracker.status == "ERROR":
            error_msg = tracker.error_message or "Unknown error"
            raise CrawlerError(
                f"Cannot proceed - tracker is in ERROR state: {error_msg}"
            )

    def _update_tracker(
        self,
        tracker: IncidentIOTracker,
        cursor: FetchCursor | None = None,
        error: Exception | None = None,
        mark_sync_complete: bool = False,
    ) -> bool:
        """Update the tracker via API.

        Args:
            tracker: Current tracker
            cursor: The FetchCursor used in this sync attempt
            error: Optional error that occurred during processing
            mark_sync_complete: Whether to mark initial_sync_complete as True

        Returns:
            True on success, False on error
        """
        # Update timestamp
        tracker.timestamp = utc_now_naive()

        # Extract the datetime from the FetchCursor for saving to tracker
        cursor_dt = None
        if cursor:
            cursor_dt = cursor.created_at_gte or cursor.updated_at_gte

        if error:
            tracker.status = "ERROR"
            tracker.error_message = str(error)[:3072]  # Truncate to column limit
            # Save cursor for retry
            tracker.last_updated_at_cursor = cursor_dt
            logger.error(
                "Setting tracker to ERROR state",
                error=str(error),
                last_updated_at_cursor=cursor_dt.isoformat() if cursor_dt else None,
            )
        else:
            tracker.status = "OK"
            tracker.error_message = None
            # Clear cursor on success
            tracker.last_updated_at_cursor = None
            if mark_sync_complete:
                tracker.initial_sync_complete = True
                logger.info("Marking initial sync as complete")

        try:
            self._matik_client.post_json_request(
                "/v1/incidentio/incident/tracker/lastrecorded",
                tracker.model_dump(mode="json"),
            )
            logger.info("Successfully updated tracker via API")
            return True
        except Exception:
            logger.exception("Failed to update tracker via API")
            return False

    async def _producer(
        self,
        queue: asyncio.Queue[IncidentWithRawFields | None],
        fetch_cursor: FetchCursor | None,
    ) -> int:
        """Producer: Fetch pages from Incident.io and put incidents in queue.

        Args:
            queue: Queue to put incidents into
            fetch_cursor: FetchCursor with date filter for API

        Returns:
            Total number of incidents fetched
        """
        client = self._incidentio_client
        page_size = self._incidentio_config.page_size
        page_count = 0
        total_fetched = 0
        after_cursor: str | None = None  # Pagination cursor (incident ID)

        # Convert datetime fields to strings for API
        created_at_gte_str = (
            fetch_cursor.created_at_gte.strftime("%Y-%m-%d")
            if fetch_cursor and fetch_cursor.created_at_gte
            else None
        )
        updated_at_gte_str = (
            fetch_cursor.updated_at_gte.strftime("%Y-%m-%d")
            if fetch_cursor and fetch_cursor.updated_at_gte
            else None
        )

        filter_type = (
            "created_at"
            if created_at_gte_str
            else "updated_at"
            if updated_at_gte_str
            else "none"
        )
        filter_value = created_at_gte_str or updated_at_gte_str or "(all)"
        logger.info(
            "Producer: using date filter",
            filter_type=filter_type,
            filter_value=filter_value,
        )

        try:
            while True:
                page_count += 1
                logger.info(
                    "Producer: fetching page",
                    page=page_count,
                    filter_type=filter_type,
                    filter_value=filter_value,
                    after=after_cursor or "(start)",
                )

                (
                    incidents_with_raw,
                    raw_count,
                    last_raw_id,
                ) = await client.list_incidents(
                    after=after_cursor,
                    page_size=page_size,
                    updated_at_gte=updated_at_gte_str,
                    created_at_gte=created_at_gte_str,
                    incident_type_ids=self._incidentio_config.incident_type_ids,
                )

                logger.info(
                    "Producer: fetched incidents",
                    page=page_count,
                    count=len(incidents_with_raw),
                    raw_count=raw_count,
                )

                # Put each incident into the queue
                for item in incidents_with_raw:
                    await queue.put(item)

                total_fetched += len(incidents_with_raw)

                # Update pagination cursor using last raw ID (before filtering)
                after_cursor = last_raw_id

                # Check if we've reached the last page (using raw count before filtering)
                if raw_count < page_size:
                    logger.info("Producer: reached last page", total_pages=page_count)
                    break

        finally:
            # Signal consumer that we're done
            await queue.put(None)
            logger.info(
                "Producer: finished, sent done signal", total_fetched=total_fetched
            )

        return total_fetched

    async def _publish_enrichment_messages(
        self, items: list[IncidentWithRawFields]
    ) -> None:
        """Publish enrichment messages to SQS for async LLM processing.

        Forwards raw content for every item unconditionally. The enricher
        owns hash-based change detection — the historian does not pre-filter.
        """
        publisher = self._enrichment_publisher
        if publisher is None:
            return

        import uuid

        try:
            for item in items:
                message = EnrichmentRequest(
                    source_type="incidentio",
                    producer="historian",
                    task_id=str(uuid.uuid4()),
                    entity_id={"incident_id": item.incident.incident_id},
                    content={
                        "name": item.name,
                        "summary": item.summary,
                        "resolution_statement": item.resolution_statement,
                        "reference_id": item.incident.reference_id,
                    },
                    entered_at=utc_now_naive(),
                )
                await publisher.publish(message)
        except Exception:
            logger.exception("Failed to publish enrichment messages to SQS")
            if self._job_metrics:
                self._job_metrics.record_job_error(
                    "incidentio_incidents", "enrichment_publish_error"
                )
            return

        logger.info(
            "Published enrichment messages",
            total=len(items),
        )

    def _write_batch_size(self) -> int:
        """Consumer batch size from config (or the default)."""
        return self._incidentio_config.write_batch_size or DEFAULT_WRITE_BATCH_SIZE

    def _consumer_timeout_seconds(self) -> float:
        """Consumer idle timeout in seconds from config (or the default)."""
        return (
            self._incidentio_config.consumer_timeout_seconds
            or DEFAULT_CONSUMER_TIMEOUT_SECONDS
        )

    def _initial_sync_complete(self, tracker: IncidentIOTracker) -> bool:
        """Whether the tracker's initial backfill sync has completed."""
        return tracker.initial_sync_complete

    async def _publish_records(self, items: list[IncidentWithRawFields]) -> int | None:
        """Base-record publish hook — delegates to the incidentio SQS publish."""
        return await self._publish_incidents_to_sqs(items)

    async def _publish_incidents_to_sqs(
        self, items: list[IncidentWithRawFields]
    ) -> int | None:
        """Publish incidents to SQS for Scribe to write to the database.

        Args:
            items: List of IncidentWithRawFields to publish (only .incident is used)

        Returns:
            Number of incidents published, or None on error
        """
        if not items:
            return 0

        if self._sqs_publisher is None:
            return 0

        try:
            for item in items:
                message = IncidentIOBaseMessage(
                    data=item.incident.model_dump(mode="json"),
                    entered_at=utc_now_naive(),
                )
                await self._sqs_publisher.send(message)
            return len(items)
        except Exception:
            logger.exception("Failed to publish incidents to SQS")
            if self._job_metrics:
                self._job_metrics.record_job_error(
                    "incidentio_incidents", "sqs_publish_error"
                )
            return None
