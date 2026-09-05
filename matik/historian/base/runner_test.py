"""Tests for the lean run_historian entry-point shell.

Drives run_historian with a fake spec + a fake build_and_run callback, patching
the shell's own module-level names (config, telescope). Covers the config-merge
failure paths, the required-config hard-fails, the telescope on/off branches, the
HistorianContext handed to the callback, the callback's return-code passthrough,
crash handling, and telescope shutdown — the common shell behavior all sources
share. Per-source client/publisher/job wiring lives in each source's callback and
is tested there.

A neutral ``fake_source`` stands in for any data source: the shell is
source-agnostic, so these tests deliberately avoid naming a real source (the
generic contract is what's under test).
"""

from typing import Any
from unittest.mock import MagicMock, patch

from historian.base.runner import HistorianContext, run_historian

# Neutral source name used throughout — the shell treats it as an opaque label
# (config section attribute + client-metrics label), so no real source is needed.
FAKE_SOURCE = "fake_source"


def _fake_spec(source_type: str = FAKE_SOURCE) -> MagicMock:
    spec = MagicMock()
    spec.source_type = source_type
    return spec


def _mock_config() -> MagicMock:
    """A config whose source section (config.fake_source) + api/enricher exist."""
    mock = MagicMock()
    mock.common.environment = "local"
    mock.common.log_level = "INFO"
    mock.fake_source = MagicMock()
    mock.api = MagicMock()
    mock.telescope = None  # telescope off by default
    mock.enricher = MagicMock()
    return mock


def _run(
    *,
    config: MagicMock,
    build_and_run: Any,
    **overrides: Any,
) -> int:
    kwargs: dict[str, Any] = {
        "spec": _fake_spec(),
        "caller_name": "historian.fake_source.main",
        "source_config_files": [
            ("matik-historian-fake-source-config.yml", "Failed to load config"),
            ("metrics.yml", "Failed to load metrics config"),
            ("matik-enricher-config.yml", "Failed to load enricher config"),
        ],
        "build_and_run": build_and_run,
        "source_config_missing_msg": "FakeSource configuration not found",
        "crash_log_msg": "FakeSource historian crawler failed with unexpected error",
    }
    kwargs.update(overrides)
    return run_historian(**kwargs)


def test_calls_build_and_run_and_returns_its_code() -> None:
    config = _mock_config()
    build_and_run = MagicMock(return_value=0)

    with (
        patch("historian.base.runner.load_config", return_value=config),
        patch("historian.base.runner.merge_config", return_value=config),
    ):
        assert _run(config=config, build_and_run=build_and_run) == 0
        build_and_run.assert_called_once()
        ctx = build_and_run.call_args[0][0]
        assert isinstance(ctx, HistorianContext)
        assert ctx.config is config
        assert ctx.source_config is config.fake_source
        # Telescope off → no meter / service name.
        assert ctx.meter is None
        assert ctx.service_name is None


def test_passes_through_nonzero_callback_code() -> None:
    config = _mock_config()
    build_and_run = MagicMock(return_value=1)

    with (
        patch("historian.base.runner.load_config", return_value=config),
        patch("historian.base.runner.merge_config", return_value=config),
    ):
        assert _run(config=config, build_and_run=build_and_run) == 1


def test_returns_one_on_base_config_load_failure() -> None:
    mock_logger = MagicMock()
    with (
        patch("historian.base.runner.load_config", side_effect=Exception("load error")),
        patch("historian.base.runner.logger", mock_logger),
    ):
        assert _run(config=_mock_config(), build_and_run=MagicMock()) == 1
        mock_logger.exception.assert_called_once_with("Failed to load config")


def test_returns_one_on_metrics_merge_failure() -> None:
    config = _mock_config()
    mock_logger = MagicMock()

    def merge_side_effect(cfg: MagicMock, path: str) -> MagicMock:
        if "metrics" in path:
            raise Exception("merge error")
        return cfg

    with (
        patch("historian.base.runner.load_config", return_value=config),
        patch("historian.base.runner.merge_config", side_effect=merge_side_effect),
        patch("historian.base.runner.logger", mock_logger),
    ):
        assert _run(config=config, build_and_run=MagicMock()) == 1
        mock_logger.exception.assert_called_once_with("Failed to load metrics config")


def test_returns_one_when_source_config_none() -> None:
    config = _mock_config()
    config.fake_source = None
    mock_logger = MagicMock()

    with (
        patch("historian.base.runner.load_config", return_value=config),
        patch("historian.base.runner.merge_config", return_value=config),
        patch("historian.base.runner.logger", mock_logger),
    ):
        build_and_run = MagicMock()
        assert _run(config=config, build_and_run=build_and_run) == 1
        mock_logger.error.assert_called_once_with("FakeSource configuration not found")
        build_and_run.assert_not_called()


