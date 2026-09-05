"""Root-cause-coverage audit entry point.

Manual config/logging/Telescope/tracing shell, mirroring
``enigmatologist/main.py`` — NOT ``historian.base.runner.run_historian``, which
hard-fails without an enricher config, an invariant that doesn't apply here.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from opentelemetry import trace

from audit.root_cause_coverage import SPEC
from audit.root_cause_coverage.pipeline import (
    CorrelationsSheetsClient,
    RootCauseAuditContext,
)
from audit.root_cause_coverage.sources import all_root_cause_sources
from audit.sheet_sync import SheetsClientProtocol, run_sheet_sync
from common.clients import create_bedrock_client_sync, create_facade_client_sync
from common.clients.braintrust_client import BraintrustClient
from common.clients.ghe_client import create_ghe_client
from common.clients.incidentio_client import create_incidentio_client_sync
from common.clients.jira_client import create_jira_client
from common.clients.sheets_client import SheetsClient
from common.config import load_config, merge_config
from common.daos.ghe_pr_dao import GHEPRDAO
from common.daos.incidentio_incident_dao import IncidentIOIncidentDAO
from common.daos.reliability_correlation_dao import ReliabilityCorrelationDAO
from common.llm_tracing.client import LLMTracingClient
from common.metrics.telescope import TelescopeClient
from common.models.sheets_config import SheetsConfig
from common.utils import log_utils
from common.utils.db_utils import create_short_lived_engine
from common.utils.env_utils import extract_service

logger = log_utils.get_logger(__name__)

_tracer = trace.get_tracer(__name__)


class _DryRunSheetsClient:
    """Stands in for ``SheetsClient`` during ``--dry-run``: never reads or
    writes the real sheet, so every eligible incident is reprocessed and the
    result is only logged for manual review. Covers both the incidents-tab
    (``read_rows``/``upsert_rows``) and correlations-tab (``append_rows``)
    interfaces, so one instance serves both roles in dry-run mode."""

    def read_rows(self) -> list[dict[str, Any]]:
        return []

    def upsert_rows(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            logger.info("dry-run row", **row)

    def append_rows(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            logger.info("dry-run correlation row", **row)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Root-cause-coverage audit")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the pipeline without reading or writing the real sheet; "
        "log every computed row instead",
    )
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=None,
        help="Override the deployed config's lookback_hours for this run only "
        "-- e.g. for a one-off backfill Job without changing the CronJob's "
        "own config.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run one root-cause-coverage audit pass. Returns 0 on success, 1 on failure."""
    args = parse_args(argv if argv is not None else sys.argv[1:])

    try:
        config = load_config("matik-audit-root-cause-config.yml")
    except Exception:
        log_utils.configure(level="INFO")
        logger.exception("failed to load audit-root-cause config")
        return 1

    log_utils.configure(level="INFO", environment=config.common.environment)
    logger.info("starting root-cause-coverage audit", dry_run=args.dry_run)

    try:
        config = merge_config(config, "metrics.yml")
    except FileNotFoundError:
        logger.warning("metrics.yml not found, proceeding without metrics config")

    try:
        config = merge_config(config, "llm-tracing-config.yml")
    except FileNotFoundError:
        logger.warning(
            "llm-tracing-config.yml not found, proceeding without LLM tracing"
        )

    if not config.mysql:
        logger.error("mysql config is not loaded")
        return 1
    if not config.incidentio:
        logger.error("incidentio config is not loaded")
        return 1
    if not config.incidentio.incident_type_ids:
        logger.error("incidentio.incident_type_ids is not configured")
        return 1
    if not config.biztech_github:
        logger.error("biztech_github config is not loaded")
        return 1
    if not config.jira:
        logger.error("jira config is not loaded")
        return 1
    if not config.llm_tracing or not config.llm_tracing.braintrust_project_id:
        logger.error("llm_tracing.braintrust_project_id is not configured")
        return 1
    if not args.dry_run and not config.sheets:
        logger.error("sheets config is not loaded")
        return 1
    if (
        not config.root_cause_audit
        or not config.root_cause_audit.ground_truth_extraction_prompt
    ):
        logger.error("root_cause_audit prompt config is not loaded")
        return 1

    # Captured before any merge_config reassigns `config` below, which would
    # otherwise widen these back to Optional for type checking.
    mysql_config = config.mysql
    incidentio_config = config.incidentio
    incident_type_ids = config.incidentio.incident_type_ids
    ghe_config = config.biztech_github
    jira_config = config.jira
    braintrust_project_id = config.llm_tracing.braintrust_project_id
    sheets_config = config.sheets
    root_cause_audit_config = config.root_cause_audit

    telescope: TelescopeClient | None = None
    if config.telescope and config.telescope.enabled:
        config.telescope.service_name = extract_service(__name__)
        config.telescope.environment = config.common.environment
        telescope = TelescopeClient(config.telescope)
        telescope.start()
        logger.info("Telescope metrics initialized")

    tracing: LLMTracingClient | None = None
    if config.llm_tracing.enabled:
        config.llm_tracing.environment = config.common.environment
        config.llm_tracing.service_name = extract_service(__name__)
        tracing = LLMTracingClient(config.llm_tracing)
        tracing.start()

    llm_provider = root_cause_audit_config.llm_provider
    llm_client: Any = None
    if llm_provider == "bedrock":
        config = merge_config(config, "bedrock-config.yml")
        if config.bedrock:
            llm_client = create_bedrock_client_sync(
                bedrock_config=config.bedrock, common_config=config.common
            )
        else:
            logger.error("bedrock config is not loaded")
    else:
        config = merge_config(config, "facade-config.yml")
        if config.facade:
            llm_client = create_facade_client_sync(
                facade_config=config.facade, common_config=config.common
            )
        else:
            logger.error("facade config is not loaded")

    if llm_client is None:
        logger.error("no LLM client available", llm_provider=llm_provider)
        return 1

    engine = create_short_lived_engine(mysql_config)

    sheets_client: SheetsClientProtocol
    correlations_sheets_client: CorrelationsSheetsClient
    if args.dry_run:
        dry_run_client = _DryRunSheetsClient()
        sheets_client = dry_run_client
        correlations_sheets_client = dry_run_client
    else:
        assert sheets_config is not None
        # SheetsClient.__post_init__ opens the spreadsheet and resolves the
        # worksheet/table immediately (several Sheets API calls) -- give that
        # setup work its own span so those calls have a parent to nest under,
        # instead of showing up as disconnected root spans in Braintrust.
        with _tracer.start_as_current_span("sheets_client_setup"):
            sheets_client = SheetsClient(sheets_config=sheets_config)
            correlations_sheets_client = SheetsClient(
                sheets_config=SheetsConfig(
                    spreadsheet_id=sheets_config.spreadsheet_id,
                    worksheet_name=root_cause_audit_config.correlations_worksheet_name,
                    service_account_json=sheets_config.service_account_json,
                    service_account_file=sheets_config.service_account_file,
                )
            )

    context = RootCauseAuditContext(
        incident_dao=IncidentIOIncidentDAO(engine),
        correlation_dao=ReliabilityCorrelationDAO(engine),
        ghe_pr_dao=GHEPRDAO(engine),
        incidentio_client=create_incidentio_client_sync(
            incidentio_config=incidentio_config
        ),
        ghe_client=create_ghe_client(ghe_config=ghe_config),
        jira_client=create_jira_client(jira_config=jira_config),
        llm_client=llm_client,
        braintrust_client=BraintrustClient(
            braintrust_project_id=braintrust_project_id,
            entity_type_by_field={
                spec.correlation_engine_field: spec.entity_type
                for spec in all_root_cause_sources()
            },
            trace_environment=root_cause_audit_config.braintrust_trace_environment,
            min_llm_score=root_cause_audit_config.min_llm_score,
            speculative_patterns=root_cause_audit_config.speculative_patterns,
            speculative_max_score=root_cause_audit_config.speculative_max_score,
        ),
        correlations_sheets_client=correlations_sheets_client,
        ground_truth_extraction_prompt=root_cause_audit_config.ground_truth_extraction_prompt,
        incident_type_ids=incident_type_ids,
        lookback_hours=(
            args.lookback_hours
            if args.lookback_hours is not None
            else root_cause_audit_config.lookback_hours
        ),
        investigation_window_minutes=root_cause_audit_config.investigation_window_minutes,
    )

    try:
        run_sheet_sync(SPEC, context, sheets_client)
        return 0
    except Exception:
        logger.exception("audit run failed")
        return 1
    finally:
        engine.dispose()
        if telescope:
            telescope.shutdown()
            logger.info("Telescope client shut down")
        if tracing:
            tracing.shutdown()
