"""Tests for Jira Historian cron job."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.clients.sqs_publisher import SQSPublisher
from common.models.jira_batch_tracker import JiraBatchTracker
from common.models.jira_issue import Issue, IssueFields, Status
from common.models.jira_issue_type import JiraIssueType

from .main import (
    BatchWindowError,
    JQLBuildError,
    _enrich_with_services,
    _save_issues_via_sqs,
    _set_tracker_error,
    _update_batch_tracker,
    build_final_jql,
    calculate_batch_window,
    convert_issue_to_record,
    main,
    process_ticket_type,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def base_tracker() -> JiraBatchTracker:
    """Create a base tracker for testing."""
    return JiraBatchTracker(
        ticket_type="tcmr",
        batch_start=datetime(2024, 1, 1, 0, 0, 0),
        batch_end=datetime(2024, 1, 15, 0, 0, 0),
        window_days=14,
        status=None,
        error_message=None,
        last_processed_at=None,
        updated_at=datetime(2024, 1, 1, 0, 0, 0),
    )


@pytest.fixture
def mock_sqs_publisher() -> MagicMock:
    """Create a mock SQSPublisher."""
    publisher = MagicMock(spec=SQSPublisher)
    publisher.send = AsyncMock()
    publisher.send_sync = MagicMock()
    return publisher


@pytest.fixture
def sample_issue() -> Issue:
    """Create a sample Issue for testing."""
    return Issue(
        id="12345",
        self="https://jira.example.com/rest/api/2/issue/12345",
        key="TCMR-123",
        fields=IssueFields(
            summary="Test issue summary",
            status=Status(
                self="https://jira.example.com/rest/api/2/status/1",
                id="1",
                name="Open",
            ),
            created="2024-01-15T10:00:00.000+0000",
        ),
        issue_summary="LLM generated summary",
        issue_comments_summary="LLM generated comments summary",
        summary_hash="abc123",
        comments_hash="def456",
        tcmr_planned_start_date="2024-01-14T16:00:00.000+0000",
        tcmr_planned_end_date="2024-01-14T20:00:00.000+0000",
    )


# =============================================================================
# Test calculate_batch_window
# =============================================================================


class TestCalculateBatchWindow:
    """Tests for calculate_batch_window function."""

    def test_initial_batch_null_status(self, base_tracker: JiraBatchTracker) -> None:
        """Test initial batch when status is NULL."""
        base_tracker.status = None

        result = calculate_batch_window(base_tracker, lookback_days=30)

        assert result.log_type == "initial"
        assert result.start == "2024-01-01T00:00:00"
        assert result.end == "2024-01-15T00:00:00"

    def test_advance_batch_ok_status(self, base_tracker: JiraBatchTracker) -> None:
        """Test advancing batch when status is OK and not caught up."""
        base_tracker.status = "OK"
        # Set batch_end far in the past so we're not caught up
        base_tracker.batch_end = datetime(2024, 1, 1, 0, 0, 0)

        with patch("historian.jira.main.utc_now_naive") as mock_now:
            mock_now.return_value = datetime(2024, 6, 1, 0, 0, 0)
            result = calculate_batch_window(base_tracker, lookback_days=30)

        assert result.log_type == "advance"
        assert result.start == "2024-01-01T00:00:00"
        assert result.end == "2024-01-15T00:00:00"

    def test_catch_up_when_next_window_overshoots_now(
        self, base_tracker: JiraBatchTracker
    ) -> None:
        """Test catch-up when next window overshoots now and gap >= 10 minutes."""
        base_tracker.status = "OK"
        # batch_end is before now, but batch_end + window_days > now
        base_tracker.batch_end = datetime(2024, 5, 25, 0, 0, 0)

        with patch("historian.jira.main.utc_now_naive") as mock_now:
            mock_now.return_value = datetime(2024, 6, 1, 0, 0, 0)
            result = calculate_batch_window(base_tracker, lookback_days=30)

        assert result.log_type == "catch_up"
        assert result.start == "2024-05-25T00:00:00"
        assert result.end == "2024-06-01T00:00:00"

    def test_lookback_when_gap_is_sub_10_minutes(
        self, base_tracker: JiraBatchTracker
    ) -> None:
        """Test lookback when gap is sub-10-minute (cron timing variance)."""
        base_tracker.status = "OK"
        base_tracker.batch_end = datetime(2024, 6, 1, 0, 5, 0)

        with patch("historian.jira.main.utc_now_naive") as mock_now:
            mock_now.return_value = datetime(2024, 6, 1, 0, 9, 0)  # 4 min later
            result = calculate_batch_window(base_tracker, lookback_days=30)

        assert result.log_type == "lookback"
        assert result.start == datetime(2024, 5, 2, 0, 9, 0).isoformat()
        assert result.end == datetime(2024, 5, 16, 0, 9, 0).isoformat()  # start + 14d

    def test_lookback_batch_caught_up(self, base_tracker: JiraBatchTracker) -> None:
        """Test lookback reset when batch_end is already at or past now."""
        base_tracker.status = "OK"
        # batch_end is at now — already fully caught up
        base_tracker.batch_end = datetime(2024, 6, 1, 0, 0, 0)

        with patch("historian.jira.main.utc_now_naive") as mock_now:
            mock_now.return_value = datetime(2024, 6, 1, 0, 0, 0)
            result = calculate_batch_window(base_tracker, lookback_days=30)

        assert result.log_type == "lookback"
        # Should reset to lookback_days back from now, advancing one window_days chunk
        assert result.start == datetime(2024, 5, 2, 0, 0, 0).isoformat()
        assert result.end == datetime(2024, 5, 16, 0, 0, 0).isoformat()  # start + 14d

    def test_resume_batch_processing_status(
        self, base_tracker: JiraBatchTracker
    ) -> None:
        """Test resuming batch when status is PROCESSING."""
        base_tracker.status = "PROCESSING"

        result = calculate_batch_window(base_tracker, lookback_days=30)

        assert result.log_type == "resume"
        assert result.start == "2024-01-01T00:00:00"
        assert result.end == "2024-01-15T00:00:00"

    def test_unknown_status_raises_error(self, base_tracker: JiraBatchTracker) -> None:
        """Test that unknown status raises BatchWindowError."""
        base_tracker.status = "UNKNOWN"

        with pytest.raises(BatchWindowError) as exc_info:
            calculate_batch_window(base_tracker, lookback_days=30)

        assert "unknown tracker status" in str(exc_info.value)

    def test_default_lookback_days(self, base_tracker: JiraBatchTracker) -> None:
        """Test that invalid lookback_days defaults to 30."""
        base_tracker.status = "OK"
        # batch_end at now so we go straight to lookback
        base_tracker.batch_end = datetime(2024, 6, 1, 0, 0, 0)

        with patch("historian.jira.main.utc_now_naive") as mock_now:
            mock_now.return_value = datetime(2024, 6, 1, 0, 0, 0)
            result = calculate_batch_window(base_tracker, lookback_days=0)

        assert result.log_type == "lookback"
        # Should use default 30 days
        expected_start = datetime(2024, 5, 2, 0, 0, 0)
        assert result.start == expected_start.isoformat()


# =============================================================================
# Test build_final_jql
# =============================================================================


class TestBuildFinalJQL:
    """Tests for build_final_jql function."""

    def test_successful_replacement(self) -> None:
        """Test successful JQL placeholder replacement."""
        jql = "project = TCMR AND [REPLACE BATCH DATES] ORDER BY created DESC"

        result = build_final_jql(
            jql,
            batch_start="2024-01-01T00:00:00",
            batch_end="2024-01-15T00:00:00",
        )

        assert "created > '2024/01/01 00:00'" in result
        assert "created < '2024/01/15 00:00'" in result
        assert "[REPLACE BATCH DATES]" not in result

    def test_empty_jql_raises_error(self) -> None:
        """Test that empty JQL raises JQLBuildError."""
        with pytest.raises(JQLBuildError) as exc_info:
            build_final_jql("", "2024-01-01T00:00:00", "2024-01-15T00:00:00")

        assert "base JQL is empty" in str(exc_info.value)

    def test_missing_placeholder_raises_error(self) -> None:
        """Test that missing placeholder raises JQLBuildError."""
        jql = "project = TCMR ORDER BY created DESC"

        with pytest.raises(JQLBuildError) as exc_info:
            build_final_jql(jql, "2024-01-01T00:00:00", "2024-01-15T00:00:00")

        assert "does not contain [REPLACE BATCH DATES]" in str(exc_info.value)

    def test_none_dates_raises_error(self) -> None:
        """Test that None dates raise JQLBuildError."""
        jql = "project = TCMR AND [REPLACE BATCH DATES] ORDER BY created DESC"

        with pytest.raises(JQLBuildError) as exc_info:
            build_final_jql(jql, None, None)  # type: ignore[arg-type]

        assert "failed to parse batch dates" in str(exc_info.value)


# =============================================================================
# Test convert_issue_to_record
# =============================================================================


class TestConvertIssueToRecord:
    """Tests for convert_issue_to_record function."""

    def test_full_conversion(self, sample_issue: Issue) -> None:
        """Test full issue conversion with all fields."""
        record = convert_issue_to_record(sample_issue, "tcmr")

        assert record.issue_id == "12345"
        assert record.issue_key == "TCMR-123"
        assert record.ticket_type == "tcmr"
        assert record.summary == "Test issue summary"
        assert record.status_name == "Open"
        assert record.issue_summary == "LLM generated summary"
        assert record.issue_comments_summary == "LLM generated comments summary"
        assert record.summary_hash == "abc123"
        assert record.comments_hash == "def456"
        assert record.tcmr_planned_start_date == datetime(2024, 1, 14, 16, 0, 0)
        assert record.tcmr_planned_end_date == datetime(2024, 1, 14, 20, 0, 0)

    def test_minimal_issue(self) -> None:
        """Test conversion with minimal issue data."""
        issue = Issue(
            id="67890",
            key="OPS-456",
        )

        record = convert_issue_to_record(issue, "operational")

        assert record.issue_id == "67890"
        assert record.issue_key == "OPS-456"
        assert record.ticket_type == "operational"
        assert record.summary is None
        assert record.status_name is None
        assert record.issue_summary is None
        assert record.issue_comments_summary is None

    def test_none_fields(self) -> None:
        """Test conversion handles None fields gracefully."""
        issue = Issue(
            id=None,
            key=None,
            fields=None,
        )

        record = convert_issue_to_record(issue, "tcmr")

        assert record.issue_id == ""
        assert record.issue_key == ""
        assert record.summary is None


# =============================================================================
# Test Helper Functions
# =============================================================================


class TestUpdateBatchTracker:
    """Tests for _update_batch_tracker function."""

    def test_update_tracker_success(self, base_tracker: JiraBatchTracker) -> None:
        """Test successful tracker update."""
        mock_api_client = MagicMock()
        mock_api_client.post_json_request.return_value = b'{"message": "ok"}'

        result = _update_batch_tracker(mock_api_client, base_tracker)

        assert result is True
        mock_api_client.post_json_request.assert_called_once()

    def test_update_tracker_api_error(self, base_tracker: JiraBatchTracker) -> None:
        """Test tracker update when API call fails."""
        mock_api_client = MagicMock()
        mock_api_client.post_json_request.side_effect = Exception("API error")

        result = _update_batch_tracker(mock_api_client, base_tracker)

        assert result is False


class TestSaveIssuesViaSqs:
    """Tests for _save_issues_via_sqs function."""

    def test_save_issues_empty_list(self, mock_sqs_publisher: MagicMock) -> None:
        """Test saving empty list of issues."""
        mock_api_client = MagicMock()

        result = _save_issues_via_sqs(mock_sqs_publisher, mock_api_client, [], "tcmr")

        assert result == 0
        mock_sqs_publisher.send_sync.assert_not_called()

    def test_save_issues_success(
        self, mock_sqs_publisher: MagicMock, sample_issue: Issue
    ) -> None:
        """Test successful issue queuing via SQS."""
        mock_api_client = MagicMock()

        result = _save_issues_via_sqs(
            mock_sqs_publisher, mock_api_client, [sample_issue], "tcmr"
        )

        assert result == 1
        mock_sqs_publisher.send_sync.assert_called_once()

    def test_save_issues_sqs_error_returns_zero(
        self, mock_sqs_publisher: MagicMock, sample_issue: Issue
    ) -> None:
        """Test that SQS errors are caught and return 0."""
        mock_api_client = MagicMock()
        mock_sqs_publisher.send_sync.side_effect = RuntimeError("SQS error")

        result = _save_issues_via_sqs(
            mock_sqs_publisher, mock_api_client, [sample_issue], "tcmr"
        )

        assert result == 0

    def test_save_issues_sets_update_services_false_without_mapping(
        self, mock_sqs_publisher: MagicMock, sample_issue: Issue
    ) -> None:
        """Without backstage_mapping, messages are sent with update_services=False."""
        import json

        mock_api_client = MagicMock()

        _save_issues_via_sqs(
            mock_sqs_publisher, mock_api_client, [sample_issue], "operational"
        )

        body = json.loads(
            mock_sqs_publisher.send_sync.call_args[0][0].model_dump_json()
        )
        assert body["update_services"] is False

    def test_save_issues_sets_update_services_true_with_mapping(
        self, mock_sqs_publisher: MagicMock, sample_issue: Issue
    ) -> None:
        """With backstage_mapping, messages are sent with update_services=True."""
        import json

        mock_api_client = MagicMock()
        mock_api_client.post_json_request.return_value = b"{}"

        _save_issues_via_sqs(
            mock_sqs_publisher,
            mock_api_client,
            [sample_issue],
            "tcmr",
            backstage_mapping={"AWS S3": ["svc-a"]},
        )

        body = json.loads(
            mock_sqs_publisher.send_sync.call_args[0][0].model_dump_json()
        )
        assert body["update_services"] is True

    def test_save_issues_stamps_entered_at(
        self, mock_sqs_publisher: MagicMock, sample_issue: Issue
    ) -> None:
        """DLQ retry staleness guard (ADR 024): Historian stamps entered_at on
        every published base message."""
        mock_api_client = MagicMock()

        _save_issues_via_sqs(
            mock_sqs_publisher, mock_api_client, [sample_issue], "tcmr"
        )

        published_message = mock_sqs_publisher.send_sync.call_args[0][0]
        assert published_message.entered_at is not None


class TestSetTrackerError:
    """Tests for _set_tracker_error function."""

    def test_set_tracker_error_success(self, base_tracker: JiraBatchTracker) -> None:
        """Test setting tracker to error state."""
        mock_api_client = MagicMock()
        mock_api_client.post_json_request.return_value = b'{"message": "ok"}'

        _set_tracker_error(mock_api_client, base_tracker, "Test error")

        assert base_tracker.status == "ERROR"
        assert base_tracker.error_message == "Test error"
        mock_api_client.post_json_request.assert_called_once()

    def test_set_tracker_error_api_failure(
        self, base_tracker: JiraBatchTracker
    ) -> None:
        """Test setting tracker error when API call fails."""
        mock_api_client = MagicMock()
        mock_api_client.post_json_request.side_effect = Exception("API error")

        # Should not raise, just log the error
        _set_tracker_error(mock_api_client, base_tracker, "Test error")

        assert base_tracker.status == "ERROR"
        assert base_tracker.error_message == "Test error"


# =============================================================================
# Test process_ticket_type
# =============================================================================


class TestProcessTicketType:
    """Tests for process_ticket_type function."""

    @pytest.fixture
    def mock_jira_config(self) -> MagicMock:
        """Create mock JIRA config."""
        config = MagicMock()
        config.lookback_days = 30
        return config

    @pytest.fixture
    def mock_jira_client(self) -> MagicMock:
        """Create mock JIRA client."""
        return MagicMock()

    @pytest.fixture
    def mock_api_client(self) -> MagicMock:
        """Create mock Matik API client."""
        return MagicMock()

    def test_process_success(
        self,
        mock_jira_config: MagicMock,
        mock_jira_client: MagicMock,
        mock_api_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        base_tracker: JiraBatchTracker,
        sample_issue: Issue,
    ) -> None:
        """Test successful batch processing."""
        import json

        mock_api_client.get_request.return_value = json.dumps(
            base_tracker.model_dump(mode="json")
        ).encode()
        mock_api_client.post_json_request.return_value = b'{"message": "ok"}'
        mock_jira_client.fetch_issues.return_value = [{"key": "TCMR-123"}]
        mock_jira_client.enrich_issues.return_value = [sample_issue]

        jql = "project = TCMR AND [REPLACE BATCH DATES]"
        result = process_ticket_type(
            mock_jira_config,
            mock_jira_client,
            mock_api_client,
            JiraIssueType.TCMR,
            jql,
            sqs_publisher=mock_sqs_publisher,
        )

        assert result is True
        # API posts: PROCESSING tracker, OK tracker (issue goes to SQS)
        assert mock_api_client.post_json_request.call_count == 2
        mock_sqs_publisher.send_sync.assert_called_once()

    def test_process_tracker_not_found(
        self,
        mock_jira_config: MagicMock,
        mock_jira_client: MagicMock,
        mock_api_client: MagicMock,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test processing when tracker is not found."""
        mock_api_client.get_request.side_effect = Exception("404 Not Found")

        jql = "project = TCMR AND [REPLACE BATCH DATES]"
        result = process_ticket_type(
            mock_jira_config,
            mock_jira_client,
            mock_api_client,
            JiraIssueType.TCMR,
            jql,
            sqs_publisher=mock_sqs_publisher,
        )

        assert result is False

    def test_process_tracker_in_error_state(
        self,
        mock_jira_config: MagicMock,
        mock_jira_client: MagicMock,
        mock_api_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        base_tracker: JiraBatchTracker,
    ) -> None:
        """Test processing when tracker is in ERROR state."""
        import json

        base_tracker.status = "ERROR"
        base_tracker.error_message = "Previous run failed"
        mock_api_client.get_request.return_value = json.dumps(
            base_tracker.model_dump(mode="json")
        ).encode()

        jql = "project = TCMR AND [REPLACE BATCH DATES]"
        result = process_ticket_type(
            mock_jira_config,
            mock_jira_client,
            mock_api_client,
            JiraIssueType.TCMR,
            jql,
            sqs_publisher=mock_sqs_publisher,
        )

        assert result is False

    def test_process_jira_fetch_error(
        self,
        mock_jira_config: MagicMock,
        mock_jira_client: MagicMock,
        mock_api_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        base_tracker: JiraBatchTracker,
    ) -> None:
        """Test processing when JIRA fetch fails."""
        import json

        mock_api_client.get_request.return_value = json.dumps(
            base_tracker.model_dump(mode="json")
        ).encode()
        mock_api_client.post_json_request.return_value = b'{"message": "ok"}'
        mock_jira_client.fetch_issues.side_effect = Exception("API error")

        jql = "project = TCMR AND [REPLACE BATCH DATES]"
        result = process_ticket_type(
            mock_jira_config,
            mock_jira_client,
            mock_api_client,
            JiraIssueType.TCMR,
            jql,
            sqs_publisher=mock_sqs_publisher,
        )

        assert result is False

    def test_process_save_issues_error(
        self,
        mock_jira_config: MagicMock,
        mock_jira_client: MagicMock,
        mock_api_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        base_tracker: JiraBatchTracker,
        sample_issue: Issue,
    ) -> None:
        """Test processing when sending issues to SQS fails."""
        import json

        mock_api_client.get_request.return_value = json.dumps(
            base_tracker.model_dump(mode="json")
        ).encode()
        mock_api_client.post_json_request.return_value = b'{"message": "ok"}'
        mock_jira_client.fetch_issues.return_value = [{"key": "TCMR-123"}]
        mock_jira_client.enrich_issues.return_value = [sample_issue]
        # SQS send fails → _save_issues_via_sqs returns 0 → RuntimeError raised
        mock_sqs_publisher.send_sync.side_effect = RuntimeError("SQS error")

        jql = "project = TCMR AND [REPLACE BATCH DATES]"
        result = process_ticket_type(
            mock_jira_config,
            mock_jira_client,
            mock_api_client,
            JiraIssueType.TCMR,
            jql,
            sqs_publisher=mock_sqs_publisher,
        )

        assert result is False

    def test_process_batch_window_error(
        self,
        mock_jira_config: MagicMock,
        mock_jira_client: MagicMock,
        mock_api_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        base_tracker: JiraBatchTracker,
    ) -> None:
        """Test processing when batch window calculation fails."""
        import json

        base_tracker.status = "UNKNOWN_STATUS"
        mock_api_client.get_request.return_value = json.dumps(
            base_tracker.model_dump(mode="json")
        ).encode()

        jql = "project = TCMR AND [REPLACE BATCH DATES]"
        result = process_ticket_type(
            mock_jira_config,
            mock_jira_client,
            mock_api_client,
            JiraIssueType.TCMR,
            jql,
            sqs_publisher=mock_sqs_publisher,
        )

        assert result is False

    def test_process_tracker_update_to_processing_fails(
        self,
        mock_jira_config: MagicMock,
        mock_jira_client: MagicMock,
        mock_api_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        base_tracker: JiraBatchTracker,
    ) -> None:
        """Test processing when updating tracker to PROCESSING fails."""
        import json

        mock_api_client.get_request.return_value = json.dumps(
            base_tracker.model_dump(mode="json")
        ).encode()
        mock_api_client.post_json_request.side_effect = Exception("API error")

        jql = "project = TCMR AND [REPLACE BATCH DATES]"
        result = process_ticket_type(
            mock_jira_config,
            mock_jira_client,
            mock_api_client,
            JiraIssueType.TCMR,
            jql,
            sqs_publisher=mock_sqs_publisher,
        )

        assert result is False

    def test_process_jql_build_error(
        self,
        mock_jira_config: MagicMock,
        mock_jira_client: MagicMock,
        mock_api_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        base_tracker: JiraBatchTracker,
    ) -> None:
        """Test processing when JQL build fails (missing placeholder)."""
        import json

        mock_api_client.get_request.return_value = json.dumps(
            base_tracker.model_dump(mode="json")
        ).encode()
        mock_api_client.post_json_request.return_value = b'{"message": "ok"}'

        jql = "project = TCMR ORDER BY created DESC"
        result = process_ticket_type(
            mock_jira_config,
            mock_jira_client,
            mock_api_client,
            JiraIssueType.TCMR,
            jql,
            sqs_publisher=mock_sqs_publisher,
        )

        assert result is False

    def test_process_tracker_update_to_ok_fails(
        self,
        mock_jira_config: MagicMock,
        mock_jira_client: MagicMock,
        mock_api_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        base_tracker: JiraBatchTracker,
        sample_issue: Issue,
    ) -> None:
        """Test processing when updating tracker to OK fails."""
        import json

        mock_api_client.get_request.return_value = json.dumps(
            base_tracker.model_dump(mode="json")
        ).encode()
        # PROCESSING succeeds, OK tracker update fails
        # (issue goes to SQS — no API call for it)
        mock_api_client.post_json_request.side_effect = [
            b'{"message": "ok"}',  # PROCESSING tracker update
            Exception("API error"),  # OK tracker update fails
        ]
        mock_jira_client.fetch_issues.return_value = [{"key": "TCMR-123"}]
        mock_jira_client.enrich_issues.return_value = [sample_issue]

        jql = "project = TCMR AND [REPLACE BATCH DATES]"
        result = process_ticket_type(
            mock_jira_config,
            mock_jira_client,
            mock_api_client,
            JiraIssueType.TCMR,
            jql,
            sqs_publisher=mock_sqs_publisher,
        )

        assert result is False

    def test_process_with_zero_lookback_days(
        self,
        mock_jira_client: MagicMock,
        mock_api_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        base_tracker: JiraBatchTracker,
        sample_issue: Issue,
    ) -> None:
        """Test processing with zero lookback_days defaults to 30."""
        import json

        mock_jira_config = MagicMock()
        mock_jira_config.lookback_days = 0

        mock_api_client.get_request.return_value = json.dumps(
            base_tracker.model_dump(mode="json")
        ).encode()
        mock_api_client.post_json_request.return_value = b'{"message": "ok"}'
        mock_jira_client.fetch_issues.return_value = [{"key": "TCMR-123"}]
        mock_jira_client.enrich_issues.return_value = [sample_issue]

        jql = "project = TCMR AND [REPLACE BATCH DATES]"
        result = process_ticket_type(
            mock_jira_config,
            mock_jira_client,
            mock_api_client,
            JiraIssueType.TCMR,
            jql,
            sqs_publisher=mock_sqs_publisher,
        )

        assert result is True

    def test_process_with_lookback_log_type(
        self,
        mock_jira_client: MagicMock,
        mock_api_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        sample_issue: Issue,
    ) -> None:
        """Test processing triggers lookback log type when caught up."""
        import json

        mock_jira_config = MagicMock()
        mock_jira_config.lookback_days = 30
        mock_jira_config.catch_up_min_gap_minutes = 10

        tracker = JiraBatchTracker(
            ticket_type="tcmr",
            batch_start=datetime(2024, 5, 20, 0, 0, 0),
            batch_end=datetime(2024, 5, 28, 0, 0, 0),
            window_days=14,
            status="OK",
            error_message=None,
            last_processed_at=None,
            updated_at=datetime(2024, 5, 28, 0, 0, 0),
        )

        mock_api_client.get_request.return_value = json.dumps(
            tracker.model_dump(mode="json")
        ).encode()
        mock_api_client.post_json_request.return_value = b'{"message": "ok"}'
        mock_jira_client.fetch_issues.return_value = [{"key": "TCMR-123"}]
        mock_jira_client.enrich_issues.return_value = [sample_issue]

        jql = "project = TCMR AND [REPLACE BATCH DATES]"

        with patch("historian.jira.main.utc_now_naive") as mock_now:
            mock_now.return_value = datetime(2024, 6, 1, 0, 0, 0)
            result = process_ticket_type(
                mock_jira_config,
                mock_jira_client,
                mock_api_client,
                JiraIssueType.TCMR,
                jql,
                sqs_publisher=mock_sqs_publisher,
            )

        assert result is True

    def test_process_with_advance_log_type(
        self,
        mock_jira_client: MagicMock,
        mock_api_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        sample_issue: Issue,
    ) -> None:
        """Test processing triggers advance log type when not caught up."""
        import json

        mock_jira_config = MagicMock()
        mock_jira_config.lookback_days = 30
        mock_jira_config.catch_up_min_gap_minutes = 10

        tracker = JiraBatchTracker(
            ticket_type="tcmr",
            batch_start=datetime(2024, 1, 1, 0, 0, 0),
            batch_end=datetime(2024, 1, 15, 0, 0, 0),
            window_days=14,
            status="OK",
            error_message=None,
            last_processed_at=None,
            updated_at=datetime(2024, 1, 15, 0, 0, 0),
        )

        mock_api_client.get_request.return_value = json.dumps(
            tracker.model_dump(mode="json")
        ).encode()
        mock_api_client.post_json_request.return_value = b'{"message": "ok"}'
        mock_jira_client.fetch_issues.return_value = [{"key": "TCMR-123"}]
        mock_jira_client.enrich_issues.return_value = [sample_issue]

        jql = "project = TCMR AND [REPLACE BATCH DATES]"

        with patch("historian.jira.main.utc_now_naive") as mock_now:
            mock_now.return_value = datetime(2024, 6, 1, 0, 0, 0)
            result = process_ticket_type(
                mock_jira_config,
                mock_jira_client,
                mock_api_client,
                JiraIssueType.TCMR,
                jql,
                sqs_publisher=mock_sqs_publisher,
            )

        assert result is True

    def test_process_with_resume_log_type(
        self,
        mock_jira_client: MagicMock,
        mock_api_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        sample_issue: Issue,
    ) -> None:
        """Test processing triggers resume log type when status is PROCESSING."""
        import json

        mock_jira_config = MagicMock()
        mock_jira_config.lookback_days = 30
        mock_jira_config.catch_up_min_gap_minutes = 10

        tracker = JiraBatchTracker(
            ticket_type="tcmr",
            batch_start=datetime(2024, 1, 1, 0, 0, 0),
            batch_end=datetime(2024, 1, 15, 0, 0, 0),
            window_days=14,
            status="PROCESSING",
            error_message=None,
            last_processed_at=None,
            updated_at=datetime(2024, 1, 15, 0, 0, 0),
        )

        mock_api_client.get_request.return_value = json.dumps(
            tracker.model_dump(mode="json")
        ).encode()
        mock_api_client.post_json_request.return_value = b'{"message": "ok"}'
        mock_jira_client.fetch_issues.return_value = [{"key": "TCMR-123"}]
        mock_jira_client.enrich_issues.return_value = [sample_issue]

        jql = "project = TCMR AND [REPLACE BATCH DATES]"
        result = process_ticket_type(
            mock_jira_config,
            mock_jira_client,
            mock_api_client,
            JiraIssueType.TCMR,
            jql,
            sqs_publisher=mock_sqs_publisher,
        )

        assert result is True


