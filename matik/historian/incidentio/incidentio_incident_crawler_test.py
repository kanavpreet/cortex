"""Tests for IncidentIO Incident Crawler."""

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.clients.incidentio_client import IncidentWithRawFields
from common.models.incidentio_config import IncidentIOConfig
from common.models.incidentio_incident import IncidentIOIncident
from common.models.incidentio_tracker import IncidentIOTracker
from historian.base.crawler import CrawlerError
from historian.incidentio.incidentio_incident_crawler import (
    FetchCursor,
    IncidentIOIncidentCrawler,
)


def _create_incidentio_config(
    start_date: str | None = None,
    lookback_days: int = 7,
) -> IncidentIOConfig:
    """Create test IncidentIO config."""
    return IncidentIOConfig(
        api_key="test-key",
        start_date=start_date,
        lookback_days=lookback_days,
        page_size=100,
        write_batch_size=10,
        consumer_timeout_seconds=5,
        root_cause_prompt="Test root cause prompt",
        description_prompt="Test description prompt",
    )


def _create_tracker(
    initial_sync_complete: bool = False,
    status: str = "OK",
    last_updated_at_cursor: datetime | None = None,
    error_message: str | None = None,
) -> IncidentIOTracker:
    """Create test tracker."""
    return IncidentIOTracker(
        timestamp=datetime(2024, 1, 15, 10, 0, 0),
        status=status,
        initial_sync_complete=initial_sync_complete,
        last_updated_at_cursor=last_updated_at_cursor,
        error_message=error_message,
    )


def _create_incident(
    incident_id: str = "INC-123",
    reference_id: str = "REF-123",
) -> IncidentIOIncident:
    """Create test incident."""
    return IncidentIOIncident(
        incident_id=incident_id,
        reference_id=reference_id,
        severity="Sev-1",
        slack_channel_id="C123456",
        status="open",
        visibility="public",
        created_at=datetime(2024, 1, 15, 10, 0, 0),
        reported_at=datetime(2024, 1, 15, 10, 0, 0),
        updated_at=datetime(2024, 1, 15, 10, 0, 0),
    )


def _create_incident_with_raw(
    incident_id: str = "INC-123",
    name: str | None = "Test incident",
    summary: str | None = "Test summary",
    resolution_statement: str | None = None,
    status_category: str | None = "closed",
) -> IncidentWithRawFields:
    """Create test IncidentWithRawFields."""
    incident = _create_incident(incident_id=incident_id)
    incident.status_category = status_category
    return IncidentWithRawFields(
        incident=incident,
        name=name,
        summary=summary,
        resolution_statement=resolution_statement,
    )


def _create_crawler(
    matik_client: MagicMock | None = None,
    incidentio_client: MagicMock | None = None,
    config: IncidentIOConfig | None = None,
    job_metrics: MagicMock | None = None,
    sqs_publisher: MagicMock | None = None,
    enrichment_publisher: MagicMock | None = None,
) -> IncidentIOIncidentCrawler:
    """Create a crawler instance with mocked dependencies."""
    return IncidentIOIncidentCrawler(
        incidentio_config=config or _create_incidentio_config(),
        matik_client=matik_client or MagicMock(),
        incidentio_client=incidentio_client or MagicMock(),
        job_metrics=job_metrics,
        sqs_publisher=sqs_publisher,
        enrichment_publisher=enrichment_publisher,
    )


class TestIncidentIOIncidentCrawlerInit:
    """Tests for crawler initialization."""

    def test_init_with_required_params(self) -> None:
        """Test initialization with required parameters."""
        matik_client = MagicMock()
        incidentio_client = MagicMock()
        config = _create_incidentio_config()

        crawler = IncidentIOIncidentCrawler(
            incidentio_config=config,
            matik_client=matik_client,
            incidentio_client=incidentio_client,
        )

        assert crawler._matik_client == matik_client
        assert crawler._incidentio_client == incidentio_client
        assert crawler._incidentio_config == config
        assert crawler._job_metrics is None

    def test_init_with_all_params(self) -> None:
        """Test initialization with all parameters."""
        matik_client = MagicMock()
        incidentio_client = MagicMock()
        job_metrics = MagicMock()
        config = _create_incidentio_config()

        crawler = IncidentIOIncidentCrawler(
            incidentio_config=config,
            matik_client=matik_client,
            incidentio_client=incidentio_client,
            job_metrics=job_metrics,
        )

        assert crawler._job_metrics == job_metrics


