"""GenAI Studio OpenTelemetry setup for production LLM-call tracing.

Configures the GaiTracer provider (GENAI mode -> Braintrust only) and instruments
the OpenAI SDK that FacadeClient uses, so Facade calls are auto-traced. The same
provider carries the manual Bedrock spans, and every span is tagged with the
deployment environment. This feeds real production LLM calls to Braintrust for
passive monitoring, alongside the offline golden-dataset evals.
"""

from genai_studio.tracking import opentelemetry_setup
from opentelemetry.instrumentation.openai import OpenAIInstrumentor
from opentelemetry.semconv.resource import ResourceAttributes

from common.llm_tracing.content import set_capture_content
from common.models.llm_tracing_config import LLMTracingConfig
from common.utils.log_utils import get_logger

logger = get_logger(__name__)

__all__ = ["LLMTracingClient"]


class LLMTracingClient:
    """Sets up GenAI Studio tracing and instruments the OpenAI SDK.

    Usage:
        client = LLMTracingClient(config.llm_tracing)
        client.start()

        # ... FacadeClient calls are now automatically traced ...

        client.shutdown()
    """

    def __init__(self, config: LLMTracingConfig) -> None:
        """Initialize the tracing client.

        Args:
            config: Tracing configuration from common.models.llm_tracing_config
        """
        self._config = config
        self._started = False

    @property
    def is_enabled(self) -> bool:
        """Check if tracing was started."""
        return self._started

    def start(self) -> None:
        """Set up GenAI Studio tracing and instrument the OpenAI SDK.

        Call this once at application startup, before any Facade calls.
        """
        if not self._config.enabled:
            logger.info("LLM tracing disabled")
            return

        # Also print spans to stdout for local debugging (the Braintrust exporter
        # is often unreachable from a local machine); never in deployed envs.
        console_export = self._config.environment == "local"

        logger.info(
            "Initializing LLM tracing",
            braintrust_project_id=self._config.braintrust_project_id,
            capture_content=bool(self._config.capture_content),
            environment=self._config.environment,
            console_export=console_export,
        )

        # Tag every span (Facade auto-instrumented + Bedrock manual) with the
        # deployment environment and originating service as resource attributes,
        # so traces from local/sandbox/staging/production and from each service
        # are distinguishable in Braintrust with no per-call wiring.
        # deployment.environment / service.name are filterable structured fields;
        # braintrust.tags populates Braintrust's Tags column (unioned per trace).
        resource_attributes: dict[str, object] = {}
        tags: list[str] = []
        if self._config.environment:
            resource_attributes[ResourceAttributes.DEPLOYMENT_ENVIRONMENT] = (
                self._config.environment
            )
            tags.append(f"environment:{self._config.environment}")
        if self._config.service_name:
            resource_attributes[ResourceAttributes.SERVICE_NAME] = (
                self._config.service_name
            )
            tags.append(f"service:{self._config.service_name}")
        if tags:
            resource_attributes["braintrust.tags"] = tags
        opentelemetry_setup(
            default_bt_project_id=self._config.braintrust_project_id,
            attributes=resource_attributes or None,
            set_default="GENAI",
            console=console_export,
        )

        # Drive content capture for both providers from config: the Facade
        # instrumentor and the Bedrock spans both read this toggle.
        set_capture_content(bool(self._config.capture_content))

        if not OpenAIInstrumentor().is_instrumented_by_opentelemetry:
            OpenAIInstrumentor().instrument()

        self._started = True
        logger.info("LLM tracing initialized successfully")

    def shutdown(self) -> None:
        """Uninstrument the OpenAI SDK.

        Call this during application shutdown.
        """
        if not self._started:
            return

        try:
            OpenAIInstrumentor().uninstrument()
            logger.info("LLM tracing shut down")
        except Exception as e:
            logger.warning("Error uninstrumenting OpenAI SDK", error=e)
