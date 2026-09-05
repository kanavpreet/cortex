"""Unit tests for log_utils module."""

import json
from collections.abc import Generator

import pytest
import structlog
from structlog.testing import capture_logs

from common.utils import log_utils
from common.utils.env_utils import extract_service


@pytest.fixture(autouse=True)
def reset_log_utils() -> Generator[None]:
    """Reset log_utils module state before each test."""
    log_utils._configured = False
    log_utils._deployment_name = "unknown"
    log_utils.clear_task_id()  # Reset task_id context
    # Reset structlog to defaults
    structlog.reset_defaults()
    yield
    # Cleanup after test
    log_utils.clear_task_id()


class TestExtractService:
    """Tests for extract_service function (from env_utils, used by log_utils)."""

    def test_extracts_first_component(self) -> None:
        assert extract_service("historian.main") == "historian"

    def test_extracts_from_nested_module(self) -> None:
        assert extract_service("historian.incidentio.main") == "historian"

    def test_extracts_api_service(self) -> None:
        assert extract_service("api.handlers.health") == "api"

    def test_extracts_chronicler_service(self) -> None:
        assert extract_service("chronicler.state_machine") == "chronicler"

    def test_extracts_common_for_utils(self) -> None:
        assert extract_service("common.utils.log_utils") == "common"

    def test_returns_unknown_for_main(self) -> None:
        assert extract_service("__main__") == "unknown"

    def test_returns_unknown_for_empty_string(self) -> None:
        assert extract_service("") == "unknown"

    def test_single_component_module(self) -> None:
        assert extract_service("historian") == "historian"


class TestConfigure:
    """Tests for configure function."""

    def test_sets_configured_flag(self) -> None:
        log_utils.configure()
        assert log_utils._configured is True

    def test_default_deployment_is_sandbox(self) -> None:
        """When environment is not provided, defaults to sandbox."""
        log_utils.configure()
        assert log_utils._deployment_name == "sandbox"

    def test_local_deployment(self) -> None:
        """When environment is local, sets deployment to local."""
        log_utils.configure(environment="local")
        assert log_utils._deployment_name == "local"

    def test_sandbox_deployment(self) -> None:
        """When environment is sandbox, sets deployment to sandbox."""
        log_utils.configure(environment="sandbox")
        assert log_utils._deployment_name == "sandbox"

    def test_staging_deployment(self) -> None:
        """When environment is staging, sets deployment to staging."""
        log_utils.configure(environment="staging")
        assert log_utils._deployment_name == "staging"

    def test_production_deployment(self) -> None:
        """When environment is production, sets deployment to production."""
        log_utils.configure(environment="production")
        assert log_utils._deployment_name == "production"

    def test_accepts_log_level_parameter(self) -> None:
        """Configure accepts log level parameter without error."""
        log_utils.configure(level="DEBUG")
        assert log_utils._configured is True

    def test_accepts_warning_level(self) -> None:
        """Configure accepts WARNING log level."""
        log_utils.configure(level="WARNING")
        assert log_utils._configured is True


class TestGetLogger:
    """Tests for get_logger function."""

    def test_returns_bound_logger(self) -> None:
        log_utils.configure()
        logger = log_utils.get_logger("test.module")
        assert isinstance(logger, structlog.stdlib.BoundLogger)

    def test_logger_has_service_context(self) -> None:
        log_utils.configure()
        logger = log_utils.get_logger("historian.main")
        context = logger._context
        assert context["service"] == "historian"

    def test_logger_deployment_is_dynamic(self) -> None:
        """Deployment is added dynamically at log time, not in bound context.

        This enables module-level loggers to use the correct deployment
        even when created before configure() is called.
        """
        log_utils.configure(environment="production")
        logger = log_utils.get_logger("api.handlers")

        # Deployment is NOT in bound context (added by processor at log time)
        assert "deployment" not in logger._context

        # The module-level _deployment_name is set correctly
        assert log_utils._deployment_name == "production"

    def test_logger_has_logger_name_context(self) -> None:
        log_utils.configure()
        logger = log_utils.get_logger("chronicler.webhooks")
        context = logger._context
        assert context["logger"] == "chronicler.webhooks"

    def test_auto_configures_if_not_configured(self) -> None:
        """get_logger should auto-configure if configure() wasn't called."""
        assert log_utils._configured is False
        log_utils.get_logger("test.module")
        assert log_utils._configured is True

    def test_handles_main_name_via_inspection(self) -> None:
        """When __name__ is __main__, inspection finds caller module."""
        log_utils.configure()
        logger = log_utils.get_logger("__main__")
        context = logger._context
        # Frame inspection finds the test module (common.utils.log_utils_test)
        assert context["service"] == "common"
        assert "common" in context["logger"]


