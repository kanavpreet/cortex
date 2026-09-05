"""Tests for Biztech GitHub Historian service entry point.

The historian now runs on the shared ``run_historian`` shell: config merges,
logging, config validation, Telescope, and shutdown live in
``historian.base.runner``; the GHE-specific client/publisher/metrics/crawler
wiring lives in the ``_build_and_run`` callback in ``historian.biztech_github.main``.
Patches target whichever module owns the name. The crawler exposes the shared
``dispatch() -> CrawlerResult`` contract.
"""

import contextlib
from typing import Any
from unittest.mock import MagicMock, patch

from historian.base.crawler import CrawlerResult
from historian.biztech_github.main import main


def _mock_config(
    *,
    telescope_enabled: bool = False,
    api_config: MagicMock | None = None,
) -> MagicMock:
    """Create a mock config object for testing.

    Args:
        telescope_enabled: Whether to enable Telescope metrics.
        api_config: API config mock, or MagicMock() if None. Use explicit None
                    to set config.api = None.
    """
    mock = MagicMock()
    mock.common.environment = "local"
    mock.common.log_level = "INFO"
    mock.biztech_github = MagicMock()
    mock.api = api_config if api_config is not None else MagicMock()
    mock.connector.get.return_value = None

    # The Enricher SQS queue is the sole LLM enrichment path and is required.
    mock.enricher = MagicMock()
    mock.enricher.region = "us-east-1"
    mock.enricher.enricher_queue_url = "https://sqs.us-east-1.amazonaws.com/q"

    if telescope_enabled:
        mock.telescope = MagicMock()
        mock.telescope.enabled = True
    else:
        mock.telescope = None

    return mock


def _mock_crawler(*, success: bool = True, prs: int = 10) -> MagicMock:
    """A crawler whose dispatch() returns a CrawlerResult (the shared contract)."""
    crawler = MagicMock()
    crawler.dispatch = MagicMock(
        return_value=CrawlerResult(
            records_processed=prs,
            success=success,
            error_message=None if success else "boom",
        )
    )
    return crawler


def _callback_patches(crawler: MagicMock) -> list[Any]:
    """Patches for the callback-owned names (all on historian.biztech_github.main)."""
    return [
        patch(
            "historian.biztech_github.main.create_ghe_client",
            return_value=MagicMock(),
        ),
        patch(
            "historian.biztech_github.main.create_matik_api_client",
            return_value=MagicMock(),
        ),
        patch("historian.biztech_github.main.boto3"),
        patch("historian.biztech_github.main.SQSPublisher"),
        patch("historian.biztech_github.main.SQSClient"),
        patch("historian.biztech_github.main.EnrichmentPublisher"),
        patch("historian.biztech_github.main.GHEPRCrawler", return_value=crawler),
    ]


def _run_main_with(config: MagicMock, crawler: MagicMock) -> int:
    """Enter the shell config patches + callback patches and call main()."""
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch("historian.base.runner.load_config", return_value=config)
        )
        stack.enter_context(
            patch("historian.base.runner.merge_config", return_value=config)
        )
        for p in _callback_patches(crawler):
            stack.enter_context(p)
        return main()


def test_main_returns_zero_on_success() -> None:
    """Test that main() returns 0 on successful execution."""
    crawler = _mock_crawler(success=True)
    assert _run_main_with(_mock_config(), crawler) == 0
    crawler.dispatch.assert_called_once()


def test_main_returns_one_on_config_load_failure() -> None:
    """Test that main() returns 1 when biztech_github config is missing."""
    mock_config = _mock_config()
    mock_config.biztech_github = None

    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", return_value=mock_config),
    ):
        assert main() == 1


def test_main_returns_one_on_failure() -> None:
    """Test that main() returns 1 when the crawler reports failure."""
    crawler = _mock_crawler(success=False)
    assert _run_main_with(_mock_config(), crawler) == 1


def test_main_returns_one_when_api_config_missing() -> None:
    """Test that main() returns 1 when api config is missing."""
    mock_config = _mock_config()
    mock_config.api = None

    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", return_value=mock_config),
    ):
        assert main() == 1


def test_main_returns_one_when_enricher_config_missing() -> None:
    """Test that main() returns 1 when enricher config is missing.

    The Enricher SQS queue is the sole LLM enrichment path, so it is required.
    """
    mock_config = _mock_config()
    mock_config.enricher = None

    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", return_value=mock_config),
    ):
        assert main() == 1


