"""Unit tests for TelescopeClient."""

from unittest.mock import MagicMock, patch

from common.constants import (
    DEFAULT_OTEL_ENDPOINT,
    DEFAULT_TELESCOPE_TIMEOUT_SECONDS,
    TELESCOPE_ORG_HEADER,
    TELESCOPE_TENANT_LABEL,
)
from common.metrics.telescope import TelescopeClient
from common.models.telescope_config import TelescopeConfig


class TestTelescopeConfig:
    """Test suite for TelescopeConfig model."""

    def test_default_values(self) -> None:
        """Test creating TelescopeConfig with default values."""
        config = TelescopeConfig()
        assert config.enabled is False
        assert config.tenant_id is None
        assert config.push_interval_seconds == 60
        assert config.otel_endpoint == DEFAULT_OTEL_ENDPOINT
        assert config.timeout_seconds == DEFAULT_TELESCOPE_TIMEOUT_SECONDS
        assert config.service_name is None
        assert config.service_version is None
        assert config.environment is None

    def test_custom_values(self) -> None:
        """Test creating TelescopeConfig with custom values."""
        config = TelescopeConfig(
            enabled=True,
            tenant_id="matik-prod",
            push_interval_seconds=30,
            otel_endpoint="http://custom-endpoint:4318/v1/metrics",
            timeout_seconds=60,
            service_name="api",
            service_version="1.2.3",
            environment="production",
        )
        assert config.enabled is True
        assert config.tenant_id == "matik-prod"
        assert config.push_interval_seconds == 30
        assert config.otel_endpoint == "http://custom-endpoint:4318/v1/metrics"
        assert config.timeout_seconds == 60
        assert config.service_name == "api"
        assert config.service_version == "1.2.3"
        assert config.environment == "production"

    def test_none_values_use_defaults(self) -> None:
        """Test that None values are converted to defaults."""
        config = TelescopeConfig(
            enabled=None,
            push_interval_seconds=None,
            otel_endpoint=None,
            timeout_seconds=None,
        )
        assert config.enabled is False
        assert config.push_interval_seconds == 60
        assert config.otel_endpoint == DEFAULT_OTEL_ENDPOINT
        assert config.timeout_seconds == DEFAULT_TELESCOPE_TIMEOUT_SECONDS


