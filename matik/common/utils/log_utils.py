"""
Structured logging utilities for Matik services.

Provides consistent, structured logging across all services with:
- JSON output for production, human-readable for local development
- Service and deployment context in all logs
- Namespace-based log organization
- Automatic stack trace capture for errors
- Task ID tracking for async/distributed tracing

Usage:
    # At application startup
    from matik.common.utils import log_utils
    log_utils.configure()

    # In your code - service is auto-extracted from __name__
    logger = log_utils.get_logger(__name__)
    logger.info("processing incidents", count=42, status="active")
    logger.exception("failed to fetch data")  # auto-captures exception traceback

    # With additional context
    logger = log_utils.get_logger(__name__).bind(team="ops")
    logger.info("fetching issues", project="INFRA")

    # Task ID tracking (for async/distributed tracing)
    log_utils.set_task_id()  # generates UUID, or pass existing ID
    logger.info("processing")  # automatically includes task_id

Environment detection:
    Set DEPLOYMENT_ENV to control output format:
    - "local" or unset: Human-readable colored console output
    - "dev", "staging", "prod": Structured JSON output
"""

import contextvars
import json
import logging
import sys
import uuid
from typing import Any

import structlog

from common.utils.env_utils import extract_service

# Module-level configuration state
_deployment_name: str = "unknown"
_configured: bool = False

# Context variable for task ID tracking in async contexts
_task_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "task_id", default=None
)


def generate_task_id(name: str) -> str:
    """
    Generate and set a new task ID for the current async context.

    Call at the start of a task/job to enable tracing across all logs.
    Generates a task ID with format <service>-<uuid>.

    Args:
        name: Logger name, typically __name__ (e.g., "historian.biztech_github.main").
              Used to extract service name for the task ID.

    Returns:
        The generated task ID.

    Examples:
        Generate a new task ID:
            >>> tid = log_utils.generate_task_id(__name__)
            >>> logger.info("processing")  # includes task_id="historian-{uuid}"

        Send task ID to downstream service:
            >>> headers = {"X-Task-ID": log_utils.get_task_id()}
            >>> await client.post(url, headers=headers)
    """
    # Handle __main__ case by inspecting caller
    name = _handle_main(name)

    # Extract service from module path (e.g., "common.historian.main" → "historian")
    service = extract_service(name)

    tid = f"{service}-{uuid.uuid4()}"
    _task_id.set(tid)

    return tid


def set_task_id(task_id: str) -> str:
    """
    Set an existing task ID for the current async context.

    Use this for distributed tracing when receiving a task ID from an upstream service.

    Args:
        task_id: The task ID from an upstream service.

    Returns:
        The task ID that was set.

    Examples:
        Use incoming task ID (for distributed tracing):
            >>> incoming_id = request.headers.get("X-Task-ID")
            >>> tid = log_utils.set_task_id(incoming_id)
            >>> logger.info("processing")  # includes task_id from upstream
    """
    _task_id.set(task_id)
    return task_id


def get_task_id() -> str | None:
    """
    Get the current task ID from context.

    Useful for propagating task ID to downstream services.

    Returns:
        The current task ID, or None if not set.

    Examples:
        Propagate to downstream service:
            >>> headers = {"X-Task-ID": log_utils.get_task_id()}
            >>> await client.post(url, headers=headers)
    """
    return _task_id.get()


def clear_task_id() -> None:
    """
    Clear the task ID from context.

    Call at the end of a task/job for cleanup. Not strictly necessary
    since contextvars are automatically scoped to async tasks.
    """
    _task_id.set(None)


