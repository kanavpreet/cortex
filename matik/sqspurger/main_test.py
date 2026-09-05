"""Tests for SQS Purger service entry point."""

import sys
from unittest.mock import MagicMock, call, patch

import botocore.exceptions
import pytest
from structlog.testing import capture_logs


def _mock_config(queue_urls: list[str] | None = None) -> MagicMock:
    """Create a mock config object for testing."""
    mock = MagicMock()
    mock.common.environment = "local"
    mock.common.log_level = "INFO"
    mock.sqs_purger.region = "us-east-1"
    mock.sqs_purger.queue_urls = queue_urls if queue_urls is not None else []
    return mock


class TestMainPurge:
    """Tests for main() purge logic."""

    def test_no_queue_urls_skips_purge(self) -> None:
        """Test that main() exits early when no queue URLs are configured."""
        from sqspurger.main import main

        with (
            capture_logs() as cap_logs,
            patch("sqspurger.main.load_config", return_value=_mock_config([])),
            patch("sqspurger.main.boto3") as mock_boto3,
        ):
            main()

            events = [log["event"] for log in cap_logs]
            assert "no queue urls configured, nothing to purge" in events
            mock_boto3.client.assert_not_called()

    def test_no_sqs_purger_config_skips_purge(self) -> None:
        """Test that main() exits early when sqs_purger config is None."""
        from sqspurger.main import main

        mock_cfg = MagicMock()
        mock_cfg.common.environment = "local"
        mock_cfg.common.log_level = "INFO"
        mock_cfg.sqs_purger = None

        with (
            capture_logs() as cap_logs,
            patch("sqspurger.main.load_config", return_value=mock_cfg),
            patch("sqspurger.main.boto3") as mock_boto3,
        ):
            main()

            events = [log["event"] for log in cap_logs]
            assert "no queue urls configured, nothing to purge" in events
            mock_boto3.client.assert_not_called()

    def test_purges_all_queues_successfully(self) -> None:
        """Test that main() calls purge_queue for each URL and logs success."""
        from sqspurger.main import main

        urls = [
            "https://sqs.us-east-1.amazonaws.com/123/queue-a",
            "https://sqs.us-east-1.amazonaws.com/123/queue-b",
        ]

        mock_sqs = MagicMock()

        with (
            capture_logs() as cap_logs,
            patch("sqspurger.main.load_config", return_value=_mock_config(urls)),
            patch("sqspurger.main.boto3") as mock_boto3,
        ):
            mock_boto3.client.return_value = mock_sqs
            main()

            mock_boto3.client.assert_called_once_with("sqs", region_name="us-east-1")
            mock_sqs.purge_queue.assert_has_calls(
                [call(QueueUrl=url) for url in urls], any_order=False
            )
            events = [log["event"] for log in cap_logs]
            assert "purged queue" in events
            assert "all queues purged successfully" in events

    def test_single_queue_failure_exits_1(self) -> None:
        """Test that main() exits with code 1 when a queue fails to purge."""
        from sqspurger.main import main

        urls = ["https://sqs.us-east-1.amazonaws.com/123/queue-a"]
        mock_sqs = MagicMock()
        mock_sqs.purge_queue.side_effect = Exception("AWS error")

        with (
            capture_logs() as cap_logs,
            patch("sqspurger.main.load_config", return_value=_mock_config(urls)),
            patch("sqspurger.main.boto3") as mock_boto3,
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_boto3.client.return_value = mock_sqs
            main()

        assert exc_info.value.code == 1
        events = [log["event"] for log in cap_logs]
        assert "failed to purge queue" in events
        assert "some queues failed to purge" in events

    def test_partial_failure_continues_and_exits_1(self) -> None:
        """Test that main() continues purging remaining queues after a failure."""
        from sqspurger.main import main

        urls = [
            "https://sqs.us-east-1.amazonaws.com/123/queue-ok",
            "https://sqs.us-east-1.amazonaws.com/123/queue-fail",
            "https://sqs.us-east-1.amazonaws.com/123/queue-ok-2",
        ]
        mock_sqs = MagicMock()
        mock_sqs.purge_queue.side_effect = [None, Exception("AWS error"), None]

        with (
            capture_logs() as cap_logs,
            patch("sqspurger.main.load_config", return_value=_mock_config(urls)),
            patch("sqspurger.main.boto3") as mock_boto3,
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_boto3.client.return_value = mock_sqs
            main()

        assert exc_info.value.code == 1
        # All three purge_queue calls were made
        assert mock_sqs.purge_queue.call_count == 3
        events = [log["event"] for log in cap_logs]
        assert "purged queue" in events
        assert "failed to purge queue" in events

    def test_purge_in_progress_is_skipped_not_failed(self) -> None:
        """Test that PurgeQueueInProgress is treated as success, not a failure."""
        from sqspurger.main import main

        urls = [
            "https://sqs.us-east-1.amazonaws.com/123/queue-a",
            "https://sqs.us-east-1.amazonaws.com/123/queue-b",
        ]
        in_progress_error = botocore.exceptions.ClientError(
            {
                "Error": {
                    "Code": "AWS.SimpleQueueService.PurgeQueueInProgress",
                    "Message": "",
                }
            },
            "PurgeQueue",
        )
        mock_sqs = MagicMock()
        mock_sqs.purge_queue.side_effect = [None, in_progress_error]

        with (
            capture_logs() as cap_logs,
            patch("sqspurger.main.load_config", return_value=_mock_config(urls)),
            patch("sqspurger.main.boto3") as mock_boto3,
        ):
            mock_boto3.client.return_value = mock_sqs
            main()  # should NOT raise SystemExit

            events = [log["event"] for log in cap_logs]
            assert "purge already in progress, skipping" in events
            assert "failed to purge queue" not in events
            assert "all queues purged successfully" in events

    def test_other_client_error_is_a_failure(self) -> None:
        """Test that non-PurgeInProgress ClientErrors are treated as failures."""
        from sqspurger.main import main

        urls = ["https://sqs.us-east-1.amazonaws.com/123/queue-a"]
        access_denied = botocore.exceptions.ClientError(
            {"Error": {"Code": "AccessDenied", "Message": ""}},
            "PurgeQueue",
        )
        mock_sqs = MagicMock()
        mock_sqs.purge_queue.side_effect = access_denied

        with (
            capture_logs() as cap_logs,
            patch("sqspurger.main.load_config", return_value=_mock_config(urls)),
            patch("sqspurger.main.boto3") as mock_boto3,
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_boto3.client.return_value = mock_sqs
            main()

        assert exc_info.value.code == 1
        events = [log["event"] for log in cap_logs]
        assert "failed to purge queue" in events

    def test_uses_configured_region(self) -> None:
        """Test that the SQS client is created with the configured region."""
        from sqspurger.main import main

        mock_cfg = _mock_config(["https://sqs.eu-west-1.amazonaws.com/123/queue"])
        mock_cfg.sqs_purger.region = "eu-west-1"

        mock_sqs = MagicMock()

        with (
            patch("sqspurger.main.load_config", return_value=mock_cfg),
            patch("sqspurger.main.boto3") as mock_boto3,
        ):
            mock_boto3.client.return_value = mock_sqs
            main()

            mock_boto3.client.assert_called_once_with("sqs", region_name="eu-west-1")


class TestModuleEntryPoint:
    """Tests for module entry point."""

    def test_module_entry_point(self) -> None:
        """Test that the module entry point calls main()."""
        import runpy

        for mod in list(sys.modules.keys()):
            if mod == "sqspurger" or mod.startswith("sqspurger."):
                del sys.modules[mod]

        urls = ["https://sqs.us-east-1.amazonaws.com/123/queue-a"]

        with (
            capture_logs() as cap_logs,
            patch("common.config.load_config", return_value=_mock_config(urls)),
            patch("boto3.client") as mock_client,
        ):
            mock_sqs = MagicMock()
            mock_client.return_value = mock_sqs
            runpy.run_module("sqspurger", run_name="main", alter_sys=True)

            events = [log["event"] for log in cap_logs]
            assert "all queues purged successfully" in events
