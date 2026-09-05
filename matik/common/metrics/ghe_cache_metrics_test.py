"""Tests for GHE crawler metrics."""

from unittest.mock import MagicMock

import pytest

from common.metrics.ghe_cache_metrics import GHECacheMetrics


class TestGHECacheMetrics:
    """Tests for GHECacheMetrics class."""

    @pytest.fixture
    def mock_counters(self) -> dict[str, MagicMock]:
        """Create separate mock counters for each metric."""
        return {
            "rate_limit_events": MagicMock(),
            "orgs_processed": MagicMock(),
            "repos_processed": MagicMock(),
            "prs_upserted": MagicMock(),
            "api_calls": MagicMock(),
        }

    @pytest.fixture
    def mock_meter(self, mock_counters: dict[str, MagicMock]) -> MagicMock:
        """Create a mock meter that returns different counters."""
        meter = MagicMock()

        def create_counter_side_effect(name: str, **kwargs: str) -> MagicMock:
            if "rate_limit" in name:
                return mock_counters["rate_limit_events"]
            elif "orgs_processed" in name:
                return mock_counters["orgs_processed"]
            elif "repos_processed" in name:
                return mock_counters["repos_processed"]
            elif "prs_upserted" in name:
                return mock_counters["prs_upserted"]
            elif "api_calls" in name:
                return mock_counters["api_calls"]
            return MagicMock()

        meter.create_counter = MagicMock(side_effect=create_counter_side_effect)
        return meter

    @pytest.fixture
    def metrics(
        self, mock_meter: MagicMock, mock_counters: dict[str, MagicMock]
    ) -> GHECacheMetrics:
        """Create GHECacheMetrics instance for testing."""
        return GHECacheMetrics(mock_meter, "historian")

    def test_init_creates_instruments(self) -> None:
        """Test that initialization creates all required instruments."""
        meter = MagicMock()
        meter.create_counter = MagicMock(return_value=MagicMock())
        GHECacheMetrics(meter, "historian")

        # Should create 5 counters (1 rate limit + 4 crawler stats)
        assert meter.create_counter.call_count == 5

        # Verify counter names
        counter_calls = meter.create_counter.call_args_list
        counter_names = [call_args[1]["name"] for call_args in counter_calls]

        assert "matik_ghe_rate_limit_events_total" in counter_names

        # Crawler statistics counters
        assert "matik_ghe_crawler_orgs_processed_total" in counter_names
        assert "matik_ghe_crawler_repos_processed_total" in counter_names
        assert "matik_ghe_crawler_prs_upserted_total" in counter_names
        assert "matik_ghe_crawler_api_calls_total" in counter_names

    def test_record_rate_limit(
        self,
        metrics: GHECacheMetrics,
        mock_counters: dict[str, MagicMock],
    ) -> None:
        """Test recording rate limit event."""
        metrics.record_rate_limit("get_prs")

        mock_counters["rate_limit_events"].add.assert_called_once_with(
            1, {"service": "historian", "operation": "get_prs"}
        )

    def test_record_rate_limit_different_operations(
        self,
        metrics: GHECacheMetrics,
        mock_counters: dict[str, MagicMock],
    ) -> None:
        """Test recording rate limit events with different operations."""
        metrics.record_rate_limit("get_repos")
        metrics.record_rate_limit("get_organization")

        assert mock_counters["rate_limit_events"].add.call_count == 2
        mock_counters["rate_limit_events"].add.assert_any_call(
            1, {"service": "historian", "operation": "get_repos"}
        )
        mock_counters["rate_limit_events"].add.assert_any_call(
            1, {"service": "historian", "operation": "get_organization"}
        )


