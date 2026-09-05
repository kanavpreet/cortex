"""Tests for Jira cache metrics."""

import unittest
from unittest.mock import MagicMock

from common.metrics.jira_cache_metrics import JiraCacheMetrics


class TestJiraCacheMetricsInit(unittest.TestCase):
    """Tests for JiraCacheMetrics initialization."""

    def test_creates_all_instruments(self) -> None:
        """Test that all metric instruments are created on init."""
        mock_meter = MagicMock()
        mock_meter.create_counter.return_value = MagicMock()

        JiraCacheMetrics(mock_meter, "historian")

        # Verify counters created
        counter_calls = mock_meter.create_counter.call_args_list
        counter_names = [call.kwargs["name"] for call in counter_calls]
        assert "matik_jira_crawler_issues_processed_total" in counter_names
        assert "matik_jira_crawler_facade_calls_total" in counter_names

    def test_stores_service_name(self) -> None:
        """Test that service name is stored."""
        mock_meter = MagicMock()
        mock_meter.create_counter.return_value = MagicMock()

        metrics = JiraCacheMetrics(mock_meter, "historian")

        assert metrics._service_name == "historian"


class TestJiraCacheMetricsCrawlerStats(unittest.TestCase):
    """Tests for record_crawler_stats method."""

    def setUp(self) -> None:
        self.mock_meter = MagicMock()
        self.mock_issues_processed = MagicMock()
        self.mock_facade_calls = MagicMock()

        counter_map = {
            "matik_jira_crawler_issues_processed_total": self.mock_issues_processed,
            "matik_jira_crawler_facade_calls_total": self.mock_facade_calls,
        }

        def create_counter_side_effect(**kwargs: str) -> MagicMock:
            return counter_map.get(kwargs["name"], MagicMock())

        self.mock_meter.create_counter.side_effect = create_counter_side_effect
        self.metrics = JiraCacheMetrics(self.mock_meter, "historian")

    def test_record_crawler_stats(self) -> None:
        """Test recording crawler statistics."""
        self.metrics.record_crawler_stats(
            issues_processed=50,
            facade_calls=15,
        )

        base_attrs = {
            "service": "historian",
            "connector_type": "jira_issues",
        }

        self.mock_issues_processed.add.assert_called_once_with(50, base_attrs)
        self.mock_facade_calls.add.assert_called_once_with(15, base_attrs)

    def test_record_crawler_stats_zero_issues(self) -> None:
        """Test that zero issues_processed is not recorded."""
        self.metrics.record_crawler_stats(
            issues_processed=0,
            facade_calls=10,
        )

        self.mock_issues_processed.add.assert_not_called()
        self.mock_facade_calls.add.assert_called_once()

    def test_record_crawler_stats_zero_facade_calls(self) -> None:
        """Test that zero facade_calls is not recorded."""
        self.metrics.record_crawler_stats(
            issues_processed=50,
            facade_calls=0,
        )

        self.mock_issues_processed.add.assert_called_once()
        self.mock_facade_calls.add.assert_not_called()

    def test_record_crawler_stats_all_zeros(self) -> None:
        """Test that all zero values are not recorded."""
        self.metrics.record_crawler_stats(
            issues_processed=0,
            facade_calls=0,
        )

        self.mock_issues_processed.add.assert_not_called()
        self.mock_facade_calls.add.assert_not_called()


if __name__ == "__main__":
    unittest.main()
