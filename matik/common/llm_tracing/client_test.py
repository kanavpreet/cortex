"""Unit tests for LLMTracingClient."""

from unittest.mock import MagicMock, patch

from common.llm_tracing.client import LLMTracingClient
from common.models.llm_tracing_config import LLMTracingConfig


class TestLLMTracingClient:
    """Test suite for LLMTracingClient."""

    def test_disabled_client(self) -> None:
        """Test that a disabled client doesn't start tracing."""
        config = LLMTracingConfig(enabled=False)
        client = LLMTracingClient(config)
        client.start()

        assert client.is_enabled is False

    @patch("common.llm_tracing.client.OpenAIInstrumentor")
    @patch("common.llm_tracing.client.opentelemetry_setup")
    def test_start_sets_up_tracing_and_instruments_openai(
        self,
        mock_setup: MagicMock,
        mock_instrumentor_cls: MagicMock,
    ) -> None:
        """Test that start configures GenAI Studio tracing and instruments OpenAI."""
        mock_instrumentor = MagicMock()
        mock_instrumentor.is_instrumented_by_opentelemetry = False
        mock_instrumentor_cls.return_value = mock_instrumentor

        config = LLMTracingConfig(enabled=True, braintrust_project_id="matik-prod")
        client = LLMTracingClient(config)
        client.start()

        mock_setup.assert_called_once_with(
            default_bt_project_id="matik-prod",
            attributes=None,
            set_default="GENAI",
            console=False,
        )
        mock_instrumentor.instrument.assert_called_once()
        assert client.is_enabled is True

    @patch("common.llm_tracing.client.OpenAIInstrumentor")
    @patch("common.llm_tracing.client.opentelemetry_setup")
    def test_start_tags_deployment_environment(
        self,
        mock_setup: MagicMock,
        mock_instrumentor_cls: MagicMock,
    ) -> None:
        """The deployment environment is tagged as a resource attribute on all spans."""
        mock_instrumentor = MagicMock()
        mock_instrumentor.is_instrumented_by_opentelemetry = False
        mock_instrumentor_cls.return_value = mock_instrumentor

        config = LLMTracingConfig(enabled=True, environment="production")
        LLMTracingClient(config).start()

        _, kwargs = mock_setup.call_args
        assert kwargs["attributes"] == {
            "deployment.environment": "production",
            "braintrust.tags": ["environment:production"],
        }
        assert kwargs["console"] is False  # console export only for local

    @patch("common.llm_tracing.client.OpenAIInstrumentor")
    @patch("common.llm_tracing.client.opentelemetry_setup")
    def test_console_export_enabled_only_for_local(
        self,
        mock_setup: MagicMock,
        mock_instrumentor_cls: MagicMock,
    ) -> None:
        """Console span export is enabled only when environment is local."""
        mock_instrumentor = MagicMock()
        mock_instrumentor.is_instrumented_by_opentelemetry = False
        mock_instrumentor_cls.return_value = mock_instrumentor

        LLMTracingClient(LLMTracingConfig(enabled=True, environment="local")).start()

        assert mock_setup.call_args.kwargs["console"] is True

    @patch("common.llm_tracing.client.OpenAIInstrumentor")
    @patch("common.llm_tracing.client.opentelemetry_setup")
    def test_start_skips_instrument_when_already_instrumented(
        self,
        mock_setup: MagicMock,
        mock_instrumentor_cls: MagicMock,
    ) -> None:
        """Test that start doesn't double-instrument the OpenAI SDK."""
        mock_instrumentor = MagicMock()
        mock_instrumentor.is_instrumented_by_opentelemetry = True
        mock_instrumentor_cls.return_value = mock_instrumentor

        config = LLMTracingConfig(enabled=True)
        client = LLMTracingClient(config)
        client.start()

        mock_instrumentor.instrument.assert_not_called()
        assert client.is_enabled is True

    def test_shutdown_without_start(self) -> None:
        """Test that shutdown is safe without start."""
        config = LLMTracingConfig(enabled=False)
        client = LLMTracingClient(config)
        # Should not raise
        client.shutdown()

    @patch("common.llm_tracing.client.OpenAIInstrumentor")
    @patch("common.llm_tracing.client.opentelemetry_setup")
    def test_shutdown_uninstruments_openai(
        self,
        mock_setup: MagicMock,
        mock_instrumentor_cls: MagicMock,
    ) -> None:
        """Test that shutdown uninstruments the OpenAI SDK after start."""
        mock_instrumentor = MagicMock()
        mock_instrumentor.is_instrumented_by_opentelemetry = False
        mock_instrumentor_cls.return_value = mock_instrumentor

        config = LLMTracingConfig(enabled=True)
        client = LLMTracingClient(config)
        client.start()
        client.shutdown()

        mock_instrumentor.uninstrument.assert_called_once()

    @patch("common.llm_tracing.client.OpenAIInstrumentor")
    @patch("common.llm_tracing.client.opentelemetry_setup")
    def test_shutdown_logs_warning_on_uninstrument_error(
        self,
        mock_setup: MagicMock,
        mock_instrumentor_cls: MagicMock,
    ) -> None:
        """Test that shutdown doesn't raise when uninstrument fails."""
        mock_instrumentor = MagicMock()
        mock_instrumentor.is_instrumented_by_opentelemetry = False
        mock_instrumentor.uninstrument.side_effect = RuntimeError("boom")
        mock_instrumentor_cls.return_value = mock_instrumentor

        config = LLMTracingConfig(enabled=True)
        client = LLMTracingClient(config)
        client.start()
        # Should not raise
        client.shutdown()