def test_returns_one_when_api_config_none() -> None:
    config = _mock_config()
    config.api = None
    mock_logger = MagicMock()

    with (
        patch("historian.base.runner.load_config", return_value=config),
        patch("historian.base.runner.merge_config", return_value=config),
        patch("historian.base.runner.logger", mock_logger),
    ):
        assert _run(config=config, build_and_run=MagicMock()) == 1
        mock_logger.error.assert_called_once_with("API configuration not found")


def test_returns_one_when_enricher_config_none() -> None:
    config = _mock_config()
    config.enricher = None
    mock_logger = MagicMock()

    with (
        patch("historian.base.runner.load_config", return_value=config),
        patch("historian.base.runner.merge_config", return_value=config),
        patch("historian.base.runner.logger", mock_logger),
    ):
        assert _run(config=config, build_and_run=MagicMock()) == 1
        mock_logger.error.assert_called_once_with(
            "Enricher configuration not found; it is required for LLM enrichment"
        )


def _telescope_config() -> MagicMock:
    telescope = MagicMock()
    telescope.enabled = True
    return telescope


def test_telescope_started_context_populated_and_shutdown() -> None:
    config = _mock_config()
    config.telescope = _telescope_config()
    mock_telescope = MagicMock()
    mock_telescope.meter = MagicMock()
    captured: dict[str, Any] = {}

    def build_and_run(ctx: HistorianContext) -> int:
        captured["meter"] = ctx.meter
        captured["service_name"] = ctx.service_name
        return 0

    with (
        patch("historian.base.runner.load_config", return_value=config),
        patch("historian.base.runner.merge_config", return_value=config),
        patch("historian.base.runner.TelescopeClient", return_value=mock_telescope),
        patch(
            "historian.base.runner.extract_full_service",
            return_value="historian.fake_source",
        ),
        patch("historian.base.runner.extract_service", return_value="historian"),
    ):
        assert _run(config=config, build_and_run=build_and_run) == 0
        mock_telescope.start.assert_called_once()
        mock_telescope.shutdown.assert_called_once()
        # The context carries the telescope meter + derived service name.
        assert captured["meter"] is mock_telescope.meter
        assert captured["service_name"] == "historian"


def test_telescope_shutdown_on_callback_crash() -> None:
    config = _mock_config()
    config.telescope = _telescope_config()
    mock_telescope = MagicMock()
    mock_telescope.meter = MagicMock()
    mock_logger = MagicMock()

    def build_and_run(ctx: HistorianContext) -> int:
        raise Exception("unexpected")

    with (
        patch("historian.base.runner.load_config", return_value=config),
        patch("historian.base.runner.merge_config", return_value=config),
        patch("historian.base.runner.TelescopeClient", return_value=mock_telescope),
        patch(
            "historian.base.runner.extract_full_service",
            return_value="historian.fake_source",
        ),
        patch("historian.base.runner.extract_service", return_value="historian"),
        patch("historian.base.runner.logger", mock_logger),
    ):
        assert _run(config=config, build_and_run=build_and_run) == 1
        mock_logger.exception.assert_called_once_with(
            "FakeSource historian crawler failed with unexpected error"
        )
        # Telescope is still shut down via the finally block.
        mock_telescope.shutdown.assert_called_once()


def test_telescope_disabled_still_runs_callback() -> None:
    config = _mock_config()  # telescope is None
    build_and_run = MagicMock(return_value=0)

    with (
        patch("historian.base.runner.load_config", return_value=config),
        patch("historian.base.runner.merge_config", return_value=config),
        patch("historian.base.runner.TelescopeClient") as mock_telescope_cls,
    ):
        assert _run(config=config, build_and_run=build_and_run) == 0
        mock_telescope_cls.assert_not_called()
        build_and_run.assert_called_once()


def test_default_missing_config_message() -> None:
    """The default source-config-missing message is used when not overridden."""
    config = _mock_config()
    config.fake_source = None
    mock_logger = MagicMock()

    with (
        patch("historian.base.runner.load_config", return_value=config),
        patch("historian.base.runner.merge_config", return_value=config),
        patch("historian.base.runner.logger", mock_logger),
    ):
        result = run_historian(
            spec=_fake_spec(),
            caller_name="mod",
            source_config_files=[],
            build_and_run=MagicMock(),
        )
        assert result == 1
        mock_logger.error.assert_called_once_with("Source configuration not found")
