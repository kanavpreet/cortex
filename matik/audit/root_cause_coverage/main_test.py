"""Tests for the root-cause-coverage audit entry point."""

from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import audit.root_cause_coverage.main as main_mod
from audit.root_cause_coverage.main import _DryRunSheetsClient, main, parse_args
from common.models.root_cause_audit_config import RootCauseAuditConfig


@pytest.fixture
def span_exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    """In-memory span exporter, wired in place of main._tracer."""
    provider = TracerProvider()
    memory_exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory_exporter))
    monkeypatch.setattr(main_mod, "_tracer", provider.get_tracer(__name__))
    yield memory_exporter


_STANDARD_PATCH_TARGETS = [
    # Never let main() call the real log_utils.configure(): it mutates global
    # structlog state and corrupts other test files' capture_logs() assertions
    # when the full suite runs in one process (matches enigmatologist/main_test.py).
    "audit.root_cause_coverage.main.log_utils",
    "audit.root_cause_coverage.main.create_short_lived_engine",
    "audit.root_cause_coverage.main.create_incidentio_client_sync",
    "audit.root_cause_coverage.main.create_ghe_client",
    "audit.root_cause_coverage.main.create_jira_client",
    "audit.root_cause_coverage.main.create_facade_client_sync",
    "audit.root_cause_coverage.main.create_bedrock_client_sync",
    "audit.root_cause_coverage.main.BraintrustClient",
    "audit.root_cause_coverage.main.SheetsClient",
    "audit.root_cause_coverage.main.IncidentIOIncidentDAO",
    "audit.root_cause_coverage.main.ReliabilityCorrelationDAO",
    "audit.root_cause_coverage.main.GHEPRDAO",
    "audit.root_cause_coverage.main.run_sheet_sync",
]


def _mock_config() -> MagicMock:
    """A config mock with every section main() requires present and valid."""
    mock = MagicMock()
    mock.common.environment = "local"
    mock.mysql = MagicMock()
    mock.incidentio = MagicMock()
    mock.biztech_github = MagicMock()
    mock.jira = MagicMock()
    mock.llm_tracing = MagicMock()
    mock.llm_tracing.braintrust_project_id = "proj-123"
    mock.llm_tracing.enabled = False
    mock.sheets = MagicMock()
    mock.sheets.spreadsheet_id = "sheet-1"
    mock.sheets.service_account_json = None
    mock.sheets.service_account_file = None
    mock.root_cause_audit = RootCauseAuditConfig(
        ground_truth_extraction_prompt="a ground truth prompt",
    )
    mock.telescope = None
    mock.bedrock = None
    mock.facade = MagicMock()
    return mock


def _run_main(
    mock_config: MagicMock,
    argv: list[str] | None = None,
    merge_config_side_effect: Any = None,
    extra_patches: dict[str, dict[str, Any]] | None = None,
) -> tuple[int, dict[str, MagicMock]]:
    """Run main() with the standard client/DAO/engine patches applied.

    ``extra_patches`` lets a test override or add a target (e.g. to assert on
    a specific factory's call count) -- applied after the standard set so it
    wins for the duration of the call.
    """
    extra_patches = extra_patches or {}
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "audit.root_cause_coverage.main.load_config", return_value=mock_config
            )
        )
        if merge_config_side_effect is not None:
            stack.enter_context(
                patch(
                    "audit.root_cause_coverage.main.merge_config",
                    side_effect=merge_config_side_effect,
                )
            )
        else:
            stack.enter_context(
                patch(
                    "audit.root_cause_coverage.main.merge_config",
                    return_value=mock_config,
                )
            )
        for target in _STANDARD_PATCH_TARGETS:
            stack.enter_context(patch(target))

        mocks: dict[str, MagicMock] = {}
        for target, kwargs in extra_patches.items():
            mocks[target] = stack.enter_context(patch(target, **kwargs))

        result = main(argv if argv is not None else [])
        return result, mocks