class TestCalculateFetchCursor:
    """Tests for _calculate_fetch_cursor method."""

    def test_uses_saved_cursor_from_failed_initial_sync(self) -> None:
        """Test that saved cursor from failed initial sync uses created_at filter."""
        crawler = _create_crawler()
        saved_cursor = datetime(2024, 1, 10, 0, 0, 0)
        tracker = _create_tracker(
            initial_sync_complete=False,
            last_updated_at_cursor=saved_cursor,
        )

        result = crawler._calculate_fetch_cursor(tracker)
        assert isinstance(result, FetchCursor)
        assert result.created_at_gte == saved_cursor
        assert result.updated_at_gte is None

    def test_uses_saved_cursor_from_failed_subsequent_sync(self) -> None:
        """Test that saved cursor from failed subsequent sync uses updated_at filter."""
        crawler = _create_crawler()
        saved_cursor = datetime(2024, 1, 10, 0, 0, 0)
        tracker = _create_tracker(
            initial_sync_complete=True,
            last_updated_at_cursor=saved_cursor,
        )

        result = crawler._calculate_fetch_cursor(tracker)
        assert isinstance(result, FetchCursor)
        assert result.updated_at_gte == saved_cursor
        assert result.created_at_gte is None

    def test_initial_sync_with_start_date(self) -> None:
        """Test initial sync uses created_at filter with start_date from config."""
        crawler = _create_crawler(
            config=_create_incidentio_config(start_date="2024-01-01")
        )
        tracker = _create_tracker(initial_sync_complete=False)

        result = crawler._calculate_fetch_cursor(tracker)
        assert isinstance(result, FetchCursor)
        assert result.created_at_gte == datetime(2024, 1, 1, 0, 0, 0)
        assert result.updated_at_gte is None

    def test_initial_sync_without_start_date(self) -> None:
        """Test initial sync without start_date returns empty FetchCursor."""
        crawler = _create_crawler(config=_create_incidentio_config(start_date=None))
        tracker = _create_tracker(initial_sync_complete=False)

        result = crawler._calculate_fetch_cursor(tracker)
        assert isinstance(result, FetchCursor)
        assert result.created_at_gte is None
        assert result.updated_at_gte is None

    def test_subsequent_sync_uses_lookback(self) -> None:
        """Test subsequent sync uses updated_at filter with lookback_days."""
        crawler = _create_crawler(config=_create_incidentio_config(lookback_days=7))
        tracker = _create_tracker(initial_sync_complete=True)

        with patch(
            "historian.incidentio.incidentio_incident_crawler.utc_now_naive"
        ) as mock_now:
            mock_now.return_value = datetime(2024, 1, 15, 10, 0, 0)
            result = crawler._calculate_fetch_cursor(tracker)
            expected = datetime(2024, 1, 15, 10, 0, 0) - timedelta(days=7)
            assert isinstance(result, FetchCursor)
            assert result.updated_at_gte == expected
            assert result.created_at_gte is None


class TestCheckTrackerStatus:
    """Tests for _check_tracker_status method."""

    def test_ok_status_passes(self) -> None:
        """Test that OK status does not raise."""
        crawler = _create_crawler()
        tracker = _create_tracker(status="OK")
        crawler._check_tracker_status(tracker)  # Should not raise

    def test_error_status_raises(self) -> None:
        """Test that ERROR status raises CrawlerError."""
        crawler = _create_crawler()
        tracker = _create_tracker(status="ERROR", error_message="Previous failure")

        with pytest.raises(CrawlerError, match="tracker is in ERROR state"):
            crawler._check_tracker_status(tracker)