# =============================================================================
# Test main() Entry Point
# =============================================================================


class TestMain:
    """Tests for main() entry point."""

    def _mock_config(self) -> MagicMock:
        """Create a mock config object for testing."""
        mock = MagicMock()
        mock.common.environment = "local"
        mock.common.log_level = "INFO"
        mock.jira = MagicMock()
        mock.jira.tcmr_jql = None
        mock.jira.operational_jql = None
        mock.api = None

        # The Enricher SQS queue is the sole LLM enrichment path and is required.
        mock.enricher = MagicMock()
        mock.enricher.region = "us-east-1"
        mock.enricher.enricher_queue_url = "https://sqs.us-east-1.amazonaws.com/q"
        return mock

    def test_main_returns_one_on_config_load_failure(self) -> None:
        """Test that main() returns 1 when config loading fails."""
        mock_logger = MagicMock()

        with (
            patch(
                "historian.base.runner.load_config",
                side_effect=Exception("Config error"),
            ),
            patch("historian.base.runner.logger", mock_logger),
        ):
            result = main()

            # Should return 1 on config failure
            assert result == 1

            # The shared shell logs the base-config load failure.
            mock_logger.exception.assert_called_once_with("Failed to load config")

    def test_main_returns_one_on_missing_jira_config(self) -> None:
        """Test that main() returns 1 when JIRA config is missing after merge."""
        config = self._mock_config()
        config.jira = None  # Config exists but jira section is None

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", return_value=config),
            patch("historian.base.runner.log_utils"),
        ):
            result = main()
            assert result == 1

    def test_main_returns_one_on_missing_api_config(self) -> None:
        """Test that main() returns 1 when API config is missing after merge."""
        config = self._mock_config()
        config.jira = MagicMock()  # jira config exists
        config.api = None  # but api config is missing

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", return_value=config),
            patch("historian.base.runner.log_utils"),
        ):
            result = main()
            assert result == 1

    def test_main_returns_one_on_missing_sqs_queue_url(self) -> None:
        """Test that main() returns 1 when sqs_queue_url is not configured."""
        config = self._mock_config()
        config.jira = MagicMock()
        config.jira.sqs_queue_url = None
        config.api = MagicMock()
        config.telescope = None

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", return_value=config),
            patch("historian.base.runner.log_utils"),
        ):
            result = main()
            assert result == 1

    def test_main_returns_one_on_missing_jira_specific_config(self) -> None:
        """Test that main() returns 1 when JIRA specific config file is missing."""
        config = self._mock_config()

        def merge_side_effect(cfg: MagicMock, filename: str) -> MagicMock:
            if filename == "matik-historian-jira-config.yml":
                raise FileNotFoundError("not found")
            return cfg

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", side_effect=merge_side_effect),
        ):
            result = main()
            assert result == 1

    def test_main_returns_one_when_enricher_config_missing(self) -> None:
        """Test that main() returns 1 when enricher config is missing.

        The Enricher SQS queue is the sole LLM enrichment path, so it is required.
        """
        config = self._mock_config()
        config.jira = MagicMock()
        config.jira.tcmr_jql_enabled = False
        config.jira.tcmr_jql = None
        config.jira.operational_jql_enabled = False
        config.jira.operational_jql = None
        config.api = MagicMock()
        config.telescope = None

        def merge_side_effect(cfg: MagicMock, filename: str) -> MagicMock:
            if filename == "matik-enricher-config.yml":
                raise FileNotFoundError("not found")
            return cfg

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", side_effect=merge_side_effect),
            patch("historian.base.runner.log_utils"),
        ):
            result = main()
            assert result == 1

    def test_main_returns_one_on_metrics_merge_failure(self) -> None:
        """A metrics.yml merge failure returns 1 (shell hard-fails all merges).

        This mirrors incidentio/GHE — metrics config is treated uniformly across
        sources by the shared shell.
        """
        config = self._mock_config()
        config.jira = MagicMock()
        config.api = MagicMock()
        config.telescope = None

        def merge_side_effect(cfg: MagicMock, filename: str) -> MagicMock:
            if filename == "metrics.yml":
                raise FileNotFoundError("not found")
            return cfg

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", side_effect=merge_side_effect),
            patch("historian.base.runner.log_utils"),
        ):
            result = main()
            assert result == 1

    def test_main_with_telescope_metrics_enabled(self) -> None:
        """Test main() initializes telescope metrics when enabled."""
        config = self._mock_config()
        config.jira = MagicMock()
        config.jira.tcmr_jql_enabled = False
        config.jira.tcmr_jql = None
        config.jira.operational_jql_enabled = False
        config.jira.operational_jql = None
        config.api = MagicMock()
        config.telescope = MagicMock()
        config.telescope.enabled = True

        mock_telescope = MagicMock()

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", return_value=config),
            patch("historian.base.runner.log_utils"),
            patch("historian.jira.main.boto3"),
            patch("historian.jira.main.EnrichmentPublisherSync"),
            patch("historian.jira.main.SQSPublisher"),
            patch(
                "historian.base.runner.TelescopeClient", return_value=mock_telescope
            ) as mock_telescope_cls,
        ):
            result = main()

            assert result == 0
            mock_telescope_cls.assert_called_once()
            mock_telescope.start.assert_called_once()
            mock_telescope.shutdown.assert_called_once()

    def test_main_processes_tcmr_tickets(self) -> None:
        """Test main() processes TCMR tickets when configured."""
        config = self._mock_config()
        config.jira = MagicMock()
        config.jira.tcmr_jql_enabled = True
        config.jira.tcmr_jql = "project = TCMR AND [REPLACE BATCH DATES]"
        config.jira.operational_jql_enabled = False
        config.jira.operational_jql = None
        config.api = MagicMock()
        config.telescope = None

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", return_value=config),
            patch("historian.base.runner.log_utils"),
            patch("historian.jira.main.boto3"),
            patch("historian.jira.main.EnrichmentPublisherSync"),
            patch("historian.jira.main.SQSPublisher"),
            patch("historian.jira.main.create_jira_client"),
            patch("historian.jira.main.create_matik_api_client"),
            patch(
                "historian.jira.main.process_ticket_type", return_value=True
            ) as mock_process,
        ):
            result = main()

            assert result == 0
            mock_process.assert_called_once()

    def test_main_processes_operational_tickets(self) -> None:
        """Test main() processes operational tickets when configured."""
        config = self._mock_config()
        config.jira = MagicMock()
        config.jira.tcmr_jql_enabled = False
        config.jira.tcmr_jql = None
        config.jira.operational_jql_enabled = True
        config.jira.operational_jql = "project = OPS AND [REPLACE BATCH DATES]"
        config.api = MagicMock()
        config.telescope = None

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", return_value=config),
            patch("historian.base.runner.log_utils"),
            patch("historian.jira.main.boto3"),
            patch("historian.jira.main.EnrichmentPublisherSync"),
            patch("historian.jira.main.SQSPublisher"),
            patch("historian.jira.main.create_jira_client"),
            patch("historian.jira.main.create_matik_api_client"),
            patch(
                "historian.jira.main.process_ticket_type", return_value=True
            ) as mock_process,
        ):
            result = main()

            assert result == 0
            mock_process.assert_called_once()

    def test_main_returns_one_on_tcmr_failure(self) -> None:
        """Test main() returns 1 when TCMR processing fails."""
        config = self._mock_config()
        config.jira = MagicMock()
        config.jira.tcmr_jql_enabled = True
        config.jira.tcmr_jql = "project = TCMR AND [REPLACE BATCH DATES]"
        config.jira.operational_jql_enabled = False
        config.jira.operational_jql = None
        config.api = MagicMock()
        config.telescope = None

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", return_value=config),
            patch("historian.base.runner.log_utils"),
            patch("historian.jira.main.boto3"),
            patch("historian.jira.main.EnrichmentPublisherSync"),
            patch("historian.jira.main.SQSPublisher"),
            patch("historian.jira.main.create_jira_client"),
            patch("historian.jira.main.create_matik_api_client"),
            patch("historian.jira.main.process_ticket_type", return_value=False),
        ):
            result = main()

            assert result == 1

    def test_main_returns_one_on_tcmr_exception(self) -> None:
        """Test main() returns 1 when TCMR processing throws exception."""
        config = self._mock_config()
        config.jira = MagicMock()
        config.jira.tcmr_jql_enabled = True
        config.jira.tcmr_jql = "project = TCMR AND [REPLACE BATCH DATES]"
        config.jira.operational_jql_enabled = False
        config.jira.operational_jql = None
        config.api = MagicMock()
        config.telescope = None

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", return_value=config),
            patch("historian.base.runner.log_utils"),
            patch("historian.jira.main.boto3"),
            patch("historian.jira.main.EnrichmentPublisherSync"),
            patch("historian.jira.main.SQSPublisher"),
            patch("historian.jira.main.create_jira_client"),
            patch("historian.jira.main.create_matik_api_client"),
            patch(
                "historian.jira.main.process_ticket_type",
                side_effect=Exception("error"),
            ),
        ):
            result = main()

            assert result == 1

    def test_main_with_job_metrics(self) -> None:
        """Test main() records job metrics when telescope is enabled."""
        config = self._mock_config()
        config.jira = MagicMock()
        config.jira.tcmr_jql_enabled = True
        config.jira.tcmr_jql = "project = TCMR AND [REPLACE BATCH DATES]"
        config.jira.operational_jql_enabled = False
        config.jira.operational_jql = None
        config.api = MagicMock()
        config.telescope = MagicMock()
        config.telescope.enabled = True

        mock_telescope = MagicMock()
        mock_job_metrics = MagicMock()
        mock_record_job = MagicMock()
        mock_job_metrics.start_job.return_value = mock_record_job

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", return_value=config),
            patch("historian.base.runner.log_utils"),
            patch("historian.jira.main.boto3"),
            patch("historian.jira.main.EnrichmentPublisherSync"),
            patch("historian.jira.main.SQSPublisher"),
            patch("historian.base.runner.TelescopeClient", return_value=mock_telescope),
            patch("historian.jira.main.ClientMetrics"),
            patch("historian.jira.main.JiraCacheMetrics"),
            patch("historian.jira.main.JobMetrics", return_value=mock_job_metrics),
            patch("historian.jira.main.create_jira_client"),
            patch("historian.jira.main.create_matik_api_client"),
            patch("historian.jira.main.process_ticket_type", return_value=True),
        ):
            result = main()

            assert result == 0
            mock_job_metrics.start_job.assert_called_with("jira_tcmr")
            mock_record_job.assert_called_once_with(1, None)

    def test_main_job_metrics_records_tcmr_failure(self) -> None:
        """Test main() records job metrics on TCMR failure."""
        config = self._mock_config()
        config.jira = MagicMock()
        config.jira.tcmr_jql_enabled = True
        config.jira.tcmr_jql = "project = TCMR AND [REPLACE BATCH DATES]"
        config.jira.operational_jql_enabled = False
        config.jira.operational_jql = None
        config.api = MagicMock()
        config.telescope = MagicMock()
        config.telescope.enabled = True

        mock_telescope = MagicMock()
        mock_job_metrics = MagicMock()
        mock_record_job = MagicMock()
        mock_job_metrics.start_job.return_value = mock_record_job

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", return_value=config),
            patch("historian.base.runner.log_utils"),
            patch("historian.jira.main.boto3"),
            patch("historian.jira.main.EnrichmentPublisherSync"),
            patch("historian.jira.main.SQSPublisher"),
            patch("historian.base.runner.TelescopeClient", return_value=mock_telescope),
            patch("historian.jira.main.ClientMetrics"),
            patch("historian.jira.main.JiraCacheMetrics"),
            patch("historian.jira.main.JobMetrics", return_value=mock_job_metrics),
            patch("historian.jira.main.create_jira_client"),
            patch("historian.jira.main.create_matik_api_client"),
            patch("historian.jira.main.process_ticket_type", return_value=False),
        ):
            result = main()

            assert result == 1
            # Should record failure with exception
            assert mock_record_job.call_count == 1
            call_args = mock_record_job.call_args[0]
            assert call_args[0] == 0
            assert isinstance(call_args[1], Exception)

    def test_main_job_metrics_records_tcmr_exception(self) -> None:
        """Test main() records job metrics on TCMR exception."""
        config = self._mock_config()
        config.jira = MagicMock()
        config.jira.tcmr_jql_enabled = True
        config.jira.tcmr_jql = "project = TCMR AND [REPLACE BATCH DATES]"
        config.jira.operational_jql_enabled = False
        config.jira.operational_jql = None
        config.api = MagicMock()
        config.telescope = MagicMock()
        config.telescope.enabled = True

        mock_telescope = MagicMock()
        mock_job_metrics = MagicMock()
        mock_record_job = MagicMock()
        mock_job_metrics.start_job.return_value = mock_record_job
        test_error = Exception("test error")

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", return_value=config),
            patch("historian.base.runner.log_utils"),
            patch("historian.jira.main.boto3"),
            patch("historian.jira.main.EnrichmentPublisherSync"),
            patch("historian.jira.main.SQSPublisher"),
            patch("historian.base.runner.TelescopeClient", return_value=mock_telescope),
            patch("historian.jira.main.ClientMetrics"),
            patch("historian.jira.main.JiraCacheMetrics"),
            patch("historian.jira.main.JobMetrics", return_value=mock_job_metrics),
            patch("historian.jira.main.create_jira_client"),
            patch("historian.jira.main.create_matik_api_client"),
            patch("historian.jira.main.process_ticket_type", side_effect=test_error),
        ):
            result = main()

            assert result == 1
            mock_record_job.assert_called_once_with(0, test_error)

    def test_main_returns_one_on_operational_failure(self) -> None:
        """Test main() returns 1 when operational processing fails."""
        config = self._mock_config()
        config.jira = MagicMock()
        config.jira.tcmr_jql_enabled = False
        config.jira.tcmr_jql = None
        config.jira.operational_jql_enabled = True
        config.jira.operational_jql = "project = OPS AND [REPLACE BATCH DATES]"
        config.api = MagicMock()
        config.telescope = None

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", return_value=config),
            patch("historian.base.runner.log_utils"),
            patch("historian.jira.main.boto3"),
            patch("historian.jira.main.EnrichmentPublisherSync"),
            patch("historian.jira.main.SQSPublisher"),
            patch("historian.jira.main.create_jira_client"),
            patch("historian.jira.main.create_matik_api_client"),
            patch("historian.jira.main.process_ticket_type", return_value=False),
        ):
            result = main()

            assert result == 1

    def test_main_returns_one_on_operational_exception(self) -> None:
        """Test main() returns 1 when operational processing throws exception."""
        config = self._mock_config()
        config.jira = MagicMock()
        config.jira.tcmr_jql_enabled = False
        config.jira.tcmr_jql = None
        config.jira.operational_jql_enabled = True
        config.jira.operational_jql = "project = OPS AND [REPLACE BATCH DATES]"
        config.api = MagicMock()
        config.telescope = None

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", return_value=config),
            patch("historian.base.runner.log_utils"),
            patch("historian.jira.main.boto3"),
            patch("historian.jira.main.EnrichmentPublisherSync"),
            patch("historian.jira.main.SQSPublisher"),
            patch("historian.jira.main.create_jira_client"),
            patch("historian.jira.main.create_matik_api_client"),
            patch(
                "historian.jira.main.process_ticket_type",
                side_effect=Exception("error"),
            ),
        ):
            result = main()

            assert result == 1

    def test_main_job_metrics_records_operational_failure(self) -> None:
        """Test main() records job metrics on operational failure."""
        config = self._mock_config()
        config.jira = MagicMock()
        config.jira.tcmr_jql_enabled = False
        config.jira.tcmr_jql = None
        config.jira.operational_jql_enabled = True
        config.jira.operational_jql = "project = OPS AND [REPLACE BATCH DATES]"
        config.api = MagicMock()
        config.telescope = MagicMock()
        config.telescope.enabled = True

        mock_telescope = MagicMock()
        mock_job_metrics = MagicMock()
        mock_record_job = MagicMock()
        mock_job_metrics.start_job.return_value = mock_record_job

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", return_value=config),
            patch("historian.base.runner.log_utils"),
            patch("historian.jira.main.boto3"),
            patch("historian.jira.main.EnrichmentPublisherSync"),
            patch("historian.jira.main.SQSPublisher"),
            patch("historian.base.runner.TelescopeClient", return_value=mock_telescope),
            patch("historian.jira.main.ClientMetrics"),
            patch("historian.jira.main.JiraCacheMetrics"),
            patch("historian.jira.main.JobMetrics", return_value=mock_job_metrics),
            patch("historian.jira.main.create_jira_client"),
            patch("historian.jira.main.create_matik_api_client"),
            patch("historian.jira.main.process_ticket_type", return_value=False),
        ):
            result = main()

            assert result == 1
            mock_job_metrics.start_job.assert_called_with("jira_operational")
            # Should record failure with exception
            assert mock_record_job.call_count == 1
            call_args = mock_record_job.call_args[0]
            assert call_args[0] == 0
            assert isinstance(call_args[1], Exception)

    def test_main_job_metrics_records_operational_exception(self) -> None:
        """Test main() records job metrics on operational exception."""
        config = self._mock_config()
        config.jira = MagicMock()
        config.jira.tcmr_jql_enabled = False
        config.jira.tcmr_jql = None
        config.jira.operational_jql_enabled = True
        config.jira.operational_jql = "project = OPS AND [REPLACE BATCH DATES]"
        config.api = MagicMock()
        config.telescope = MagicMock()
        config.telescope.enabled = True

        mock_telescope = MagicMock()
        mock_job_metrics = MagicMock()
        mock_record_job = MagicMock()
        mock_job_metrics.start_job.return_value = mock_record_job
        test_error = Exception("test error")

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", return_value=config),
            patch("historian.base.runner.log_utils"),
            patch("historian.jira.main.boto3"),
            patch("historian.jira.main.EnrichmentPublisherSync"),
            patch("historian.jira.main.SQSPublisher"),
            patch("historian.base.runner.TelescopeClient", return_value=mock_telescope),
            patch("historian.jira.main.ClientMetrics"),
            patch("historian.jira.main.JiraCacheMetrics"),
            patch("historian.jira.main.JobMetrics", return_value=mock_job_metrics),
            patch("historian.jira.main.create_jira_client"),
            patch("historian.jira.main.create_matik_api_client"),
            patch("historian.jira.main.process_ticket_type", side_effect=test_error),
        ):
            result = main()

            assert result == 1
            mock_record_job.assert_called_once_with(0, test_error)

    def test_main_job_metrics_records_operational_success(self) -> None:
        """Test main() records job metrics on operational success."""
        config = self._mock_config()
        config.jira = MagicMock()
        config.jira.tcmr_jql_enabled = False
        config.jira.tcmr_jql = None
        config.jira.operational_jql_enabled = True
        config.jira.operational_jql = "project = OPS AND [REPLACE BATCH DATES]"
        config.api = MagicMock()
        config.telescope = MagicMock()
        config.telescope.enabled = True

        mock_telescope = MagicMock()
        mock_job_metrics = MagicMock()
        mock_record_job = MagicMock()
        mock_job_metrics.start_job.return_value = mock_record_job

        with (
            patch("historian.base.runner.load_config", return_value=config),
            patch("historian.base.runner.merge_config", return_value=config),
            patch("historian.base.runner.log_utils"),
            patch("historian.jira.main.boto3"),
            patch("historian.jira.main.EnrichmentPublisherSync"),
            patch("historian.jira.main.SQSPublisher"),
            patch("historian.base.runner.TelescopeClient", return_value=mock_telescope),
            patch("historian.jira.main.ClientMetrics"),
            patch("historian.jira.main.JiraCacheMetrics"),
            patch("historian.jira.main.JobMetrics", return_value=mock_job_metrics),
            patch("historian.jira.main.create_jira_client"),
            patch("historian.jira.main.create_matik_api_client"),
            patch("historian.jira.main.process_ticket_type", return_value=True),
        ):
            result = main()

            assert result == 0
            mock_job_metrics.start_job.assert_called_with("jira_operational")
            mock_record_job.assert_called_once_with(1, None)