def test_returns_one_on_config_load_failure() -> None:
    with (
        patch(
            "audit.root_cause_coverage.main.load_config",
            side_effect=Exception("config error"),
        ),
        patch("audit.root_cause_coverage.main.log_utils"),
    ):
        assert main([]) == 1


def test_warns_and_continues_when_metrics_config_not_found() -> None:
    mock_config = _mock_config()

    def merge_side_effect(config: MagicMock, path: str) -> MagicMock:
        if path == "metrics.yml":
            raise FileNotFoundError()
        return config

    result, mocks = _run_main(
        mock_config,
        merge_config_side_effect=merge_side_effect,
        extra_patches={"audit.root_cause_coverage.main.logger": {}},
    )
    assert result == 0
    mocks["audit.root_cause_coverage.main.logger"].warning.assert_any_call(
        "metrics.yml not found, proceeding without metrics config"
    )


def test_warns_and_continues_when_llm_tracing_config_not_found() -> None:
    mock_config = _mock_config()

    def merge_side_effect(config: MagicMock, path: str) -> MagicMock:
        if path == "llm-tracing-config.yml":
            raise FileNotFoundError()
        return config

    result, _ = _run_main(mock_config, merge_config_side_effect=merge_side_effect)
    assert result == 0


def test_returns_one_when_mysql_config_missing() -> None:
    mock_config = _mock_config()
    mock_config.mysql = None
    result, _ = _run_main(mock_config)
    assert result == 1


def test_returns_one_when_incidentio_config_missing() -> None:
    mock_config = _mock_config()
    mock_config.incidentio = None
    result, _ = _run_main(mock_config)
    assert result == 1


def test_returns_one_when_biztech_github_config_missing() -> None:
    mock_config = _mock_config()
    mock_config.biztech_github = None
    result, _ = _run_main(mock_config)
    assert result == 1


def test_returns_one_when_jira_config_missing() -> None:
    mock_config = _mock_config()
    mock_config.jira = None
    result, _ = _run_main(mock_config)
    assert result == 1


def test_returns_one_when_llm_tracing_missing() -> None:
    mock_config = _mock_config()
    mock_config.llm_tracing = None
    result, _ = _run_main(mock_config)
    assert result == 1


def test_returns_one_when_braintrust_project_id_missing() -> None:
    mock_config = _mock_config()
    mock_config.llm_tracing.braintrust_project_id = None
    result, _ = _run_main(mock_config)
    assert result == 1


def test_returns_one_when_sheets_config_missing_and_not_dry_run() -> None:
    mock_config = _mock_config()
    mock_config.sheets = None
    result, _ = _run_main(mock_config)
    assert result == 1


def test_dry_run_skips_sheets_config_requirement() -> None:
    mock_config = _mock_config()
    mock_config.sheets = None
    result, _ = _run_main(mock_config, argv=["--dry-run"])
    assert result == 0


def test_returns_one_when_root_cause_audit_config_missing() -> None:
    mock_config = _mock_config()
    mock_config.root_cause_audit = None
    result, _ = _run_main(mock_config)
    assert result == 1


def test_returns_one_when_ground_truth_prompt_missing() -> None:
    mock_config = _mock_config()
    mock_config.root_cause_audit.ground_truth_extraction_prompt = ""
    result, _ = _run_main(mock_config)
    assert result == 1


def test_uses_bedrock_client_when_llm_provider_is_bedrock() -> None:
    mock_config = _mock_config()
    mock_config.root_cause_audit.llm_provider = "bedrock"
    mock_config.bedrock = MagicMock()

    result, mocks = _run_main(
        mock_config,
        extra_patches={"audit.root_cause_coverage.main.create_bedrock_client_sync": {}},
    )

    assert result == 0
    mocks[
        "audit.root_cause_coverage.main.create_bedrock_client_sync"
    ].assert_called_once()


def test_returns_one_when_bedrock_config_missing() -> None:
    mock_config = _mock_config()
    mock_config.root_cause_audit.llm_provider = "bedrock"
    mock_config.bedrock = None
    result, _ = _run_main(mock_config)
    assert result == 1


