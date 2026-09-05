"""Unit tests for McpMetrics."""

from unittest.mock import MagicMock

from common.metrics.mcp_metrics import TOOL_CALL_DURATION_BUCKETS, McpMetrics


class TestMcpMetricsConstants:
    """Test module constants."""

    def test_duration_buckets(self) -> None:
        """Test tool call duration buckets are defined and ascending."""
        assert TOOL_CALL_DURATION_BUCKETS == (
            0.05,
            0.1,
            0.25,
            0.5,
            1.0,
            2.5,
            5.0,
            10.0,
            30.0,
        )
        for i in range(len(TOOL_CALL_DURATION_BUCKETS) - 1):
            assert TOOL_CALL_DURATION_BUCKETS[i] < TOOL_CALL_DURATION_BUCKETS[i + 1]


class TestMcpMetrics:
    """Test suite for McpMetrics."""

    def _create_mock_meter(self) -> MagicMock:
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_gauge.return_value = MagicMock()
        return meter

    def test_init_creates_instruments(self) -> None:
        """Test that init creates all required instruments."""
        meter = self._create_mock_meter()
        McpMetrics(meter)

        counter_names = [c[1]["name"] for c in meter.create_counter.call_args_list]
        assert "matik_mcp_tool_calls_total" in counter_names
        assert "matik_mcp_tool_call_errors_total" in counter_names
        assert "matik_mcp_spec_refreshes_total" in counter_names

        histogram_names = [c[1]["name"] for c in meter.create_histogram.call_args_list]
        assert "matik_mcp_tool_call_duration_seconds" in histogram_names

        gauge_names = [c[1]["name"] for c in meter.create_gauge.call_args_list]
        assert "matik_mcp_active_sessions" in gauge_names

    def test_record_tool_call_success(self) -> None:
        """Test recording a successful tool call increments counter and histogram."""
        meter = self._create_mock_meter()
        tool_calls = MagicMock()
        duration = MagicMock()
        errors = MagicMock()

        meter.create_counter.side_effect = [tool_calls, errors, MagicMock()]
        meter.create_histogram.return_value = duration

        metrics = McpMetrics(meter)
        metrics.record_tool_call("mcp_get_correlation_group", "success", 0.25)

        tool_calls.add.assert_called_once_with(
            1,
            {
                "service": "mcp-server",
                "tool_name": "mcp_get_correlation_group",
                "status": "success",
            },
        )
        duration.record.assert_called_once_with(
            0.25,
            {
                "service": "mcp-server",
                "tool_name": "mcp_get_correlation_group",
                "status": "success",
            },
        )
        errors.add.assert_not_called()

    def test_record_tool_call_error(self) -> None:
        """Test recording a failed tool call increments error counter."""
        meter = self._create_mock_meter()
        tool_calls = MagicMock()
        errors = MagicMock()

        meter.create_counter.side_effect = [tool_calls, errors, MagicMock()]

        metrics = McpMetrics(meter)
        metrics.record_tool_call(
            "mcp_get_ghe_prs", "error", 1.5, error_type="HTTPStatusError"
        )

        tool_calls.add.assert_called_once_with(
            1,
            {
                "service": "mcp-server",
                "tool_name": "mcp_get_ghe_prs",
                "status": "error",
            },
        )
        errors.add.assert_called_once_with(
            1,
            {
                "service": "mcp-server",
                "tool_name": "mcp_get_ghe_prs",
                "error_type": "HTTPStatusError",
            },
        )

    def test_record_tool_call_unknown_tool(self) -> None:
        """Unknown tool: counts the call + error but skips the latency histogram.

        The tool is rejected before any work, so there is no meaningful
        duration — recording a synthetic 0.0 would skew the percentiles.
        """
        meter = self._create_mock_meter()
        tool_calls = MagicMock()
        errors = MagicMock()
        duration = MagicMock()
        meter.create_counter.side_effect = [tool_calls, errors, MagicMock()]
        meter.create_histogram.return_value = duration

        metrics = McpMetrics(meter)
        metrics.record_tool_call("nonexistent_tool", "error", error_type="unknown_tool")

        tool_calls.add.assert_called_once_with(
            1,
            {
                "service": "mcp-server",
                "tool_name": "nonexistent_tool",
                "status": "error",
            },
        )
        errors.add.assert_called_once_with(
            1,
            {
                "service": "mcp-server",
                "tool_name": "nonexistent_tool",
                "error_type": "unknown_tool",
            },
        )
        duration.record.assert_not_called()

    def test_start_tool_call_records_success(self) -> None:
        """Test start_tool_call timing helper records on success."""
        meter = self._create_mock_meter()
        tool_calls = MagicMock()
        meter.create_counter.side_effect = [tool_calls, MagicMock(), MagicMock()]

        metrics = McpMetrics(meter)
        record = metrics.start_tool_call("mcp_get_jira_issues")
        record("success")

        assert tool_calls.add.call_count == 1
        call_attrs = tool_calls.add.call_args[0][1]
        assert call_attrs["tool_name"] == "mcp_get_jira_issues"
        assert call_attrs["status"] == "success"

    def test_start_tool_call_records_error(self) -> None:
        """Test start_tool_call timing helper records error with error_type."""
        meter = self._create_mock_meter()
        errors = MagicMock()
        meter.create_counter.side_effect = [MagicMock(), errors, MagicMock()]

        metrics = McpMetrics(meter)
        record = metrics.start_tool_call("mcp_get_incidentio_incidents")
        record("error", "RuntimeError")

        errors.add.assert_called_once()
        call_attrs = errors.add.call_args[0][1]
        assert call_attrs["error_type"] == "RuntimeError"

    def test_set_active_sessions(self) -> None:
        """Test set_active_sessions sets the gauge with transport label."""
        meter = self._create_mock_meter()
        gauge = MagicMock()
        meter.create_gauge.return_value = gauge

        metrics = McpMetrics(meter)
        metrics.set_active_sessions(3)

        gauge.set.assert_called_once_with(
            3, {"service": "mcp-server", "transport": "sse"}
        )

    def test_set_active_sessions_http_transport(self) -> None:
        """Test set_active_sessions with explicit http transport label."""
        meter = self._create_mock_meter()
        gauge = MagicMock()
        meter.create_gauge.return_value = gauge

        metrics = McpMetrics(meter)
        metrics.set_active_sessions(2, transport="http")

        gauge.set.assert_called_once_with(
            2, {"service": "mcp-server", "transport": "http"}
        )

    def test_record_spec_refresh_success(self) -> None:
        """Test recording a successful spec refresh."""
        meter = self._create_mock_meter()
        spec_counter = MagicMock()
        meter.create_counter.side_effect = [MagicMock(), MagicMock(), spec_counter]

        metrics = McpMetrics(meter)
        metrics.record_spec_refresh("success")

        spec_counter.add.assert_called_once_with(
            1, {"service": "mcp-server", "status": "success"}
        )

    def test_record_spec_refresh_unchanged(self) -> None:
        """Test recording an unchanged spec refresh."""
        meter = self._create_mock_meter()
        spec_counter = MagicMock()
        meter.create_counter.side_effect = [MagicMock(), MagicMock(), spec_counter]

        metrics = McpMetrics(meter)
        metrics.record_spec_refresh("unchanged")

        spec_counter.add.assert_called_once_with(
            1, {"service": "mcp-server", "status": "unchanged"}
        )

    def test_record_spec_refresh_error(self) -> None:
        """Test recording a failed spec refresh."""
        meter = self._create_mock_meter()
        spec_counter = MagicMock()
        meter.create_counter.side_effect = [MagicMock(), MagicMock(), spec_counter]

        metrics = McpMetrics(meter)
        metrics.record_spec_refresh("error")

        spec_counter.add.assert_called_once_with(
            1, {"service": "mcp-server", "status": "error"}
        )

    def test_custom_service_name(self) -> None:
        """Test custom service name is used in all metric attributes."""
        meter = self._create_mock_meter()
        tool_calls = MagicMock()
        meter.create_counter.side_effect = [tool_calls, MagicMock(), MagicMock()]

        metrics = McpMetrics(meter, service="mcp-server-test")
        metrics.record_tool_call("my_tool", "success", 0.1)

        call_attrs = tool_calls.add.call_args[0][1]
        assert call_attrs["service"] == "mcp-server-test"