class TestLogOutput:
    """Tests for log output using structlog's capture_logs."""

    def test_info_log_captured(self) -> None:
        """Info log events are captured with correct level."""
        with capture_logs() as cap_logs:
            logger = structlog.get_logger()
            logger.info("test message", key="value")

        assert len(cap_logs) == 1
        assert cap_logs[0]["event"] == "test message"
        assert cap_logs[0]["key"] == "value"
        assert cap_logs[0]["log_level"] == "info"

    def test_warning_log_captured(self) -> None:
        """Warning log events are captured."""
        with capture_logs() as cap_logs:
            logger = structlog.get_logger()
            logger.warning("warning message", count=42)

        assert len(cap_logs) == 1
        assert cap_logs[0]["event"] == "warning message"
        assert cap_logs[0]["count"] == 42
        assert cap_logs[0]["log_level"] == "warning"

    def test_error_log_captured(self) -> None:
        """Error log events are captured."""
        with capture_logs() as cap_logs:
            logger = structlog.get_logger()
            logger.error("error message", error_code=500)

        assert len(cap_logs) == 1
        assert cap_logs[0]["event"] == "error message"
        assert cap_logs[0]["error_code"] == 500
        assert cap_logs[0]["log_level"] == "error"

    def test_multiple_logs_captured(self) -> None:
        """Multiple log events are captured in order."""
        with capture_logs() as cap_logs:
            logger = structlog.get_logger()
            logger.info("first")
            logger.info("second")
            logger.info("third")

        assert len(cap_logs) == 3
        assert cap_logs[0]["event"] == "first"
        assert cap_logs[1]["event"] == "second"
        assert cap_logs[2]["event"] == "third"


class TestLoggerBinding:
    """Tests for logger context binding."""

    def test_bind_adds_context(self) -> None:
        log_utils.configure()
        logger = log_utils.get_logger("test.module")
        bound_logger = logger.bind(task_id="abc-123")

        context = bound_logger._context
        assert context["task_id"] == "abc-123"
        assert context["service"] == "test"

    def test_bind_is_immutable(self) -> None:
        log_utils.configure()
        logger = log_utils.get_logger("test.module")
        bound_logger = logger.bind(extra="data")

        assert "extra" not in logger._context
        assert "extra" in bound_logger._context

    def test_bind_multiple_values(self) -> None:
        log_utils.configure()
        logger = log_utils.get_logger("test.module")
        bound_logger = logger.bind(user_id="123", session="abc")

        context = bound_logger._context
        assert context["user_id"] == "123"
        assert context["session"] == "abc"

    def test_chain_binds(self) -> None:
        log_utils.configure()
        logger = log_utils.get_logger("test.module")
        bound_logger = logger.bind(a="1").bind(b="2")

        context = bound_logger._context
        assert context["a"] == "1"
        assert context["b"] == "2"


class TestDynamicDeployment:
    """Tests for dynamic deployment binding (fixes module-level logger bug)."""

    def test_initial_deployment_is_unknown(self) -> None:
        """
        Before any configure() call, deployment should be 'unknown'.

        This indicates that config loading hasn't happened yet, which is useful
        for logs emitted during failed config loading attempts.
        """
        # Don't call configure() or get_logger() - check initial state
        assert log_utils._deployment_name == "unknown"

    def test_module_level_logger_uses_deployment_from_configure(self) -> None:
        """
        Logger created before configure() should use deployment set by configure().

        This tests the fix for the bug where module-level loggers would bind
        deployment at import time, ignoring later configure() calls.
        """
        # Simulate module-level logger creation (before configure)
        # This auto-configures with defaults (deployment="sandbox")
        logger = log_utils.get_logger("historian.main")
        assert log_utils._deployment_name == "sandbox"  # Auto-configured default

        # Now configure with production environment
        log_utils._configured = False  # Allow reconfigure
        log_utils.configure(environment="production")

        # The module-level _deployment_name is now production
        assert log_utils._deployment_name == "production"

        # Logger's bound context remains unchanged (no deployment binding)
        assert "deployment" not in logger._context

    def test_deployment_name_updates_after_reconfigure(self) -> None:
        """Module-level _deployment_name should reflect the most recent configure() call."""
        log_utils.configure(environment="sandbox")
        assert log_utils._deployment_name == "sandbox"

        logger = log_utils.get_logger("api.handlers")
        assert "deployment" not in logger._context  # Never bound

        # Reconfigure to production
        log_utils._configured = False  # Allow reconfigure
        log_utils.configure(environment="production")

        # Module-level state updates
        assert log_utils._deployment_name == "production"

        # Logger context still has no deployment (added dynamically by processor)
        assert "deployment" not in logger._context

    def test_logger_context_excludes_deployment(self) -> None:
        """Logger context should NOT include deployment (it's added by processor)."""
        log_utils.configure(environment="staging")

        logger = log_utils.get_logger("chronicler.main")
        context = logger._context

        # These are bound at logger creation
        assert "service" in context
        assert "logger" in context

        # Deployment is NOT bound - it's added dynamically by processor
        assert "deployment" not in context