class TestGetTracker:
    """Tests for _get_tracker method."""

    def test_returns_tracker_on_success(self) -> None:
        """Test returns tracker when API call succeeds."""
        mock_client = MagicMock()
        tracker_data = {
            "timestamp": "2024-01-15T10:00:00",
            "status": "OK",
            "initial_sync_complete": True,
        }
        mock_client.get_request.return_value = json.dumps(tracker_data).encode()

        crawler = _create_crawler(matik_client=mock_client)

        result = crawler._get_tracker()
        assert result is not None
        assert result.status == "OK"
        assert result.initial_sync_complete is True

    def test_returns_none_on_null_response(self) -> None:
        """Test returns None when API returns null."""
        mock_client = MagicMock()
        mock_client.get_request.return_value = b"null"

        crawler = _create_crawler(matik_client=mock_client)

        result = crawler._get_tracker()
        assert result is None

    def test_returns_none_on_exception(self) -> None:
        """Test returns None when API call fails."""
        mock_client = MagicMock()
        mock_client.get_request.side_effect = Exception("API error")

        crawler = _create_crawler(matik_client=mock_client)

        result = crawler._get_tracker()
        assert result is None


class TestUpdateTracker:
    """Tests for _update_tracker method."""

    def test_success_update(self) -> None:
        """Test successful tracker update."""
        mock_client = MagicMock()
        crawler = _create_crawler(matik_client=mock_client)
        tracker = _create_tracker()

        result = crawler._update_tracker(tracker, mark_sync_complete=True)

        assert result is True
        assert tracker.status == "OK"
        assert tracker.initial_sync_complete is True
        mock_client.post_json_request.assert_called_once()

    def test_error_update(self) -> None:
        """Test tracker update with error."""
        mock_client = MagicMock()
        crawler = _create_crawler(matik_client=mock_client)
        tracker = _create_tracker()
        cursor_dt = datetime(2024, 1, 10, 0, 0, 0)
        cursor = FetchCursor(updated_at_gte=cursor_dt)
        error = Exception("Test error")

        result = crawler._update_tracker(tracker, cursor=cursor, error=error)

        assert result is True
        assert tracker.status == "ERROR"
        assert tracker.error_message == "Test error"
        assert tracker.last_updated_at_cursor == cursor_dt

    def test_returns_false_on_api_failure(self) -> None:
        """Test returns False when API call fails."""
        mock_client = MagicMock()
        mock_client.post_json_request.side_effect = Exception("API error")
        crawler = _create_crawler(matik_client=mock_client)
        tracker = _create_tracker()

        result = crawler._update_tracker(tracker)

        assert result is False


class TestDispatch:
    """Tests for dispatch method."""

    def test_returns_failure_when_no_tracker(self) -> None:
        """Test returns failure when tracker not found."""
        mock_client = MagicMock()
        mock_client.get_request.return_value = b"null"

        crawler = _create_crawler(matik_client=mock_client)

        result = crawler.dispatch()

        assert result.success is False
        assert result.error_message == "Tracker not found in database"

    def test_returns_failure_when_tracker_in_error_state(self) -> None:
        """Test returns failure when tracker is in ERROR state."""
        mock_client = MagicMock()
        tracker_data = {
            "timestamp": "2024-01-15T10:00:00",
            "status": "ERROR",
            "initial_sync_complete": False,
            "error_message": "Previous failure",
        }
        mock_client.get_request.return_value = json.dumps(tracker_data).encode()

        crawler = _create_crawler(matik_client=mock_client)

        result = crawler.dispatch()

        assert result.success is False
        assert result.error_message is not None
        assert "ERROR state" in result.error_message

    def test_successful_dispatch_with_no_incidents(self) -> None:
        """Test successful dispatch when no incidents to process."""
        mock_matik = MagicMock()
        tracker_data = {
            "timestamp": "2024-01-15T10:00:00",
            "status": "OK",
            "initial_sync_complete": False,
        }
        mock_matik.get_request.return_value = json.dumps(tracker_data).encode()

        mock_io_client = MagicMock()
        # Return tuple: (incidents, raw_count, last_raw_id)
        mock_io_client.list_incidents = AsyncMock(return_value=([], 0, None))

        crawler = _create_crawler(
            matik_client=mock_matik,
            incidentio_client=mock_io_client,
            config=_create_incidentio_config(start_date="2024-01-01"),
        )

        result = crawler.dispatch()

        assert result.success is True
        assert result.records_processed == 0


