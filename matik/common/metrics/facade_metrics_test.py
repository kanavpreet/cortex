"""Unit tests for FacadeMetrics."""

import time
from unittest.mock import MagicMock

from common.metrics.facade_metrics import LLM_LATENCY_BUCKETS, FacadeMetrics


class TestFacadeMetricsConstants:
    """Test module constants."""

    def test_latency_buckets(self) -> None:
        """Test LLM latency buckets are defined correctly."""
        assert LLM_LATENCY_BUCKETS == (
            0.5,
            1.0,
            2.0,
            5.0,
            10.0,
            20.0,
            30.0,
            60.0,
            120.0,
        )
        # Verify buckets are in ascending order
        for i in range(len(LLM_LATENCY_BUCKETS) - 1):
            assert LLM_LATENCY_BUCKETS[i] < LLM_LATENCY_BUCKETS[i + 1]


class TestFacadeMetrics:
    """Test suite for FacadeMetrics."""

    def _create_mock_meter(self) -> MagicMock:
        """Create a mock meter with mock instruments."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_gauge.return_value = MagicMock()
        return meter

    def test_init_creates_instruments(self) -> None:
        """Test that init creates all required instruments."""
        meter = self._create_mock_meter()
        FacadeMetrics(meter, "historian-incidentio", "facade")

        # Verify counters created
        counter_calls = [c[1]["name"] for c in meter.create_counter.call_args_list]
        assert "matik_facade_calls_total" in counter_calls
        assert "matik_facade_prompt_tokens_total" in counter_calls
        assert "matik_facade_completion_tokens_total" in counter_calls
        assert "matik_facade_call_errors_total" in counter_calls

        # Verify histogram created
        histogram_calls = [c[1]["name"] for c in meter.create_histogram.call_args_list]
        assert "matik_facade_call_duration_seconds" in histogram_calls

        # Verify gauges created
        gauge_calls = [c[1]["name"] for c in meter.create_gauge.call_args_list]
        assert "matik_facade_ratelimit_limit_requests" in gauge_calls
        assert "matik_facade_ratelimit_remaining_requests" in gauge_calls
        assert "matik_facade_ratelimit_limit_tokens" in gauge_calls
        assert "matik_facade_ratelimit_remaining_tokens" in gauge_calls

    def test_record_call_success(self) -> None:
        """Test recording a successful call with token usage."""
        meter = self._create_mock_meter()
        call_count = MagicMock()
        prompt_tokens = MagicMock()
        completion_tokens = MagicMock()
        call_errors = MagicMock()
        call_duration = MagicMock()

        meter.create_counter.side_effect = [
            call_count,
            prompt_tokens,
            completion_tokens,
            call_errors,
        ]
        meter.create_histogram.return_value = call_duration

        metrics = FacadeMetrics(meter, "historian-incidentio", "facade")
        metrics.record_call(
            model="gpt-4o",
            operation="summarize_root_cause",
            prompt_tokens=500,
            completion_tokens=100,
            duration_seconds=2.5,
        )

        # Verify call count incremented
        call_count.add.assert_called_once()
        call_args = call_count.add.call_args
        assert call_args[0][0] == 1
        attrs = call_args[0][1]
        assert attrs["service"] == "historian-incidentio"
        assert attrs["model"] == "gpt-4o"
        assert attrs["operation"] == "summarize_root_cause"

        # Verify duration recorded
        call_duration.record.assert_called_once()
        assert call_duration.record.call_args[0][0] == 2.5

        # Verify tokens recorded
        prompt_tokens.add.assert_called_once_with(500, attrs)
        completion_tokens.add.assert_called_once_with(100, attrs)

        # Verify errors not incremented
        call_errors.add.assert_not_called()

    def test_record_call_with_error(self) -> None:
        """Test recording a call with error flag."""
        meter = self._create_mock_meter()
        call_count = MagicMock()
        prompt_tokens = MagicMock()
        completion_tokens = MagicMock()
        call_errors = MagicMock()

        meter.create_counter.side_effect = [
            call_count,
            prompt_tokens,
            completion_tokens,
            call_errors,
        ]

        metrics = FacadeMetrics(meter, "historian-incidentio", "facade")
        metrics.record_call(
            model="gpt-4o",
            operation="summarize_resolution",
            error=True,
        )

        # Verify error count incremented
        call_errors.add.assert_called_once()
        call_args = call_errors.add.call_args
        assert call_args[0][0] == 1
        attrs = call_args[0][1]
        assert attrs["service"] == "historian-incidentio"
        assert attrs["model"] == "gpt-4o"
        assert attrs["operation"] == "summarize_resolution"

    def test_record_call_zero_tokens_not_recorded(self) -> None:
        """Test that zero tokens are not recorded."""
        meter = self._create_mock_meter()
        call_count = MagicMock()
        prompt_tokens = MagicMock()
        completion_tokens = MagicMock()
        call_errors = MagicMock()

        meter.create_counter.side_effect = [
            call_count,
            prompt_tokens,
            completion_tokens,
            call_errors,
        ]

        metrics = FacadeMetrics(meter, "historian-incidentio", "facade")
        metrics.record_call(
            model="gpt-4o",
            operation="test_operation",
            prompt_tokens=0,
            completion_tokens=0,
            duration_seconds=1.0,
        )

        # Verify tokens NOT recorded when zero
        prompt_tokens.add.assert_not_called()
        completion_tokens.add.assert_not_called()

    def test_record_call_only_prompt_tokens(self) -> None:
        """Test recording when only prompt tokens are provided."""
        meter = self._create_mock_meter()
        call_count = MagicMock()
        prompt_tokens = MagicMock()
        completion_tokens = MagicMock()
        call_errors = MagicMock()

        meter.create_counter.side_effect = [
            call_count,
            prompt_tokens,
            completion_tokens,
            call_errors,
        ]

        metrics = FacadeMetrics(meter, "historian-incidentio", "facade")
        metrics.record_call(
            model="gpt-4o",
            operation="test",
            prompt_tokens=100,
            completion_tokens=0,
        )

        prompt_tokens.add.assert_called_once()
        completion_tokens.add.assert_not_called()

    def test_record_call_only_completion_tokens(self) -> None:
        """Test recording when only completion tokens are provided."""
        meter = self._create_mock_meter()
        call_count = MagicMock()
        prompt_tokens = MagicMock()
        completion_tokens = MagicMock()
        call_errors = MagicMock()

        meter.create_counter.side_effect = [
            call_count,
            prompt_tokens,
            completion_tokens,
            call_errors,
        ]

        metrics = FacadeMetrics(meter, "historian-incidentio", "facade")
        metrics.record_call(
            model="gpt-4o",
            operation="test",
            prompt_tokens=0,
            completion_tokens=50,
        )

        prompt_tokens.add.assert_not_called()
        completion_tokens.add.assert_called_once()

    def test_start_call_timing(self) -> None:
        """Test that start_call measures duration correctly."""
        meter = self._create_mock_meter()
        call_duration = MagicMock()
        meter.create_histogram.return_value = call_duration

        metrics = FacadeMetrics(meter, "historian-incidentio", "facade")

        # Start call
        record = metrics.start_call("gpt-4o", "summarize_root_cause")

        # Simulate some work
        time.sleep(0.05)

        # Record completion
        record(500, 100, False)

        # Verify duration is recorded and is at least 50ms
        call_duration.record.assert_called_once()
        recorded_duration = call_duration.record.call_args[0][0]
        assert recorded_duration >= 0.05

    def test_start_call_with_error(self) -> None:
        """Test start_call records errors correctly."""
        meter = self._create_mock_meter()
        call_count = MagicMock()
        prompt_tokens = MagicMock()
        completion_tokens = MagicMock()
        call_errors = MagicMock()

        meter.create_counter.side_effect = [
            call_count,
            prompt_tokens,
            completion_tokens,
            call_errors,
        ]

        metrics = FacadeMetrics(meter, "historian-incidentio", "facade")
        record = metrics.start_call("gpt-4o", "summarize_resolution")
        record(0, 0, True)

        call_errors.add.assert_called_once()

    def test_start_call_with_tokens(self) -> None:
        """Test start_call records tokens correctly."""
        meter = self._create_mock_meter()
        call_count = MagicMock()
        prompt_tokens = MagicMock()
        completion_tokens = MagicMock()
        call_errors = MagicMock()

        meter.create_counter.side_effect = [
            call_count,
            prompt_tokens,
            completion_tokens,
            call_errors,
        ]

        metrics = FacadeMetrics(meter, "historian-incidentio", "facade")
        record = metrics.start_call("gpt-4o", "summarize_root_cause")
        record(500, 100, False)

        # Verify tokens recorded
        prompt_tokens.add.assert_called_once()
        assert prompt_tokens.add.call_args[0][0] == 500
        completion_tokens.add.assert_called_once()
        assert completion_tokens.add.call_args[0][0] == 100

    def test_start_call_default_parameters(self) -> None:
        """Test start_call record function default parameters."""
        meter = self._create_mock_meter()
        call_count = MagicMock()
        prompt_tokens = MagicMock()
        completion_tokens = MagicMock()
        call_errors = MagicMock()

        meter.create_counter.side_effect = [
            call_count,
            prompt_tokens,
            completion_tokens,
            call_errors,
        ]

        metrics = FacadeMetrics(meter, "historian-incidentio", "facade")
        record = metrics.start_call("gpt-4o", "test")

        # Call with defaults (no tokens, no error)
        record(0, 0, False)

        # Verify no tokens recorded
        prompt_tokens.add.assert_not_called()
        completion_tokens.add.assert_not_called()
        # Verify no error recorded
        call_errors.add.assert_not_called()


class TestFacadeMetricsInstrumentDescriptions:
    """Test instrument descriptions and units."""

    def test_counter_descriptions(self) -> None:
        """Test that counters have proper descriptions."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_gauge.return_value = MagicMock()

        FacadeMetrics(meter, "historian-incidentio", "facade")

        counter_calls = {
            c[1]["name"]: c[1] for c in meter.create_counter.call_args_list
        }

        assert (
            "Total number of Facade/LLM API calls"
            in counter_calls["matik_facade_calls_total"]["description"]
        )
        assert counter_calls["matik_facade_calls_total"]["unit"] == "{call}"

        assert (
            "Total prompt tokens used"
            in counter_calls["matik_facade_prompt_tokens_total"]["description"]
        )
        assert counter_calls["matik_facade_prompt_tokens_total"]["unit"] == "{token}"

        assert (
            "Total completion tokens used"
            in counter_calls["matik_facade_completion_tokens_total"]["description"]
        )
        assert (
            counter_calls["matik_facade_completion_tokens_total"]["unit"] == "{token}"
        )

        assert (
            "Total number of Facade/LLM API call errors"
            in counter_calls["matik_facade_call_errors_total"]["description"]
        )
        assert counter_calls["matik_facade_call_errors_total"]["unit"] == "{error}"

    def test_histogram_description(self) -> None:
        """Test that histogram has proper description."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_gauge.return_value = MagicMock()

        FacadeMetrics(meter, "historian-incidentio", "facade")

        histogram_call = meter.create_histogram.call_args[1]
        assert histogram_call["name"] == "matik_facade_call_duration_seconds"
        assert "call duration" in histogram_call["description"].lower()
        assert histogram_call["unit"] == "s"
        assert (
            histogram_call["explicit_bucket_boundaries_advisory"] == LLM_LATENCY_BUCKETS
        )

    def test_gauge_descriptions(self) -> None:
        """Test that rate limit gauges have proper descriptions."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_gauge.return_value = MagicMock()

        FacadeMetrics(meter, "historian-incidentio", "facade")

        gauge_calls = {c[1]["name"]: c[1] for c in meter.create_gauge.call_args_list}

        assert "matik_facade_ratelimit_limit_requests" in gauge_calls
        assert "matik_facade_ratelimit_remaining_requests" in gauge_calls
        assert "matik_facade_ratelimit_limit_tokens" in gauge_calls
        assert "matik_facade_ratelimit_remaining_tokens" in gauge_calls

        assert (
            gauge_calls["matik_facade_ratelimit_limit_requests"]["unit"] == "{request}"
        )
        assert (
            gauge_calls["matik_facade_ratelimit_remaining_tokens"]["unit"] == "{token}"
        )