def test_returns_one_when_facade_config_missing() -> None:
    mock_config = _mock_config()
    mock_config.facade = None
    result, _ = _run_main(mock_config)
    assert result == 1


def test_dry_run_uses_dry_run_sheets_client() -> None:
    mock_config = _mock_config()
    result, mocks = _run_main(
        mock_config,
        argv=["--dry-run"],
        extra_patches={"audit.root_cause_coverage.main.SheetsClient": {}},
    )
    assert result == 0
    mocks["audit.root_cause_coverage.main.SheetsClient"].assert_not_called()


def test_non_dry_run_uses_real_sheets_client() -> None:
    mock_config = _mock_config()
    result, mocks = _run_main(
        mock_config,
        extra_patches={"audit.root_cause_coverage.main.SheetsClient": {}},
    )
    assert result == 0
    assert mocks["audit.root_cause_coverage.main.SheetsClient"].call_count == 2


def test_non_dry_run_opens_sheets_client_setup_span(
    span_exporter: InMemorySpanExporter,
) -> None:
    """SheetsClient construction (which makes its own Sheets API calls in
    __post_init__) has a span to nest under, instead of floating as a
    disconnected root span in Braintrust."""
    mock_config = _mock_config()
    result, _ = _run_main(
        mock_config,
        extra_patches={"audit.root_cause_coverage.main.SheetsClient": {}},
    )
    assert result == 0
    span_names = [s.name for s in span_exporter.get_finished_spans()]
    assert "sheets_client_setup" in span_names


def test_dry_run_does_not_open_sheets_client_setup_span(
    span_exporter: InMemorySpanExporter,
) -> None:
    """Dry-run never constructs a real SheetsClient, so no setup span opens."""
    mock_config = _mock_config()
    result, _ = _run_main(mock_config, argv=["--dry-run"])
    assert result == 0
    span_names = [s.name for s in span_exporter.get_finished_spans()]
    assert "sheets_client_setup" not in span_names


def test_braintrust_client_uses_configured_trace_environment() -> None:
    """main() threads root_cause_audit.braintrust_trace_environment through
    to BraintrustClient, rather than hardcoding a value itself."""
    mock_config = _mock_config()
    mock_config.root_cause_audit.braintrust_trace_environment = "staging"
    result, mocks = _run_main(
        mock_config,
        extra_patches={"audit.root_cause_coverage.main.BraintrustClient": {}},
    )
    assert result == 0
    call_kwargs = mocks["audit.root_cause_coverage.main.BraintrustClient"].call_args[1]
    assert call_kwargs["trace_environment"] == "staging"


def test_telescope_started_and_shutdown_when_enabled() -> None:
    mock_config = _mock_config()
    mock_config.telescope = MagicMock()
    mock_config.telescope.enabled = True
    mock_telescope = MagicMock()

    result, mocks = _run_main(
        mock_config,
        extra_patches={
            "audit.root_cause_coverage.main.TelescopeClient": {
                "return_value": mock_telescope
            }
        },
    )

    assert result == 0
    mocks["audit.root_cause_coverage.main.TelescopeClient"].assert_called_once()
    mock_telescope.start.assert_called_once()
    mock_telescope.shutdown.assert_called_once()


def test_telescope_skipped_when_disabled() -> None:
    mock_config = _mock_config()
    mock_config.telescope = None

    result, mocks = _run_main(
        mock_config,
        extra_patches={"audit.root_cause_coverage.main.TelescopeClient": {}},
    )

    assert result == 0
    mocks["audit.root_cause_coverage.main.TelescopeClient"].assert_not_called()


def test_tracing_started_and_shutdown_when_enabled() -> None:
    mock_config = _mock_config()
    mock_config.llm_tracing.enabled = True
    mock_tracing = MagicMock()

    result, mocks = _run_main(
        mock_config,
        extra_patches={
            "audit.root_cause_coverage.main.LLMTracingClient": {
                "return_value": mock_tracing
            }
        },
    )

    assert result == 0
    mocks["audit.root_cause_coverage.main.LLMTracingClient"].assert_called_once()
    mock_tracing.start.assert_called_once()
    mock_tracing.shutdown.assert_called_once()