class TestPublishIncidentsToSqs:
    """Tests for _publish_incidents_to_sqs method."""

    def test_empty_list_returns_zero(self) -> None:
        """Test empty list returns 0."""
        crawler = _create_crawler()

        result = asyncio.run(crawler._publish_incidents_to_sqs([]))
        assert result == 0

    def test_returns_count_on_success(self) -> None:
        """Test returns number of published incidents on success."""
        mock_sqs = MagicMock()
        mock_sqs.send = AsyncMock()

        crawler = _create_crawler(sqs_publisher=mock_sqs)

        items = [_create_incident_with_raw() for _ in range(5)]
        result = asyncio.run(crawler._publish_incidents_to_sqs(items))

        assert result == 5
        assert mock_sqs.send.call_count == 5

    def test_returns_none_on_exception(self) -> None:
        """Test returns None on exception."""
        mock_sqs = MagicMock()
        mock_sqs.send = AsyncMock(side_effect=Exception("SQS error"))

        crawler = _create_crawler(sqs_publisher=mock_sqs)

        items = [_create_incident_with_raw()]
        result = asyncio.run(crawler._publish_incidents_to_sqs(items))

        assert result is None

    def test_message_stamped_with_entered_at(self) -> None:
        """DLQ retry staleness guard (ADR 024): Historian, as a true origin of
        the base fact, stamps entered_at on every published message."""
        mock_sqs = MagicMock()
        mock_sqs.send = AsyncMock()

        crawler = _create_crawler(sqs_publisher=mock_sqs)

        asyncio.run(crawler._publish_incidents_to_sqs([_create_incident_with_raw()]))

        published_message = mock_sqs.send.call_args[0][0]
        assert published_message.entered_at is not None


class TestProducer:
    """Tests for _producer method."""

    def test_producer_fetches_and_queues_incidents(self) -> None:
        """Test producer fetches incidents and puts them in queue."""
        mock_io_client = MagicMock()
        incident = _create_incident_with_raw("INC-1")
        # Return one incident on first page, empty on second
        mock_io_client.list_incidents = AsyncMock(
            side_effect=[([incident], 1, "INC-1"), ([], 0, None)]
        )

        crawler = _create_crawler(incidentio_client=mock_io_client)

        queue: asyncio.Queue[IncidentWithRawFields | None] = asyncio.Queue(maxsize=100)

        total = asyncio.run(crawler._producer(queue, FetchCursor()))

        assert total == 1
        # Should have incident + None (done signal)
        assert queue.qsize() == 2

    def test_producer_handles_multiple_pages(self) -> None:
        """Test producer handles pagination across multiple pages."""
        mock_io_client = MagicMock()
        config = _create_incidentio_config()
        config.page_size = 2  # Force pagination with small page size

        incidents_page1 = [
            _create_incident_with_raw("INC-1"),
            _create_incident_with_raw("INC-2"),
        ]
        incidents_page2 = [_create_incident_with_raw("INC-3")]

        # Return full page, then partial page (indicates last page)
        mock_io_client.list_incidents = AsyncMock(
            side_effect=[
                (incidents_page1, 2, "INC-2"),
                (incidents_page2, 1, "INC-3"),
            ]
        )

        crawler = _create_crawler(incidentio_client=mock_io_client, config=config)

        queue: asyncio.Queue[IncidentWithRawFields | None] = asyncio.Queue(maxsize=100)

        total = asyncio.run(crawler._producer(queue, FetchCursor()))

        assert total == 3
        # 3 incidents + None signal
        assert queue.qsize() == 4


