"""Telescope metrics client using OpenTelemetry.

Provides OTLP HTTP export to local otel-collector sidecar which forwards
metrics to Telescope.
"""

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from common.constants import (
    OTEL_METRICS_PATH,
    TELESCOPE_ORG_HEADER,
    TELESCOPE_TENANT_LABEL,
)
from common.models.telescope_config import TelescopeConfig
from common.utils.log_utils import get_logger

logger = get_logger(__name__)

# Re-export TelescopeConfig for convenience
__all__ = ["TelescopeClient", "TelescopeConfig"]


class TelescopeClient:
    """OpenTelemetry meter provider wrapper for Telescope integration.

    Metrics are sent to the local otel-collector sidecar which forwards
    them to Telescope with proper routing.

    Usage:
        # Create config (typically from MatikConfig.telescope)
        config = TelescopeConfig(
            enabled=True,
            tenant_id="matik-production",
            service_name="historian",
            environment="production",
        )

        # Initialize and start client
        client = TelescopeClient(config)
        client.start()

        # Create metrics using the meter
        counter = client.meter.create_counter("my_counter")

        # Shutdown on application exit
        client.shutdown()
    """

    def __init__(self, config: TelescopeConfig) -> None:
        """Initialize Telescope client.

        Args:
            config: Telescope configuration from common.models.telescope_config
        """
        self._config = config
        self._meter_provider: MeterProvider | None = None
        self._meter: metrics.Meter | None = None

    @property
    def meter(self) -> metrics.Meter:
        """Get the OpenTelemetry meter for creating instruments.

        Returns:
            The configured meter, or a no-op meter if not initialized.
        """
        if self._meter is None:
            # Return a no-op meter if not initialized
            tenant_id = self._config.tenant_id or "unknown"
            return metrics.get_meter(tenant_id)
        return self._meter

    @property
    def is_enabled(self) -> bool:
        """Check if the Telescope client is enabled and initialized."""
        return self._meter_provider is not None

    def start(self) -> None:
        """Initialize and start the metrics provider.

        Creates the OTLP HTTP exporter and configures the meter provider.
        Call this at application startup.
        """
        if not self._config.enabled:
            logger.info("Telescope metrics disabled")
            return

        if not self._config.tenant_id:
            logger.warning("Telescope tenant ID not configured, metrics disabled")
            return

        if not self._config.service_name:
            logger.warning("Telescope service name not configured, metrics disabled")
            return

        # Convert seconds to milliseconds for the reader
        push_interval_ms = (self._config.push_interval_seconds or 60) * 1000

        # Construct full endpoint URL from base URL and metrics path
        base_url = (self._config.otel_endpoint or "").rstrip("/")
        full_endpoint = f"{base_url}{OTEL_METRICS_PATH}"

        logger.info(
            "Initializing Telescope metrics client",
            tenant_id=self._config.tenant_id,
            service_name=self._config.service_name,
            otel_endpoint=full_endpoint,
            push_interval_seconds=self._config.push_interval_seconds,
        )

        # Build resource attributes following Airbnb patterns
        resource_attrs: dict[str, str | bool] = {
            TELESCOPE_TENANT_LABEL: self._config.tenant_id,
            "service.name": self._config.service_name,
            "keep_raw": True,
        }
        if self._config.service_version:
            resource_attrs["service.version"] = self._config.service_version
        if self._config.environment:
            resource_attrs["deployment.environment"] = self._config.environment

        resource = Resource.create(resource_attrs)

        # Create OTLP HTTP exporter to otel-collector sidecar
        exporter = OTLPMetricExporter(
            endpoint=full_endpoint,
            headers={TELESCOPE_ORG_HEADER: self._config.tenant_id},
            timeout=self._config.timeout_seconds,
        )

        # Create periodic reader
        reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=push_interval_ms,
        )

        # Create meter provider
        self._meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[reader],
        )

        # Set as global provider
        metrics.set_meter_provider(self._meter_provider)

        # Get meter for this tenant
        self._meter = self._meter_provider.get_meter(self._config.tenant_id)

        logger.info("Telescope metrics client initialized successfully")

    def shutdown(self) -> None:
        """Gracefully shut down the meter provider, flushing pending metrics.

        Explicitly force-flushes before shutdown to ensure metrics emitted
        by short-lived services (e.g., cron jobs) are exported before the
        process exits. The flush is attempted with a 15 s timeout; a warning
        is logged if it does not complete in time.

        Call this during application shutdown.
        """
        if self._meter_provider is None:
            return

        logger.info("Shutting down Telescope metrics client")

        # Explicit flush before shutdown so metrics from short-lived processes
        # (cron jobs) are exported even if they finish within one push interval.
        flushed = self.force_flush(timeout_ms=15000)
        if not flushed:
            logger.warning(
                "Telescope metrics flush timed out — some metrics may not have been exported"
            )
        else:
            logger.info("Telescope metrics flushed successfully")

        try:
            self._meter_provider.shutdown()
            logger.info("Telescope metrics client shutdown complete")
        except Exception as e:
            logger.exception("Error shutting down Telescope metrics client", error=e)
            raise

    def force_flush(self, timeout_ms: int = 10000) -> bool:
        """Force a flush of any pending metrics.

        Args:
            timeout_ms: Maximum time to wait for flush in milliseconds

        Returns:
            True if flush was successful, False otherwise.
        """
        if self._meter_provider is None:
            return True

        return self._meter_provider.force_flush(timeout_millis=timeout_ms)