def configure(level: str = "INFO", environment: str = "") -> None:
    """
    Configure structured logging for the application.

    Should be called once at application startup.

    Args:
        level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        environment: Deployment environment from config.common.environment.
                    "local" = human-readable console output
                    Everything else (sandbox, staging, production) = JSON

    Examples:
        Basic configuration (defaults to sandbox):
            >>> import log_utils
            >>> log_utils.configure()

        With environment from config:
            >>> config = load_config("config.yml")
            >>> log_utils.configure(environment=config.common.environment)

        With custom log level:
            >>> log_utils.configure(level="DEBUG", environment="production")
    """
    global _deployment_name, _configured

    # Store environment as-is (local, sandbox, staging, production)
    _deployment_name = environment.lower() if environment else "sandbox"

    # Only "local" is human-readable, everything else is JSON
    is_local = _deployment_name == "local"

    # Processor to add deployment dynamically at log time
    def add_deployment(
        logger: Any, method_name: str, event_dict: structlog.types.EventDict
    ) -> structlog.types.EventDict:
        """Add current deployment to log context dynamically."""
        event_dict["deployment"] = _deployment_name
        return event_dict

    # Processor to add task_id from context (if set)
    def add_task_id(
        logger: Any, method_name: str, event_dict: structlog.types.EventDict
    ) -> structlog.types.EventDict:
        """Add task_id to log context if set in contextvars."""
        tid = _task_id.get()
        if tid is not None:
            event_dict["task_id"] = tid
        return event_dict

    # Processor to reorder fields for consistent output
    def reorder_fields(
        logger: Any, method_name: str, event_dict: structlog.types.EventDict
    ) -> structlog.types.EventDict:
        """Reorder event_dict to put important fields first."""
        # Desired field order (message is renamed from event by EventRenamer)
        field_order = [
            "timestamp",
            "level",
            "deployment",
            "service",
            "task_id",
            "logger",
            "message",
            "filename",
            "lineno",
        ]

        # Build new dict with ordered fields, then remaining fields
        ordered: dict[str, Any] = {}
        for field in field_order:
            if field in event_dict:
                ordered[field] = event_dict[field]

        # Add remaining fields that weren't in field_order
        for key, value in event_dict.items():
            if key not in ordered:
                ordered[key] = value

        return ordered

    # Build processor pipeline - shared between structlog and stdlib logging
    # These run for ALL logs (both structlog and third-party via stdlib)
    shared_processors: list[Any] = [
        # Interpolate %s/%d positional args (e.g. logger.info("x=%d", 42))
        structlog.stdlib.PositionalArgumentsFormatter(),
        # Add log level as a field
        structlog.stdlib.add_log_level,
        # Add timestamp
        structlog.processors.TimeStamper(fmt="iso"),
        # Add deployment dynamically (reads current _deployment_name)
        add_deployment,
        # Add task_id from context (if set)
        add_task_id,
        # Add service and deployment context to all logs
        structlog.processors.CallsiteParameterAdder(
            {
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.LINENO,
            }
        ),
        # Stack trace rendering
        structlog.processors.StackInfoRenderer(),
    ]

    def embed_extra_in_message(
        logger: Any, method_name: str, event_dict: structlog.types.EventDict
    ) -> structlog.types.EventDict:
        """Serialize the full event dict into the message field.

        Kibana only surfaces the message field, so this ensures all structured
        fields (service, deployment, caller kwargs, etc.) are preserved there
        as a JSON string. The outer JSON fields remain unchanged for pod logs.
        """
        event_dict["message"] = json.dumps(event_dict, default=str)
        return event_dict

    # Choose renderer based on environment
    renderer: structlog.types.Processor
    if is_local:
        # Human-readable console output for local development
        # Custom columns to remove default level padding (8 chars for "critical")
        styles = structlog.dev._colorful_styles
        renderer = structlog.dev.ConsoleRenderer(
            colors=True,
            exception_formatter=structlog.dev.plain_traceback,
            columns=[
                structlog.dev.Column(
                    "timestamp",
                    structlog.dev.KeyValueColumnFormatter(
                        key_style=None,
                        value_style=styles.timestamp,
                        reset_style=styles.reset,
                        value_repr=str,
                    ),
                ),
                structlog.dev.Column(
                    "level",
                    structlog.dev.LogLevelColumnFormatter(
                        level_styles={
                            "critical": styles.level_critical,
                            "exception": styles.level_exception,
                            "error": styles.level_error,
                            "warn": styles.level_warn,
                            "warning": styles.level_warn,
                            "info": styles.level_info,
                            "debug": styles.level_debug,
                            "notset": styles.level_notset,
                        },
                        reset_style=styles.reset,
                        width=0,  # No padding
                    ),
                ),
                structlog.dev.Column(
                    "",
                    structlog.dev.KeyValueColumnFormatter(
                        key_style=styles.kv_key,
                        value_style=styles.kv_value,
                        reset_style=styles.reset,
                        value_repr=str,
                    ),
                ),
            ],
        )
    else:
        # Structured JSON for production/cloud environments
        renderer = structlog.processors.JSONRenderer()

    # Configure structlog to use stdlib logging integration
    # wrap_for_formatter prepares the event dict WITHOUT rendering,
    # letting ProcessorFormatter do the final rendering (avoids double-formatting)
    extra_processors = [] if is_local else [embed_extra_in_message]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            structlog.processors.EventRenamer("message"),
            reorder_fields,  # Reorder fields before rendering
            *extra_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging to use structlog formatting
    # This ensures third-party libraries (like Alembic) also use our format
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Create handler with structlog's ProcessorFormatter
    # ProcessorFormatter renders the final output (for both structlog and stdlib logs)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=renderer,
            foreign_pre_chain=[
                *shared_processors,
                structlog.processors.format_exc_info,
                structlog.processors.EventRenamer("message"),
                reorder_fields,
                *extra_processors,
            ],
            keep_exc_info=True,
            keep_stack_info=True,
        )
    )

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """
    Get a logger bound to a specific name.

    Typically called with __name__ to use the module path. Service is automatically
    extracted from the module path (e.g., "historian.biztech_github.main" → service="historian").

    IMPORTANT: Call log_utils.configure() at application startup BEFORE creating loggers.
    If configure() hasn't been called, this will auto-configure with defaults for
    sandbox/testing convenience.

    Args:
        name: Logger name, typically __name__ (e.g., "historian.biztech_github.main").
              If None or "__main__", attempts to determine from caller's module.

    Returns:
        Bound logger with service, deployment, and logger name context

    Examples:
        Production usage (correct):
            >>> # In main.py
            >>> log_utils.configure(environment="production")
            >>> logger = log_utils.get_logger(__name__)
            >>> logger.info("started")  # deployment="production"

        sandbox usage:
            >>> logger = log_utils.get_logger(__name__)  # Auto-configures
            >>> logger.info("processing data", count=42)  # deployment="sandbox"

        With additional context:
            >>> logger = log_utils.get_logger(__name__).bind(user_id="123")
            >>> logger.info("user action completed")

        Service extraction:
            >>> # In historian.biztech_github.main module
            >>> logger = log_utils.get_logger(__name__)
            >>> # Creates logger with service="historian"
    """
    if not _configured:
        # Auto-configure with defaults for sandbox/testing convenience
        # In production, configure() should be called explicitly first
        configure()

    # Handle __main__ case by inspecting caller
    name = _handle_main(name)

    # Extract service from module path (e.g., "common.historian.main" → "historian")
    service = extract_service(name)

    # Note: deployment is added dynamically by the add_deployment processor
    # so it reflects the current _deployment_name value at log time
    return structlog.get_logger().bind(  # type: ignore[no-any-return]
        service=service,
        logger=name,
    )


def _handle_main(name: str | None) -> str:
    """
    Extracts caller name when __name__ is __main__

    Args:
        name: Module name (e.g., "common.utils.log_utils", "historian.biztech_github.main")

    Returns:
        str: Extracted name
    """

    # Handle __main__ case by inspecting caller
    if name is None or name == "__main__":
        import inspect

        frame = inspect.currentframe()
        if frame and frame.f_back:
            caller_module = inspect.getmodule(frame.f_back)
            if caller_module and caller_module.__name__ != "__main__":
                name = caller_module.__name__
            else:
                name = "__main__"
        else:
            name = "__main__"

    return name