class TestConsumer:
    """Tests for _consumer method."""

    def test_consumer_timeout_sets_failure(self) -> None:
        """Test consumer timeout returns failure."""
        config = IncidentIOConfig(
            api_key="test-key",
            consumer_timeout_seconds=1,  # 1 second timeout
            root_cause_prompt="Test prompt",
            description_prompt="Test description prompt",
        )
        crawler = _create_crawler(config=config)

        queue: asyncio.Queue[IncidentWithRawFields | None] = asyncio.Queue(maxsize=100)
        # Don't put anything in queue - will timeout

        success, total = asyncio.run(crawler._consumer(queue))

        assert success is False
        assert total == 0

    def test_consumer_timeout_writes_partial_batch(self) -> None:
        """Test consumer writes partial batch on timeout."""
        mock_sqs = MagicMock()
        mock_sqs.send = AsyncMock()

        config = IncidentIOConfig(
            api_key="test-key",
            consumer_timeout_seconds=1,  # 1 second timeout
            write_batch_size=10,  # Large batch size so it doesn't auto-flush
            root_cause_prompt="Test prompt",
            description_prompt="Test description prompt",
        )
        crawler = _create_crawler(sqs_publisher=mock_sqs, config=config)

        queue: asyncio.Queue[IncidentWithRawFields | None] = asyncio.Queue(maxsize=100)
        # Add one item but no None signal
        queue.put_nowait(_create_incident_with_raw())

        success, total = asyncio.run(crawler._consumer(queue))

        assert success is True  # Batch was successfully written despite timeout
        assert total == 1  # Partial batch was written
        mock_sqs.send.assert_called_once()

    def test_consumer_writes_full_batches(self) -> None:
        """Test consumer writes batches when full."""
        mock_sqs = MagicMock()
        mock_sqs.send = AsyncMock()

        config = IncidentIOConfig(
            api_key="test-key",
            write_batch_size=2,  # Small batch size
            consumer_timeout_seconds=5,
            root_cause_prompt="Test prompt",
            description_prompt="Test description prompt",
        )
        crawler = _create_crawler(sqs_publisher=mock_sqs, config=config)

        queue: asyncio.Queue[IncidentWithRawFields | None] = asyncio.Queue(maxsize=100)
        queue.put_nowait(_create_incident_with_raw("INC-1"))
        queue.put_nowait(_create_incident_with_raw("INC-2"))
        queue.put_nowait(_create_incident_with_raw("INC-3"))
        queue.put_nowait(None)  # Done signal

        success, total = asyncio.run(crawler._consumer(queue))

        assert success is True
        assert total == 3
        # Should have sent 3 SQS messages (one per incident)
        assert mock_sqs.send.call_count == 3

    def test_consumer_failure_on_upsert_error(self) -> None:
        """Test consumer returns failure when SQS publish fails."""
        mock_sqs = MagicMock()
        mock_sqs.send = AsyncMock(side_effect=Exception("SQS error"))

        config = IncidentIOConfig(
            api_key="test-key",
            write_batch_size=2,
            consumer_timeout_seconds=5,
            root_cause_prompt="Test prompt",
            description_prompt="Test description prompt",
        )
        crawler = _create_crawler(sqs_publisher=mock_sqs, config=config)

        queue: asyncio.Queue[IncidentWithRawFields | None] = asyncio.Queue(maxsize=100)
        queue.put_nowait(_create_incident_with_raw("INC-1"))
        queue.put_nowait(_create_incident_with_raw("INC-2"))
        queue.put_nowait(None)

        success, total = asyncio.run(crawler._consumer(queue))

        assert success is False
        assert total == 2