# =============================================================================
# Test _enrich_with_services
# =============================================================================


class TestEnrichWithServices:
    """Tests for _enrich_with_services function."""

    @pytest.fixture
    def mock_api_client(self) -> MagicMock:
        """Create mock Matik API client."""
        return MagicMock()

    @pytest.fixture
    def backstage_mapping(self) -> dict[str, list[str]]:
        """Create a backstage mapping for testing."""
        return {
            "AWS S3": ["biztech_aws-s3"],
            "Airflow": ["airflow", "airflow-worker"],
        }

    def test_enrich_via_pr_link(self, mock_api_client: MagicMock) -> None:
        """Test enrichment via PR link lookup."""
        issue = Issue(
            id="1",
            key="TCMR-100",
            tcmr_related_git_pr_link="https://github.airbnb.biz/Org/repo/pull/19",
        )

        # Mock the API response for PR service lookup
        import json

        mock_api_client.post_json_request.return_value = json.dumps(
            {"Org:repo:19": ["service-a", "service-b"]}
        ).encode()

        result = _enrich_with_services([issue], mock_api_client, {})

        assert "TCMR-100" in result
        assert result["TCMR-100"] == ["service-a", "service-b"]

    def test_fallback_to_mapping(
        self,
        mock_api_client: MagicMock,
        backstage_mapping: dict[str, list[str]],
    ) -> None:
        """Test fallback to backstage mapping when no PR link."""
        issue = Issue(
            id="2",
            key="TCMR-200",
            tcmr_related_services=["AWS S3", "Airflow"],
        )

        result = _enrich_with_services([issue], mock_api_client, backstage_mapping)

        assert "TCMR-200" in result
        assert result["TCMR-200"] == [
            "biztech_aws-s3",
            "airflow",
            "airflow-worker",
        ]

    def test_pr_link_takes_precedence(
        self,
        mock_api_client: MagicMock,
        backstage_mapping: dict[str, list[str]],
    ) -> None:
        """Test that PR link services take precedence over mapping."""
        issue = Issue(
            id="3",
            key="TCMR-300",
            tcmr_related_git_pr_link="https://github.com/Org/repo/pull/1",
            tcmr_related_services=["AWS S3"],
        )

        import json

        mock_api_client.post_json_request.return_value = json.dumps(
            {"Org:repo:1": ["pr-service"]}
        ).encode()

        result = _enrich_with_services([issue], mock_api_client, backstage_mapping)

        assert result["TCMR-300"] == ["pr-service"]

    def test_fallback_when_pr_not_found(
        self,
        mock_api_client: MagicMock,
        backstage_mapping: dict[str, list[str]],
    ) -> None:
        """Test fallback to mapping when PR is not found in DB."""
        issue = Issue(
            id="4",
            key="TCMR-400",
            tcmr_related_git_pr_link="https://github.com/Org/repo/pull/999",
            tcmr_related_services=["AWS S3"],
        )

        import json

        # PR not found - return empty response
        mock_api_client.post_json_request.return_value = json.dumps({}).encode()

        result = _enrich_with_services([issue], mock_api_client, backstage_mapping)

        assert result["TCMR-400"] == ["biztech_aws-s3"]

    def test_no_enrichment_possible(self, mock_api_client: MagicMock) -> None:
        """Test issue with no PR link and no tcmr_related_services."""
        issue = Issue(
            id="5",
            key="TCMR-500",
        )

        result = _enrich_with_services([issue], mock_api_client, {})

        assert "TCMR-500" not in result

    def test_api_error_falls_back_to_mapping(
        self,
        mock_api_client: MagicMock,
        backstage_mapping: dict[str, list[str]],
    ) -> None:
        """Test that API error on PR lookup falls back to mapping."""
        issue = Issue(
            id="6",
            key="TCMR-600",
            tcmr_related_git_pr_link="https://github.com/Org/repo/pull/1",
            tcmr_related_services=["AWS S3"],
        )

        mock_api_client.post_json_request.side_effect = Exception("API error")

        result = _enrich_with_services([issue], mock_api_client, backstage_mapping)

        # Should fall back to mapping since PR lookup failed
        assert result["TCMR-600"] == ["biztech_aws-s3"]

    def test_empty_issues_list(self, mock_api_client: MagicMock) -> None:
        """Test with empty issues list."""
        result = _enrich_with_services([], mock_api_client, {})
        assert result == {}

    def test_invalid_pr_url_falls_back(
        self,
        mock_api_client: MagicMock,
        backstage_mapping: dict[str, list[str]],
    ) -> None:
        """Test that unparseable PR URL falls back to mapping."""
        issue = Issue(
            id="7",
            key="TCMR-700",
            tcmr_related_git_pr_link="not-a-valid-url",
            tcmr_related_services=["AWS S3"],
        )

        result = _enrich_with_services([issue], mock_api_client, backstage_mapping)

        assert result["TCMR-700"] == ["biztech_aws-s3"]