def test_main_with_telescope_enabled() -> None:
    """Test that main() initializes and shuts down Telescope when enabled."""
    crawler = _mock_crawler(success=True, prs=5)
    mock_telescope = MagicMock()
    mock_telescope.meter = MagicMock()

    with contextlib.ExitStack() as stack:
        cfg = _mock_config(telescope_enabled=True)
        stack.enter_context(
            patch("historian.base.runner.load_config", return_value=cfg)
        )
        stack.enter_context(
            patch("historian.base.runner.merge_config", return_value=cfg)
        )
        stack.enter_context(
            patch("historian.base.runner.TelescopeClient", return_value=mock_telescope)
        )
        stack.enter_context(patch("historian.biztech_github.main.ClientMetrics"))
        stack.enter_context(patch("historian.biztech_github.main.GHECacheMetrics"))
        stack.enter_context(patch("historian.biztech_github.main.JobMetrics"))
        stack.enter_context(patch("historian.biztech_github.main.SQSPublisherMetrics"))
        for p in _callback_patches(crawler):
            stack.enter_context(p)
        assert main() == 0
        mock_telescope.start.assert_called_once()
        mock_telescope.shutdown.assert_called_once()


def test_main_telescope_shutdown_on_failure() -> None:
    """Test that Telescope is shut down even when the crawler fails."""
    crawler = _mock_crawler(success=False)
    mock_telescope = MagicMock()
    mock_telescope.meter = MagicMock()

    with contextlib.ExitStack() as stack:
        cfg = _mock_config(telescope_enabled=True)
        stack.enter_context(
            patch("historian.base.runner.load_config", return_value=cfg)
        )
        stack.enter_context(
            patch("historian.base.runner.merge_config", return_value=cfg)
        )
        stack.enter_context(
            patch("historian.base.runner.TelescopeClient", return_value=mock_telescope)
        )
        stack.enter_context(patch("historian.biztech_github.main.ClientMetrics"))
        stack.enter_context(patch("historian.biztech_github.main.GHECacheMetrics"))
        stack.enter_context(patch("historian.biztech_github.main.JobMetrics"))
        stack.enter_context(patch("historian.biztech_github.main.SQSPublisherMetrics"))
        for p in _callback_patches(crawler):
            stack.enter_context(p)
        assert main() == 1
        mock_telescope.start.assert_called_once()
        mock_telescope.shutdown.assert_called_once()


def test_main_returns_one_on_metrics_merge_failure() -> None:
    """A metrics.yml merge failure returns 1 (shell hard-fails all merges).

    This mirrors incidentio — metrics config is treated uniformly across sources.
    """
    base_config = _mock_config()

    def merge_side_effect(config: MagicMock, path: str) -> MagicMock:
        if path == "metrics.yml":
            raise FileNotFoundError
        return config

    with (
        patch("historian.base.runner.load_config", return_value=base_config),
        patch("historian.base.runner.merge_config", side_effect=merge_side_effect),
    ):
        assert main() == 1


def test_main_returns_one_when_sqs_queue_url_missing() -> None:
    """Test that main() returns 1 when sqs_queue_url is not configured."""
    mock_config = _mock_config()
    mock_config.biztech_github.sqs_queue_url = None
    assert _run_main_with(mock_config, _mock_crawler()) == 1


def test_main_with_enricher_config_creates_publisher() -> None:
    """Test that main() creates an EnrichmentPublisher from the enricher config."""
    crawler = _mock_crawler(success=True, prs=1)
    base_config = _mock_config()
    mock_sqs_client = MagicMock()
    mock_enrichment_publisher = MagicMock()

    with (
        patch("historian.base.runner.load_config", return_value=base_config),
        patch("historian.base.runner.merge_config", return_value=base_config),
        patch(
            "historian.biztech_github.main.create_ghe_client",
            return_value=MagicMock(),
        ),
        patch(
            "historian.biztech_github.main.create_matik_api_client",
            return_value=MagicMock(),
        ),
        patch("historian.biztech_github.main.boto3"),
        patch("historian.biztech_github.main.SQSPublisher"),
        patch("historian.biztech_github.main.SQSClient", return_value=mock_sqs_client),
        patch(
            "historian.biztech_github.main.EnrichmentPublisher",
            return_value=mock_enrichment_publisher,
        ) as mock_publisher_cls,
        patch("historian.biztech_github.main.GHEPRCrawler", return_value=crawler),
    ):
        assert main() == 0
        # The enrichment publisher is created unconditionally from enricher config.
        mock_publisher_cls.assert_called_once_with(
            mock_sqs_client, queue_url=base_config.enricher.enricher_queue_url
        )