class TestDispatchAsync:
    """Tests for _dispatch_async method."""

    def test_dispatch_async_consumer_failure(self) -> None:
        """Test dispatch_async handles consumer failure."""
        mock_matik = MagicMock()
        tracker_data = {
            "timestamp": "2024-01-15T10:00:00",
            "status": "OK",
            "initial_sync_complete": False,
        }
        mock_matik.get_request.return_value = json.dumps(tracker_data).encode()

        mock_io_client = MagicMock()
        mock_io_client.list_incidents = AsyncMock(
            return_value=(
                [_create_incident_with_raw()],
                1,
                "INC-123",
            )
        )

        mock_sqs_publisher = MagicMock()
        mock_sqs_publisher.send = AsyncMock(side_effect=Exception("SQS error"))

        config = IncidentIOConfig(
            api_key="test-key",
            write_batch_size=10,
            consumer_timeout_seconds=5,
            page_size=100,
            root_cause_prompt="Test prompt",
            description_prompt="Test description prompt",
        )
        crawler = _create_crawler(
            matik_client=mock_matik,
            incidentio_client=mock_io_client,
            config=config,
            sqs_publisher=mock_sqs_publisher,
        )

        result = crawler.dispatch()

        assert result.success is False
        assert "Failed to upsert" in (result.error_message or "")

    def test_dispatch_async_exception_during_processing(self) -> None:
        """Test dispatch_async handles exceptions during processing."""
        mock_matik = MagicMock()
        tracker_data = {
            "timestamp": "2024-01-15T10:00:00",
            "status": "OK",
            "initial_sync_complete": False,
        }
        mock_matik.get_request.return_value = json.dumps(tracker_data).encode()

        mock_io_client = MagicMock()
        mock_io_client.list_incidents = AsyncMock(
            side_effect=Exception("API connection error")
        )

        crawler = _create_crawler(
            matik_client=mock_matik,
            incidentio_client=mock_io_client,
            config=_create_incidentio_config(start_date="2024-01-01"),
        )

        result = crawler.dispatch()

        assert result.success is False
        assert "Error in async dispatch" in (result.error_message or "")

    def test_dispatch_marks_initial_sync_complete(self) -> None:
        """Test dispatch marks initial_sync_complete on first successful run."""
        mock_matik = MagicMock()
        tracker_data = {
            "timestamp": "2024-01-15T10:00:00",
            "status": "OK",
            "initial_sync_complete": False,
        }
        mock_matik.get_request.return_value = json.dumps(tracker_data).encode()
        mock_matik.post_json_request.return_value = json.dumps(
            {"affected_rows": 1}
        ).encode()

        mock_io_client = MagicMock()
        mock_io_client.list_incidents = AsyncMock(return_value=([], 0, None))

        crawler = _create_crawler(
            matik_client=mock_matik,
            incidentio_client=mock_io_client,
            config=_create_incidentio_config(start_date="2024-01-01"),
        )

        result = crawler.dispatch()

        assert result.success is True
        # Check that tracker was updated with initial_sync_complete=True
        calls = mock_matik.post_json_request.call_args_list
        # Last call should be tracker update
        tracker_call = calls[-1]
        assert tracker_call[0][0] == "/v1/incidentio/incident/tracker/lastrecorded"

    def test_dispatch_with_records_processed(self) -> None:
        """Test dispatch processes incidents and logs success."""
        mock_matik = MagicMock()
        tracker_data = {
            "timestamp": "2024-01-15T10:00:00",
            "status": "OK",
            "initial_sync_complete": True,
        }
        mock_matik.get_request.return_value = json.dumps(tracker_data).encode()
        mock_matik.post_json_request.return_value = json.dumps({}).encode()

        mock_sqs = MagicMock()
        mock_sqs.send = AsyncMock()

        mock_io_client = MagicMock()
        incidents = [
            _create_incident_with_raw("INC-1"),
            _create_incident_with_raw("INC-2"),
        ]
        mock_io_client.list_incidents = AsyncMock(return_value=(incidents, 2, "INC-2"))

        crawler = _create_crawler(
            matik_client=mock_matik,
            incidentio_client=mock_io_client,
            sqs_publisher=mock_sqs,
        )

        result = crawler.dispatch()

        assert result.success is True
        assert result.records_processed == 2

    def test_dispatch_async_consumer_timeout_cancels_producer(self) -> None:
        """Test that consumer timeout triggers producer cancellation (deadlock prevention)."""
        mock_matik = MagicMock()
        tracker_data = {
            "timestamp": "2024-01-15T10:00:00",
            "status": "OK",
            "initial_sync_complete": False,
        }
        mock_matik.get_request.return_value = json.dumps(tracker_data).encode()

        # Make producer slow by having it wait before returning
        async def slow_list_incidents(
            **kwargs: object,
        ) -> tuple[list[IncidentWithRawFields], int, str | None]:
            await asyncio.sleep(10)  # Simulate slow API
            return ([], 0, None)

        mock_io_client = MagicMock()
        mock_io_client.list_incidents = slow_list_incidents

        config = IncidentIOConfig(
            api_key="test-key",
            write_batch_size=10,
            consumer_timeout_seconds=1,  # Short timeout to trigger consumer exit
            page_size=100,
            root_cause_prompt="Test prompt",
            description_prompt="Test description prompt",
        )
        crawler = _create_crawler(
            matik_client=mock_matik,
            incidentio_client=mock_io_client,
            config=config,
        )

        result = crawler.dispatch()

        # Consumer times out, producer gets cancelled, result is failure
        assert result.success is False