class TestRecordRateLimits:
    """Test suite for FacadeMetrics.record_rate_limits."""

    def _create_metrics_with_gauges(self) -> tuple[FacadeMetrics, dict[str, MagicMock]]:
        """Create FacadeMetrics with individually tracked gauge mocks."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()

        gauges: dict[str, MagicMock] = {}

        def create_gauge_side_effect(**kwargs: str) -> MagicMock:
            mock = MagicMock()
            gauges[kwargs["name"]] = mock
            return mock

        meter.create_gauge.side_effect = create_gauge_side_effect

        metrics = FacadeMetrics(meter, "historian-incidentio", "facade")
        return metrics, gauges

    def test_record_rate_limits_all_headers(self) -> None:
        """Test recording all four rate limit headers."""
        metrics, gauges = self._create_metrics_with_gauges()

        headers = {
            "x-ratelimit-limit-requests": "5000",
            "x-ratelimit-remaining-requests": "4994",
            "x-ratelimit-limit-tokens": "5000000",
            "x-ratelimit-remaining-tokens": "4993281",
        }

        metrics.record_rate_limits(headers, "gpt-4o", "summarize_root_cause")

        expected_attrs = {
            "service": "historian-incidentio",
            "client": "facade",
            "model": "gpt-4o",
            "operation": "summarize_root_cause",
        }

        gauges["matik_facade_ratelimit_limit_requests"].set.assert_called_once_with(
            5000, expected_attrs
        )
        gauges["matik_facade_ratelimit_remaining_requests"].set.assert_called_once_with(
            4994, expected_attrs
        )
        gauges["matik_facade_ratelimit_limit_tokens"].set.assert_called_once_with(
            5000000, expected_attrs
        )
        gauges["matik_facade_ratelimit_remaining_tokens"].set.assert_called_once_with(
            4993281, expected_attrs
        )

    def test_record_rate_limits_missing_headers(self) -> None:
        """Test that missing headers are silently ignored."""
        metrics, gauges = self._create_metrics_with_gauges()

        headers: dict[str, str] = {}

        metrics.record_rate_limits(headers, "gpt-4o", "test")

        for gauge in gauges.values():
            gauge.set.assert_not_called()

    def test_record_rate_limits_partial_headers(self) -> None:
        """Test that only present headers are recorded."""
        metrics, gauges = self._create_metrics_with_gauges()

        headers = {
            "x-ratelimit-remaining-requests": "4994",
            "x-ratelimit-remaining-tokens": "4993281",
        }

        metrics.record_rate_limits(headers, "gpt-4o", "test")

        expected_attrs = {
            "service": "historian-incidentio",
            "client": "facade",
            "model": "gpt-4o",
            "operation": "test",
        }

        gauges["matik_facade_ratelimit_limit_requests"].set.assert_not_called()
        gauges["matik_facade_ratelimit_remaining_requests"].set.assert_called_once_with(
            4994, expected_attrs
        )
        gauges["matik_facade_ratelimit_limit_tokens"].set.assert_not_called()
        gauges["matik_facade_ratelimit_remaining_tokens"].set.assert_called_once_with(
            4993281, expected_attrs
        )

    def test_record_rate_limits_non_numeric_values(self) -> None:
        """Test that non-numeric header values are silently ignored."""
        metrics, gauges = self._create_metrics_with_gauges()

        headers = {
            "x-ratelimit-limit-requests": "not-a-number",
            "x-ratelimit-remaining-requests": "4994",
        }

        metrics.record_rate_limits(headers, "gpt-4o", "test")

        gauges["matik_facade_ratelimit_limit_requests"].set.assert_not_called()
        gauges["matik_facade_ratelimit_remaining_requests"].set.assert_called_once()