class TestTelescopeClient:
    """Test suite for TelescopeClient."""

    def test_disabled_client(self) -> None:
        """Test that disabled client doesn't initialize."""
        config = TelescopeConfig(
            enabled=False,
            tenant_id="test",
            service_name="historian",
        )
        client = TelescopeClient(config)
        client.start()

        assert client.is_enabled is False

    def test_missing_tenant_id(self) -> None:
        """Test that missing tenant ID disables metrics."""
        config = TelescopeConfig(
            enabled=True,
            tenant_id=None,
            service_name="historian",
        )
        client = TelescopeClient(config)
        client.start()

        assert client.is_enabled is False

    def test_missing_service_name(self) -> None:
        """Test that missing service name disables metrics."""
        config = TelescopeConfig(
            enabled=True,
            tenant_id="test-tenant",
            service_name=None,
        )
        client = TelescopeClient(config)
        client.start()

        assert client.is_enabled is False

    @patch("common.metrics.telescope.OTLPMetricExporter")
    @patch("common.metrics.telescope.PeriodicExportingMetricReader")
    @patch("common.metrics.telescope.MeterProvider")
    @patch("common.metrics.telescope.metrics.set_meter_provider")
    def test_start_creates_provider(
        self,
        mock_set_provider: MagicMock,
        mock_meter_provider: MagicMock,
        mock_reader: MagicMock,
        mock_exporter: MagicMock,
    ) -> None:
        """Test that start creates the meter provider correctly."""
        mock_provider_instance = MagicMock()
        mock_meter = MagicMock()
        mock_provider_instance.get_meter.return_value = mock_meter
        mock_meter_provider.return_value = mock_provider_instance

        config = TelescopeConfig(
            enabled=True,
            tenant_id="test-tenant",
            service_name="historian",
            service_version="1.0.0",
            environment="staging",
            push_interval_seconds=10,
            otel_endpoint="http://custom:4318",  # Base URL only, path added by client
            timeout_seconds=45,
        )
        client = TelescopeClient(config)
        client.start()

        # Verify exporter was created with correct config (path appended to base URL)
        mock_exporter.assert_called_once()
        exporter_call = mock_exporter.call_args
        assert exporter_call.kwargs["endpoint"] == "http://custom:4318/v1/metrics"
        assert exporter_call.kwargs["headers"][TELESCOPE_ORG_HEADER] == "test-tenant"
        assert exporter_call.kwargs["timeout"] == 45

        # Verify reader was created with correct interval (seconds * 1000)
        mock_reader.assert_called_once()
        reader_call = mock_reader.call_args
        assert reader_call.kwargs["export_interval_millis"] == 10000

        # Verify provider was set globally
        mock_set_provider.assert_called_once_with(mock_provider_instance)

        assert client.is_enabled is True
        assert client.meter == mock_meter

    def test_shutdown_without_start(self) -> None:
        """Test that shutdown is safe without start."""
        config = TelescopeConfig(
            enabled=False,
            tenant_id="test",
            service_name="historian",
        )
        client = TelescopeClient(config)
        # Should not raise
        client.shutdown()

    @patch("common.metrics.telescope.OTLPMetricExporter")
    @patch("common.metrics.telescope.PeriodicExportingMetricReader")
    @patch("common.metrics.telescope.MeterProvider")
    @patch("common.metrics.telescope.metrics.set_meter_provider")
    def test_shutdown_flushes_then_shuts_down(
        self,
        mock_set_provider: MagicMock,
        mock_meter_provider: MagicMock,
        mock_reader: MagicMock,
        mock_exporter: MagicMock,
    ) -> None:
        """Test that shutdown force-flushes before calling provider shutdown."""
        mock_provider_instance = MagicMock()
        mock_provider_instance.force_flush.return_value = True
        mock_meter_provider.return_value = mock_provider_instance

        config = TelescopeConfig(
            enabled=True,
            tenant_id="test",
            service_name="historian",
        )
        client = TelescopeClient(config)
        client.start()
        client.shutdown()

        mock_provider_instance.force_flush.assert_called_once_with(timeout_millis=15000)
        mock_provider_instance.shutdown.assert_called_once()

    @patch("common.metrics.telescope.OTLPMetricExporter")
    @patch("common.metrics.telescope.PeriodicExportingMetricReader")
    @patch("common.metrics.telescope.MeterProvider")
    @patch("common.metrics.telescope.metrics.set_meter_provider")
    def test_shutdown_logs_warning_when_flush_times_out(
        self,
        mock_set_provider: MagicMock,
        mock_meter_provider: MagicMock,
        mock_reader: MagicMock,
        mock_exporter: MagicMock,
    ) -> None:
        """Test that shutdown logs a warning when the flush times out."""
        mock_provider_instance = MagicMock()
        mock_provider_instance.force_flush.return_value = False  # timeout
        mock_meter_provider.return_value = mock_provider_instance

        config = TelescopeConfig(
            enabled=True,
            tenant_id="test",
            service_name="historian",
        )
        client = TelescopeClient(config)
        client.start()
        # Should not raise even when flush times out
        client.shutdown()

        mock_provider_instance.force_flush.assert_called_once()
        mock_provider_instance.shutdown.assert_called_once()

    def test_force_flush_without_start(self) -> None:
        """Test that force_flush is safe without start."""
        config = TelescopeConfig(
            enabled=False,
            tenant_id="test",
            service_name="historian",
        )
        client = TelescopeClient(config)
        result = client.force_flush()
        assert result is True

    @patch("common.metrics.telescope.OTLPMetricExporter")
    @patch("common.metrics.telescope.PeriodicExportingMetricReader")
    @patch("common.metrics.telescope.MeterProvider")
    @patch("common.metrics.telescope.metrics.set_meter_provider")
    def test_force_flush_calls_provider(
        self,
        mock_set_provider: MagicMock,
        mock_meter_provider: MagicMock,
        mock_reader: MagicMock,
        mock_exporter: MagicMock,
    ) -> None:
        """Test that force_flush calls provider force_flush."""
        mock_provider_instance = MagicMock()
        mock_provider_instance.force_flush.return_value = True
        mock_meter_provider.return_value = mock_provider_instance

        config = TelescopeConfig(
            enabled=True,
            tenant_id="test",
            service_name="historian",
        )
        client = TelescopeClient(config)
        client.start()
        result = client.force_flush(timeout_ms=5000)

        mock_provider_instance.force_flush.assert_called_once_with(timeout_millis=5000)
        assert result is True

    def test_meter_returns_noop_when_not_started(self) -> None:
        """Test that meter returns a valid meter even when not started."""
        config = TelescopeConfig(
            enabled=False,
            tenant_id="test",
            service_name="historian",
        )
        client = TelescopeClient(config)
        # Should not raise, returns no-op meter
        meter = client.meter
        assert meter is not None


class TestConstants:
    """Test constants from common.constants."""

    def test_otel_endpoint(self) -> None:
        """Test default OTEL collector endpoint constant (base URL only)."""
        assert DEFAULT_OTEL_ENDPOINT == "http://127.0.0.1:4318"

    def test_otel_metrics_path(self) -> None:
        """Test OTEL metrics path constant."""
        from common.constants import OTEL_METRICS_PATH

        assert OTEL_METRICS_PATH == "/v1/metrics"

    def test_tenant_label(self) -> None:
        """Test telescope tenant label constant."""
        assert TELESCOPE_TENANT_LABEL == "telescope_tenant_id"

    def test_org_header(self) -> None:
        """Test telescope org header constant."""
        assert TELESCOPE_ORG_HEADER == "X-Scope-OrgID"

    def test_default_timeout(self) -> None:
        """Test default timeout constant."""
        assert DEFAULT_TELESCOPE_TIMEOUT_SECONDS == 30
