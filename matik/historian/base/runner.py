"""Shared historian entry-point shell.

``run_historian`` is a lean shell that runs the pieces every historian ``main()``
shares — load the base config and merge the per-source / metrics / enricher
configs, configure logging, enforce the source / API / enricher hard-fails, build
the Telescope client (exposing the ``meter`` + service name), invoke the source's
``build_and_run`` callback, map its return code, and shut Telescope down on the
way out.

Everything that genuinely varies per source — which clients and publishers to
build (sync vs async enrichment, extra cache metrics, artifactory), how many jobs
to start and with what labels, and how to run the crawler — lives in the source's
``build_and_run(ctx)`` callback, which receives a :class:`HistorianContext` with
the validated config and the Telescope ``meter`` / ``service_name``. Sources that
fit the streaming shape run a :class:`~historian.base.crawler.BaseCrawler`
subclass and call its ``dispatch()``; sources that don't (e.g. GHE's fan-out,
jira's synchronous two-job crawl) run their own dispatch, but every source's
crawler exposes the same ``dispatch() -> CrawlerResult`` contract.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from common.config import load_config, merge_config
from common.datasources.registry import DataSourceSpec
from common.metrics import TelescopeClient
from common.utils import log_utils
from common.utils.env_utils import extract_full_service, extract_service

logger = log_utils.get_logger(__name__)

# The base historian config every source starts from.
BASE_CONFIG_FILE = "matik-historian-config.yml"


@dataclass
class HistorianContext:
    """Everything a source's ``build_and_run`` callback needs from the shell.

    The shell has already loaded + merged config, configured logging, validated
    the source / API / enricher config, and (if enabled) started Telescope. The
    callback builds its own clients, publishers, and metric objects from these
    primitives and runs its crawler.
    """

    config: Any
    """The fully-merged config object."""

    source_config: Any
    """``getattr(config, spec.source_type)`` — validated non-None by the shell."""

    meter: Any | None
    """Telescope meter, or ``None`` when Telescope is disabled. Build metric
    objects (``ClientMetrics`` / ``JobMetrics`` / source cache metrics) from this."""

    service_name: str | None
    """``extract_service(caller_name)`` when Telescope is enabled, else ``None``."""


def run_historian(
    *,
    spec: DataSourceSpec,
    caller_name: str,
    source_config_files: list[tuple[str, str]],
    build_and_run: Callable[[HistorianContext], int],
    source_config_attr: str | None = None,
    source_config_missing_msg: str = "Source configuration not found",
    crash_log_msg: str = "Historian crawler failed with unexpected error",
) -> int:
    """Run a historian with the shared entry-point shell.

    Args:
        spec: The source's ``DataSourceSpec``. Only ``source_type`` is read — to
            locate the source's config section (``getattr(config, source_type)``)
            and as the client-metrics label a callback typically uses.
        caller_name: The source main module's ``__name__``, used for Telescope
            service-name derivation (``extract_full_service`` / ``extract_service``).
        source_config_files: Ordered ``(config_file, failure_log_message)`` pairs
            merged on top of the base config. Each merge failure logs its message
            and returns 1. Should include the per-source, metrics, and enricher
            config files.
        build_and_run: The source callback. Receives a :class:`HistorianContext`
            (validated config + Telescope meter/service name), builds its own
            clients / publishers / metrics, runs its crawler, and returns a process
            exit code (0 success, non-zero failure).
        source_config_attr: The attribute on ``config`` holding the source's config
            section. Defaults to ``spec.source_type``; pass explicitly when they
            differ (e.g. GHE's ``source_type="ghe_pr"`` but config is
            ``config.biztech_github``).
        source_config_missing_msg: Error logged when the source's config section
            is ``None``.
        crash_log_msg: ``logger.exception`` message when ``build_and_run`` raises.

    Returns:
        0 on success, non-zero on failure.
    """
    # Load base historian config.
    try:
        config = load_config(BASE_CONFIG_FILE)
    except Exception:
        logger.exception("Failed to load config")
        return 1

    # Merge the per-source / metrics / enricher configs.
    for config_file, failure_message in source_config_files:
        try:
            config = merge_config(config, config_file)
        except Exception:
            logger.exception(failure_message)
            return 1

    # Configure logging.
    log_utils.configure(
        level=config.common.log_level, environment=config.common.environment
    )
    log_utils.generate_task_id(caller_name)

    logger.info("Historian started", source=spec.source_type)

    # Validate required configuration.
    config_attr = source_config_attr or spec.source_type
    source_config = getattr(config, config_attr, None)
    if source_config is None:
        logger.error(source_config_missing_msg)
        return 1

    if config.api is None:
        logger.error("API configuration not found")
        return 1

    # The Enricher (via SQS) is now the sole LLM enrichment path. Without it,
    # records would be written with no summaries — fail fast instead.
    if config.enricher is None:
        logger.error(
            "Enricher configuration not found; it is required for LLM enrichment"
        )
        return 1

    telescope: TelescopeClient | None = None
    meter: Any | None = None
    service_name: str | None = None

    if config.telescope and config.telescope.enabled:
        config.telescope.service_name = extract_full_service(caller_name)
        config.telescope.environment = config.common.environment

        telescope = TelescopeClient(config.telescope)
        telescope.start()
        meter = telescope.meter
        service_name = extract_service(caller_name)

        logger.info("Telescope metrics initialized")
    else:
        logger.warning(
            "Telescope metrics not initialized. Either config is not available "
            "or metrics collection is disabled."
        )

    ctx = HistorianContext(
        config=config,
        source_config=source_config,
        meter=meter,
        service_name=service_name,
    )

    try:
        return build_and_run(ctx)
    except Exception:
        logger.exception(crash_log_msg)
        return 1
    finally:
        if telescope:
            telescope.shutdown()
            logger.info("Telescope client shut down")