class TestPublishEnrichmentMessages:
    """Tests for _publish_enrichment_messages method."""

    def _create_crawler_with_publisher(
        self,
        matik_client: MagicMock | None = None,
        publisher: MagicMock | None = None,
    ) -> IncidentIOIncidentCrawler:
        from common.queues.enrichment_publisher import EnrichmentPublisher

        return IncidentIOIncidentCrawler(
            incidentio_config=_create_incidentio_config(),
            matik_client=matik_client or MagicMock(),
            incidentio_client=MagicMock(),
            enrichment_publisher=publisher or MagicMock(spec=EnrichmentPublisher),
        )

    def test_publishes_item(self) -> None:
        """Test publishes an item to SQS."""
        mock_publisher = MagicMock()
        mock_publisher.publish = AsyncMock()

        crawler = self._create_crawler_with_publisher(publisher=mock_publisher)

        item = _create_incident_with_raw(summary="Test summary")
        asyncio.run(crawler._publish_enrichment_messages([item]))

        mock_publisher.publish.assert_called_once()

    def test_publishes_all_items_unconditionally(self) -> None:
        """Test publishes every item regardless of existing hash state.

        Hash-based dedup is the enricher's responsibility, not the historian's.
        """
        mock_publisher = MagicMock()
        mock_publisher.publish = AsyncMock()

        crawler = self._create_crawler_with_publisher(publisher=mock_publisher)

        items = [
            _create_incident_with_raw(summary="First incident"),
            _create_incident_with_raw(summary="Second incident"),
        ]
        asyncio.run(crawler._publish_enrichment_messages(items))

        assert mock_publisher.publish.call_count == 2

    def test_message_stamped_with_entered_at(self) -> None:
        """DLQ retry staleness guard (ADR 024): Historian stamps entered_at on
        every published EnrichmentRequest — the Enricher forwards it
        unchanged rather than regenerating it."""
        mock_publisher = MagicMock()
        mock_publisher.publish = AsyncMock()

        crawler = self._create_crawler_with_publisher(publisher=mock_publisher)
        item = _create_incident_with_raw(summary="Test summary")
        asyncio.run(crawler._publish_enrichment_messages([item]))

        published_message = mock_publisher.publish.call_args[0][0]
        assert published_message.entered_at is not None

    def test_content_contains_only_raw_fields(self) -> None:
        """Test that published content has raw input fields only, no pre-computed hashes."""
        mock_publisher = MagicMock()
        mock_publisher.publish = AsyncMock()

        crawler = self._create_crawler_with_publisher(publisher=mock_publisher)
        item = _create_incident_with_raw(
            summary="Test summary", resolution_statement="Test resolution"
        )
        asyncio.run(crawler._publish_enrichment_messages([item]))

        mock_publisher.publish.assert_called_once()
        call_args = mock_publisher.publish.call_args[0][0]
        assert set(call_args.content.keys()) == {
            "name",
            "summary",
            "resolution_statement",
            "reference_id",
        }
        assert "root_cause_summary_hash" not in call_args.content
        assert "description_hash" not in call_args.content

    def test_returns_early_when_publisher_is_none(self) -> None:
        """Test returns early (no-op) when enrichment_publisher is None."""
        crawler = _create_crawler()  # No enrichment_publisher

        item = _create_incident_with_raw(summary="Test")
        # Should return without error even though publisher is None
        asyncio.run(crawler._publish_enrichment_messages([item]))

    def test_publish_error_is_swallowed_and_recorded(self) -> None:
        """Test that SQS publish failures are caught, logged, and recorded as job errors."""
        mock_publisher = MagicMock()
        mock_publisher.publish = AsyncMock(side_effect=Exception("SQS unavailable"))
        mock_job_metrics = MagicMock()

        crawler = IncidentIOIncidentCrawler(
            incidentio_config=_create_incidentio_config(),
            matik_client=MagicMock(),
            incidentio_client=MagicMock(),
            enrichment_publisher=mock_publisher,
            job_metrics=mock_job_metrics,
        )

        item = _create_incident_with_raw(summary="Test")
        # Should not raise
        asyncio.run(crawler._publish_enrichment_messages([item]))

        mock_job_metrics.record_job_error.assert_called_once_with(
            "incidentio_incidents", "enrichment_publish_error"
        )