def test_tracing_skipped_when_disabled() -> None:
    mock_config = _mock_config()
    mock_config.llm_tracing.enabled = False

    result, mocks = _run_main(
        mock_config,
        extra_patches={"audit.root_cause_coverage.main.LLMTracingClient": {}},
    )

    assert result == 0
    mocks["audit.root_cause_coverage.main.LLMTracingClient"].assert_not_called()


def test_returns_zero_on_success_and_disposes_engine() -> None:
    mock_config = _mock_config()
    mock_engine = MagicMock()

    result, _ = _run_main(
        mock_config,
        extra_patches={
            "audit.root_cause_coverage.main.create_short_lived_engine": {
                "return_value": mock_engine
            }
        },
    )

    assert result == 0
    mock_engine.dispose.assert_called_once()


def test_returns_one_when_run_sheet_sync_raises() -> None:
    mock_config = _mock_config()

    result, mocks = _run_main(
        mock_config,
        extra_patches={
            "audit.root_cause_coverage.main.run_sheet_sync": {
                "side_effect": RuntimeError("boom")
            },
            "audit.root_cause_coverage.main.logger": {},
        },
    )

    assert result == 1
    mocks["audit.root_cause_coverage.main.logger"].exception.assert_called_once_with(
        "audit run failed"
    )


def test_parse_args_dry_run_flag_defaults_false() -> None:
    args = parse_args([])
    assert args.dry_run is False


def test_parse_args_dry_run_flag_set() -> None:
    args = parse_args(["--dry-run"])
    assert args.dry_run is True


def test_parse_args_lookback_hours_defaults_none() -> None:
    args = parse_args([])
    assert args.lookback_hours is None


def test_parse_args_lookback_hours_set() -> None:
    args = parse_args(["--lookback-hours", "720"])
    assert args.lookback_hours == 720


def test_lookback_hours_override_used_when_provided() -> None:
    mock_config = _mock_config()

    _, mocks = _run_main(
        mock_config,
        argv=["--lookback-hours", "720"],
        extra_patches={"audit.root_cause_coverage.main.run_sheet_sync": {}},
    )

    context = mocks["audit.root_cause_coverage.main.run_sheet_sync"].call_args.args[1]
    assert context.lookback_hours == 720


def test_lookback_hours_falls_back_to_config_when_not_provided() -> None:
    mock_config = _mock_config()
    mock_config.root_cause_audit.lookback_hours = 24

    _, mocks = _run_main(
        mock_config,
        extra_patches={"audit.root_cause_coverage.main.run_sheet_sync": {}},
    )

    context = mocks["audit.root_cause_coverage.main.run_sheet_sync"].call_args.args[1]
    assert context.lookback_hours == 24


def test_dry_run_sheets_client_read_rows_returns_empty_list() -> None:
    client = _DryRunSheetsClient()
    assert client.read_rows() == []


def test_dry_run_sheets_client_upsert_rows_logs_each_row() -> None:
    client = _DryRunSheetsClient()
    with patch("audit.root_cause_coverage.main.logger") as mock_logger:
        client.upsert_rows([{"reference_id": "INC-1", "audit_status": "llm_reviewed"}])
    mock_logger.info.assert_called_once_with(
        "dry-run row", reference_id="INC-1", audit_status="llm_reviewed"
    )


def test_dry_run_sheets_client_append_rows_logs_each_row() -> None:
    client = _DryRunSheetsClient()
    with patch("audit.root_cause_coverage.main.logger") as mock_logger:
        client.append_rows([{"reference_id": "INC-1", "classification": "relevant"}])
    mock_logger.info.assert_called_once_with(
        "dry-run correlation row", reference_id="INC-1", classification="relevant"
    )