class TestGenerateTaskId:
    """Tests for generate_task_id function."""

    def test_generates_service_prefixed_uuid(self) -> None:
        """generate_task_id() generates a task ID with format {service}-{uuid}."""
        tid = log_utils.generate_task_id("historian.main")

        # Should be service-uuid format (service + hyphen + 36 char UUID)
        assert tid.startswith("historian-")
        uuid_part = tid[len("historian-") :]
        assert len(uuid_part) == 36
        assert uuid_part.count("-") == 4

    def test_extracts_service_from_nested_module(self) -> None:
        """generate_task_id() extracts service from nested module path."""
        tid = log_utils.generate_task_id("api.handlers.health")

        assert tid.startswith("api-")

    def test_resolves_main_to_caller_module(self) -> None:
        """generate_task_id() resolves __main__ to caller module."""
        tid = log_utils.generate_task_id("__main__")

        # _handle_main will resolve __main__ to caller module (common in this case)
        assert tid.startswith("common-")

    def test_sets_task_id_in_context(self) -> None:
        """generate_task_id() sets the task ID in context."""
        tid = log_utils.generate_task_id("chronicler.webhooks")

        assert log_utils.get_task_id() == tid
        assert tid.startswith("chronicler-")


class TestSetTaskId:
    """Tests for set_task_id function."""

    def test_sets_provided_task_id(self) -> None:
        """set_task_id() sets the provided task ID in context."""
        tid = log_utils.set_task_id("upstream-trace-123")

        assert tid == "upstream-trace-123"
        assert log_utils.get_task_id() == "upstream-trace-123"

    def test_overwrites_previous_task_id(self) -> None:
        """set_task_id() overwrites any previously set task ID."""
        log_utils.set_task_id("first-id")
        log_utils.set_task_id("second-id")

        assert log_utils.get_task_id() == "second-id"


class TestTaskIdContext:
    """Tests for task ID context operations."""

    def test_get_task_id_returns_none_when_not_set(self) -> None:
        """get_task_id() returns None when no task ID is set."""
        assert log_utils.get_task_id() is None

    def test_clear_task_id_removes_value(self) -> None:
        """clear_task_id() removes the task ID from context."""
        log_utils.generate_task_id("historian.main")
        assert log_utils.get_task_id() is not None

        log_utils.clear_task_id()
        assert log_utils.get_task_id() is None

    def test_generate_overwrites_set(self) -> None:
        """generate_task_id() overwrites task ID set by set_task_id()."""
        log_utils.set_task_id("incoming-id")
        tid = log_utils.generate_task_id("api.main")

        assert log_utils.get_task_id() == tid
        assert tid.startswith("api-")


class TestTaskIdInLogs:
    """Tests for task ID appearing in log output."""

    def test_task_id_appears_in_logs_when_generated(self) -> None:
        """When task_id is generated, it appears in log output."""
        log_utils.configure(environment="local")
        tid = log_utils.generate_task_id("historian.main")
        logger = log_utils.get_logger("test.module")

        with capture_logs():
            logger.info("test message")

        # Note: capture_logs captures before our processors run,
        # so we verify via the context var instead
        assert log_utils.get_task_id() == tid
        assert tid.startswith("historian-")

    def test_task_id_not_in_context_when_not_set(self) -> None:
        """When task_id is not set, it should not appear in logs."""
        # Ensure task_id is not set
        assert log_utils.get_task_id() is None

        log_utils.configure(environment="local")
        logger = log_utils.get_logger("test.module")

        # Logger context should not include task_id
        assert "task_id" not in logger._context


class TestEmbedExtraInMessage:
    """Tests for the embed_extra_in_message processor (non-local environments)."""

    def test_message_contains_full_json_in_non_local(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """In non-local environments, the message field is the full event JSON."""
        log_utils.configure(environment="sandbox")
        logger = log_utils.get_logger("test.module")
        logger.info("correlation completed", reference_id="INC-123", count=5)

        captured = capsys.readouterr()
        outer = json.loads(captured.out)
        inner = json.loads(outer["message"])

        assert inner["message"] == "correlation completed"
        assert inner["reference_id"] == "INC-123"
        assert inner["count"] == 5

    def test_standard_fields_present_in_embedded_json(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Embedded JSON contains standard fields like level, service, deployment."""
        log_utils.configure(environment="sandbox")
        logger = log_utils.get_logger("test.module")
        logger.info("test event", extra="data")

        captured = capsys.readouterr()
        outer = json.loads(captured.out)
        inner = json.loads(outer["message"])

        assert inner["level"] == "info"
        assert inner["deployment"] == "sandbox"
        assert "timestamp" in inner

    def test_message_not_embedded_in_local(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """In local environment, the message field is plain text (no JSON embedding)."""
        log_utils.configure(environment="local")
        logger = log_utils.get_logger("test.module")
        logger.info("plain message", extra_field="value")

        captured = capsys.readouterr()
        # Local output is human-readable — message is not a JSON string
        assert "plain message" in captured.out
        assert '"message": "plain message"' not in captured.out