# =============================================================================
# Test _save_issues_via_sqs with service enrichment
# =============================================================================


class TestSaveIssuesWithEnrichment:
    """Tests for _save_issues_via_sqs with backstage_mapping."""

    def test_save_with_enrichment(
        self, mock_sqs_publisher: MagicMock, sample_issue: Issue
    ) -> None:
        """Test that enrichment is applied when backstage_mapping is provided."""
        mock_api_client = MagicMock()
        mock_api_client.post_json_request.return_value = b"{}"

        sample_issue.tcmr_related_services = ["AWS S3"]
        backstage_mapping = {"AWS S3": ["biztech_aws-s3"]}

        result = _save_issues_via_sqs(
            mock_sqs_publisher,
            mock_api_client,
            [sample_issue],
            "tcmr",
            backstage_mapping=backstage_mapping,
        )

        assert result == 1
        mock_sqs_publisher.send_sync.assert_called_once()

    def test_save_without_enrichment(
        self, mock_sqs_publisher: MagicMock, sample_issue: Issue
    ) -> None:
        """Test that saving works without backstage_mapping."""
        mock_api_client = MagicMock()

        result = _save_issues_via_sqs(
            mock_sqs_publisher, mock_api_client, [sample_issue], "tcmr"
        )

        assert result == 1
        mock_sqs_publisher.send_sync.assert_called_once()