class TestRecordCrawlerStats:
    """Tests for record_crawler_stats method."""

    @pytest.fixture
    def mock_counters(self) -> dict[str, MagicMock]:
        """Create separate mock counters for each metric."""
        return {
            "rate_limit_events": MagicMock(),
            "orgs_processed": MagicMock(),
            "repos_processed": MagicMock(),
            "prs_upserted": MagicMock(),
            "api_calls": MagicMock(),
        }

    @pytest.fixture
    def mock_meter(self, mock_counters: dict[str, MagicMock]) -> MagicMock:
        """Create a mock meter that returns different counters."""
        meter = MagicMock()

        def create_counter_side_effect(name: str, **kwargs: str) -> MagicMock:
            if "rate_limit" in name:
                return mock_counters["rate_limit_events"]
            elif "orgs_processed" in name:
                return mock_counters["orgs_processed"]
            elif "repos_processed" in name:
                return mock_counters["repos_processed"]
            elif "prs_upserted" in name:
                return mock_counters["prs_upserted"]
            elif "api_calls" in name:
                return mock_counters["api_calls"]
            return MagicMock()

        meter.create_counter = MagicMock(side_effect=create_counter_side_effect)
        return meter

    @pytest.fixture
    def metrics(
        self, mock_meter: MagicMock, mock_counters: dict[str, MagicMock]
    ) -> GHECacheMetrics:
        """Create GHECacheMetrics instance for testing."""
        return GHECacheMetrics(mock_meter, "historian")

    def test_record_crawler_stats_all_values(
        self,
        metrics: GHECacheMetrics,
        mock_counters: dict[str, MagicMock],
    ) -> None:
        """Test recording crawler stats with all non-zero values."""
        metrics.record_crawler_stats(
            orgs_processed=2,
            repos_processed=10,
            prs_upserted=50,
            api_calls_ghe=15,
            api_calls_matik=30,
        )

        base_attrs = {"service": "historian", "connector_type": "ghe_pr"}

        mock_counters["orgs_processed"].add.assert_called_once_with(2, base_attrs)
        mock_counters["repos_processed"].add.assert_called_once_with(10, base_attrs)
        mock_counters["prs_upserted"].add.assert_called_once_with(50, base_attrs)

        # API calls should be called twice (once for ghe, once for matik)
        assert mock_counters["api_calls"].add.call_count == 2
        mock_counters["api_calls"].add.assert_any_call(
            15, {**base_attrs, "target": "ghe"}
        )
        mock_counters["api_calls"].add.assert_any_call(
            30, {**base_attrs, "target": "matik"}
        )

    def test_record_crawler_stats_skips_zero_values(
        self,
        metrics: GHECacheMetrics,
        mock_counters: dict[str, MagicMock],
    ) -> None:
        """Test that zero values are not recorded."""
        metrics.record_crawler_stats(
            orgs_processed=0,
            repos_processed=0,
            prs_upserted=5,
            api_calls_ghe=0,
            api_calls_matik=0,
        )

        mock_counters["orgs_processed"].add.assert_not_called()
        mock_counters["repos_processed"].add.assert_not_called()
        mock_counters["prs_upserted"].add.assert_called_once()
        mock_counters["api_calls"].add.assert_not_called()

    def test_record_crawler_stats_only_ghe_api_calls(
        self,
        metrics: GHECacheMetrics,
        mock_counters: dict[str, MagicMock],
    ) -> None:
        """Test recording only GHE API calls."""
        metrics.record_crawler_stats(
            orgs_processed=0,
            repos_processed=0,
            prs_upserted=0,
            api_calls_ghe=10,
            api_calls_matik=0,
        )

        mock_counters["api_calls"].add.assert_called_once_with(
            10, {"service": "historian", "connector_type": "ghe_pr", "target": "ghe"}
        )

    def test_record_crawler_stats_only_matik_api_calls(
        self,
        metrics: GHECacheMetrics,
        mock_counters: dict[str, MagicMock],
    ) -> None:
        """Test recording only Matik API calls."""
        metrics.record_crawler_stats(
            orgs_processed=0,
            repos_processed=0,
            prs_upserted=0,
            api_calls_ghe=0,
            api_calls_matik=20,
        )

        mock_counters["api_calls"].add.assert_called_once_with(
            20, {"service": "historian", "connector_type": "ghe_pr", "target": "matik"}
        )


class TestGHECacheMetricsInstrumentDescriptions:
    """Tests for instrument descriptions."""

    def test_counter_descriptions(self) -> None:
        """Test that all counters have descriptions."""
        meter = MagicMock()
        meter.create_counter = MagicMock(return_value=MagicMock())
        GHECacheMetrics(meter, "historian")

        for call_args in meter.create_counter.call_args_list:
            assert "description" in call_args[1]
            assert call_args[1]["description"] != ""

    def test_counter_units(self) -> None:
        """Test that all counters have units."""
        meter = MagicMock()
        meter.create_counter = MagicMock(return_value=MagicMock())
        GHECacheMetrics(meter, "historian")

        for call_args in meter.create_counter.call_args_list:
            assert "unit" in call_args[1]
            assert call_args[1]["unit"] != ""