class TestProcessBatchWithPublisher:
    """Tests for _process_batch when enrichment_publisher is configured."""

    def test_process_batch_calls_publisher_instead_of_facade(self) -> None:
        """Test _process_batch uses publisher path when enrichment_publisher is set."""
        from common.queues.enrichment_publisher import EnrichmentPublisher

        mock_matik = MagicMock()
        mock_matik.post_json_request.return_value = json.dumps(
            {"affected_rows": 1, "INC-123": None}
        ).encode()

        mock_publisher = MagicMock(spec=EnrichmentPublisher)
        mock_publisher.publish = AsyncMock()

        crawler = IncidentIOIncidentCrawler(
            incidentio_config=_create_incidentio_config(),
            matik_client=mock_matik,
            incidentio_client=MagicMock(),
            enrichment_publisher=mock_publisher,
        )

        batch = [_create_incident_with_raw()]
        result = asyncio.run(crawler._process_batch(batch, 1))

        assert result is True
        mock_publisher.publish.assert_called_once()


class TestConsumerTimeoutWithBatchFailure:
    """Tests for _consumer timeout where batch write fails (line 527)."""

    def test_consumer_timeout_with_failing_batch_returns_failure(self) -> None:
        """Test consumer returns failure when partial batch fails to write on timeout."""
        mock_sqs_publisher = AsyncMock()
        mock_sqs_publisher.send.side_effect = Exception("SQS write error")

        config = IncidentIOConfig(
            api_key="test-key",
            consumer_timeout_seconds=1,
            write_batch_size=10,
            root_cause_prompt="Test prompt",
            description_prompt="Test description prompt",
        )
        crawler = _create_crawler(sqs_publisher=mock_sqs_publisher, config=config)

        queue: asyncio.Queue[IncidentWithRawFields | None] = asyncio.Queue(maxsize=100)
        queue.put_nowait(_create_incident_with_raw())
        # No None signal - will timeout with a partial batch that fails to write

        success, total = asyncio.run(crawler._consumer(queue))

        assert success is False
        assert total == 1


class TestPublishIncidentsToSqsWithJobMetrics:
    """Tests for _publish_incidents_to_sqs job_metrics on error."""

    def test_records_sqs_publish_error_metric_on_exception(self) -> None:
        """Test that SQS publish failure records sqs_publish_error metric when job_metrics set."""
        mock_sqs = MagicMock()
        mock_sqs.send = AsyncMock(side_effect=Exception("SQS error"))
        mock_job_metrics = MagicMock()

        crawler = IncidentIOIncidentCrawler(
            incidentio_config=_create_incidentio_config(),
            matik_client=MagicMock(),
            incidentio_client=MagicMock(),
            job_metrics=mock_job_metrics,
            sqs_publisher=mock_sqs,
        )

        items = [_create_incident_with_raw()]
        result = asyncio.run(crawler._publish_incidents_to_sqs(items))

        assert result is None
        mock_job_metrics.record_job_error.assert_called_with(
            "incidentio_incidents", "sqs_publish_error"
        )
