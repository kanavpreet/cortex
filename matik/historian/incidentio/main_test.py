"""Tests for IncidentIO Historian service entry point."""

from unittest.mock import MagicMock, patch

from historian.incidentio.main import main


def _boto3_patch() -> MagicMock:
    """Return a patch context for boto3.Session that returns a mock SQS client."""
    mock_boto3 = MagicMock()
    mock_boto3.Session.return_value.client.return_value = MagicMock()
    return mock_boto3


def _mock_config() -> MagicMock:
    """Create a mock config object for testing."""
    mock = MagicMock()
    mock.common.environment = "local"
    mock.common.log_level = "INFO"
    mock.incidentio = MagicMock()
    mock.api = MagicMock()
    mock.telescope = None  # Disable telescope by default
    mock.enricher = MagicMock()  # Enricher is required (sole LLM enrichment path)
    return mock


def _mock_crawler_result(success: bool = True) -> MagicMock:
    """Create a mock crawler result."""
    result = MagicMock()
    result.success = success
    result.records_processed = 10
    result.error_message = None if success else "Test error"
    return result


def test_main_returns_zero_on_success() -> None:
    """Test that main() returns 0 on successful execution."""
    mock_config = _mock_config()
    mock_crawler = MagicMock()
    mock_crawler.dispatch.return_value = _mock_crawler_result(success=True)

    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", return_value=mock_config),
        patch("historian.incidentio.main.create_matik_api_client"),
        patch("historian.incidentio.main.create_incidentio_client"),
        patch("historian.incidentio.main.SQSClient"),
        patch("historian.incidentio.main.EnrichmentPublisher"),
        patch("historian.incidentio.main.boto3", _boto3_patch()),
        patch(
            "historian.incidentio.main.IncidentIOIncidentCrawler",
            return_value=mock_crawler,
        ),
    ):
        result = main()
        assert result == 0


def test_main_returns_one_on_config_load_failure() -> None:
    """Test that main() returns 1 when config loading fails."""
    mock_logger = MagicMock()

    with (
        patch(
            "historian.base.runner.load_config",
            side_effect=Exception("Config error"),
        ),
        patch("historian.base.runner.logger", mock_logger),
    ):
        result = main()

        # Should return 1 on config failure
        assert result == 1

        # Should log the config error with exception()
        mock_logger.exception.assert_called_once_with("Failed to load config")


def test_main_returns_one_on_failure() -> None:
    """Test that main() returns 1 when an exception occurs during job execution."""
    mock_config = _mock_config()
    mock_logger = MagicMock()

    # Make crawler creation raise an exception
    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", return_value=mock_config),
        patch("historian.incidentio.main.create_matik_api_client"),
        patch("historian.incidentio.main.create_incidentio_client"),
        patch("historian.incidentio.main.SQSClient"),
        patch("historian.incidentio.main.EnrichmentPublisher"),
        patch("historian.incidentio.main.boto3", _boto3_patch()),
        patch(
            "historian.incidentio.main.IncidentIOIncidentCrawler",
            side_effect=Exception("Crawler error"),
        ),
        patch("historian.base.runner.logger", mock_logger),
    ):
        result = main()

        # Should return 1 on failure
        assert result == 1

        # Should log the error with exception()
        mock_logger.exception.assert_called_once_with(
            "IncidentIO historian crawler failed with unexpected error"
        )


def test_main_returns_one_on_incidentio_config_merge_failure() -> None:
    """Test that main() returns 1 when incidentio config merge fails."""
    mock_config = _mock_config()
    mock_logger = MagicMock()

    def merge_side_effect(config: MagicMock, path: str) -> MagicMock:
        if "incidentio" in path:
            raise Exception("Merge error")
        return config

    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", side_effect=merge_side_effect),
        patch("historian.base.runner.logger", mock_logger),
    ):
        result = main()
        assert result == 1
        mock_logger.exception.assert_called_once_with("Failed to load config")


def test_main_returns_one_on_enricher_config_merge_failure() -> None:
    """Test that main() returns 1 when the (required) enricher config merge fails."""
    mock_config = _mock_config()
    mock_logger = MagicMock()

    def merge_side_effect(config: MagicMock, path: str) -> MagicMock:
        if "enricher" in path:
            raise Exception("Merge error")
        return config

    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", side_effect=merge_side_effect),
        patch("historian.base.runner.logger", mock_logger),
    ):
        result = main()
        assert result == 1
        mock_logger.exception.assert_called_once_with("Failed to load enricher config")


def test_main_returns_one_when_incidentio_config_is_none() -> None:
    """Test that main() returns 1 when incidentio config is None."""
    mock_config = _mock_config()
    mock_config.incidentio = None
    mock_logger = MagicMock()

    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", return_value=mock_config),
        patch("historian.base.runner.logger", mock_logger),
    ):
        result = main()
        assert result == 1
        mock_logger.error.assert_called_once_with("IncidentIO configuration not found")


def test_main_returns_one_when_api_config_is_none() -> None:
    """Test that main() returns 1 when api config is None."""
    mock_config = _mock_config()
    mock_config.api = None
    mock_logger = MagicMock()

    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", return_value=mock_config),
        patch("historian.base.runner.logger", mock_logger),
    ):
        result = main()
        assert result == 1
        mock_logger.error.assert_called_once_with("API configuration not found")


def test_main_returns_one_when_enricher_config_is_none() -> None:
    """Test that main() returns 1 when enricher config is None (it is required)."""
    mock_config = _mock_config()
    mock_config.enricher = None
    mock_logger = MagicMock()

    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", return_value=mock_config),
        patch("historian.base.runner.logger", mock_logger),
    ):
        result = main()
        assert result == 1
        mock_logger.error.assert_called_once_with(
            "Enricher configuration not found; it is required for LLM enrichment"
        )


def test_main_returns_one_when_crawler_fails() -> None:
    """Test that main() returns 1 when crawler dispatch fails."""
    mock_config = _mock_config()
    mock_crawler = MagicMock()
    mock_crawler.dispatch.return_value = _mock_crawler_result(success=False)
    mock_logger = MagicMock()

    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", return_value=mock_config),
        patch("historian.incidentio.main.create_matik_api_client"),
        patch("historian.incidentio.main.create_incidentio_client"),
        patch("historian.incidentio.main.SQSClient"),
        patch("historian.incidentio.main.EnrichmentPublisher"),
        patch("historian.incidentio.main.boto3", _boto3_patch()),
        patch(
            "historian.incidentio.main.IncidentIOIncidentCrawler",
            return_value=mock_crawler,
        ),
        patch("historian.incidentio.main.logger", mock_logger),
    ):
        result = main()
        assert result == 1
        mock_logger.error.assert_called_once()


def test_main_returns_one_on_metrics_config_merge_failure() -> None:
    """Test that main() returns 1 when metrics config merge fails."""
    mock_config = _mock_config()
    mock_logger = MagicMock()

    def merge_side_effect(config: MagicMock, path: str) -> MagicMock:
        if "metrics" in path:
            raise Exception("Merge error")
        return config

    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", side_effect=merge_side_effect),
        patch("historian.base.runner.logger", mock_logger),
    ):
        result = main()
        assert result == 1
        mock_logger.exception.assert_called_once_with("Failed to load metrics config")


def _mock_telescope_config() -> MagicMock:
    """Create a mock telescope config that is enabled."""
    telescope = MagicMock()
    telescope.enabled = True
    return telescope


def test_main_initializes_telescope_when_enabled() -> None:
    """Test that main() initializes telescope metrics when config is enabled."""
    mock_config = _mock_config()
    mock_config.telescope = _mock_telescope_config()
    mock_crawler = MagicMock()
    mock_crawler.dispatch.return_value = _mock_crawler_result(success=True)
    mock_telescope = MagicMock()
    mock_meter = MagicMock()
    mock_telescope.meter = mock_meter

    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", return_value=mock_config),
        patch("historian.incidentio.main.create_matik_api_client"),
        patch("historian.incidentio.main.create_incidentio_client"),
        patch("historian.incidentio.main.SQSClient"),
        patch("historian.incidentio.main.EnrichmentPublisher"),
        patch("historian.incidentio.main.boto3", _boto3_patch()),
        patch(
            "historian.incidentio.main.IncidentIOIncidentCrawler",
            return_value=mock_crawler,
        ),
        patch("historian.base.runner.TelescopeClient", return_value=mock_telescope),
        patch("historian.incidentio.main.ClientMetrics"),
        patch("historian.incidentio.main.JobMetrics"),
        patch(
            "historian.base.runner.extract_full_service",
            return_value="historian.incidentio",
        ),
        patch("historian.base.runner.extract_service", return_value="historian"),
    ):
        result = main()
        assert result == 0
        mock_telescope.start.assert_called_once()
        mock_telescope.shutdown.assert_called_once()


def test_main_records_job_metrics_on_success() -> None:
    """Test that main() records job metrics when crawler succeeds."""
    mock_config = _mock_config()
    mock_config.telescope = _mock_telescope_config()
    mock_crawler = MagicMock()
    mock_crawler.dispatch.return_value = _mock_crawler_result(success=True)
    mock_telescope = MagicMock()
    mock_meter = MagicMock()
    mock_telescope.meter = mock_meter
    mock_job_metrics = MagicMock()
    mock_record_job = MagicMock()
    mock_job_metrics.start_job.return_value = mock_record_job

    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", return_value=mock_config),
        patch("historian.incidentio.main.create_matik_api_client"),
        patch("historian.incidentio.main.create_incidentio_client"),
        patch("historian.incidentio.main.SQSClient"),
        patch("historian.incidentio.main.EnrichmentPublisher"),
        patch("historian.incidentio.main.boto3", _boto3_patch()),
        patch(
            "historian.incidentio.main.IncidentIOIncidentCrawler",
            return_value=mock_crawler,
        ),
        patch("historian.base.runner.TelescopeClient", return_value=mock_telescope),
        patch("historian.incidentio.main.ClientMetrics"),
        patch("historian.incidentio.main.JobMetrics", return_value=mock_job_metrics),
        patch(
            "historian.base.runner.extract_full_service",
            return_value="historian.incidentio",
        ),
        patch("historian.base.runner.extract_service", return_value="historian"),
    ):
        result = main()
        assert result == 0
        mock_job_metrics.start_job.assert_called_once_with("incidentio_incidents")
        mock_record_job.assert_called_once_with(10, None)


def test_main_records_job_metrics_on_failure() -> None:
    """Test that main() records job metrics when crawler fails."""
    mock_config = _mock_config()
    mock_config.telescope = _mock_telescope_config()
    mock_crawler = MagicMock()
    mock_crawler.dispatch.return_value = _mock_crawler_result(success=False)
    mock_telescope = MagicMock()
    mock_meter = MagicMock()
    mock_telescope.meter = mock_meter
    mock_job_metrics = MagicMock()
    mock_record_job = MagicMock()
    mock_job_metrics.start_job.return_value = mock_record_job

    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", return_value=mock_config),
        patch("historian.incidentio.main.create_matik_api_client"),
        patch("historian.incidentio.main.create_incidentio_client"),
        patch("historian.incidentio.main.SQSClient"),
        patch("historian.incidentio.main.EnrichmentPublisher"),
        patch("historian.incidentio.main.boto3", _boto3_patch()),
        patch(
            "historian.incidentio.main.IncidentIOIncidentCrawler",
            return_value=mock_crawler,
        ),
        patch("historian.base.runner.TelescopeClient", return_value=mock_telescope),
        patch("historian.incidentio.main.ClientMetrics"),
        patch("historian.incidentio.main.JobMetrics", return_value=mock_job_metrics),
        patch(
            "historian.base.runner.extract_full_service",
            return_value="historian.incidentio",
        ),
        patch("historian.base.runner.extract_service", return_value="historian"),
    ):
        result = main()
        assert result == 1
        mock_job_metrics.start_job.assert_called_once_with("incidentio_incidents")
        # record_job is called with error exception
        assert mock_record_job.call_count == 1
        call_args = mock_record_job.call_args
        assert call_args[0][0] == 10  # records_processed
        assert isinstance(call_args[0][1], Exception)  # error exception


def test_main_records_job_metrics_on_exception() -> None:
    """Test that main() records job metrics when crawler throws exception."""
    mock_config = _mock_config()
    mock_config.telescope = _mock_telescope_config()
    mock_crawler = MagicMock()
    mock_crawler.dispatch.side_effect = Exception("Unexpected error")
    mock_telescope = MagicMock()
    mock_meter = MagicMock()
    mock_telescope.meter = mock_meter
    mock_job_metrics = MagicMock()
    mock_record_job = MagicMock()
    mock_job_metrics.start_job.return_value = mock_record_job

    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", return_value=mock_config),
        patch("historian.incidentio.main.create_matik_api_client"),
        patch("historian.incidentio.main.create_incidentio_client"),
        patch("historian.incidentio.main.SQSClient"),
        patch("historian.incidentio.main.EnrichmentPublisher"),
        patch("historian.incidentio.main.boto3", _boto3_patch()),
        patch(
            "historian.incidentio.main.IncidentIOIncidentCrawler",
            return_value=mock_crawler,
        ),
        patch("historian.base.runner.TelescopeClient", return_value=mock_telescope),
        patch("historian.incidentio.main.ClientMetrics"),
        patch("historian.incidentio.main.JobMetrics", return_value=mock_job_metrics),
        patch(
            "historian.base.runner.extract_full_service",
            return_value="historian.incidentio",
        ),
        patch("historian.base.runner.extract_service", return_value="historian"),
    ):
        result = main()
        assert result == 1
        mock_job_metrics.start_job.assert_called_once_with("incidentio_incidents")
        # record_job is called with 0 items and error
        mock_record_job.assert_called_once()
        call_args = mock_record_job.call_args
        assert call_args[0][0] == 0  # no incidents processed
        assert isinstance(call_args[0][1], Exception)


def test_main_handles_record_job_exception_on_success() -> None:
    """Test that main() handles exceptions from record_job on success path."""
    mock_config = _mock_config()
    mock_config.telescope = _mock_telescope_config()
    mock_crawler = MagicMock()
    mock_crawler.dispatch.return_value = _mock_crawler_result(success=True)
    mock_telescope = MagicMock()
    mock_meter = MagicMock()
    mock_telescope.meter = mock_meter
    mock_job_metrics = MagicMock()
    mock_record_job = MagicMock(side_effect=Exception("Metrics error"))
    mock_job_metrics.start_job.return_value = mock_record_job
    mock_logger = MagicMock()

    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", return_value=mock_config),
        patch("historian.incidentio.main.create_matik_api_client"),
        patch("historian.incidentio.main.create_incidentio_client"),
        patch("historian.incidentio.main.SQSClient"),
        patch("historian.incidentio.main.EnrichmentPublisher"),
        patch("historian.incidentio.main.boto3", _boto3_patch()),
        patch(
            "historian.incidentio.main.IncidentIOIncidentCrawler",
            return_value=mock_crawler,
        ),
        patch("historian.base.runner.TelescopeClient", return_value=mock_telescope),
        patch("historian.incidentio.main.ClientMetrics"),
        patch("historian.incidentio.main.JobMetrics", return_value=mock_job_metrics),
        patch(
            "historian.base.runner.extract_full_service",
            return_value="historian.incidentio",
        ),
        patch("historian.base.runner.extract_service", return_value="historian"),
        patch("historian.incidentio.main.logger", mock_logger),
    ):
        result = main()
        # Should still return 0 - job succeeded, just metrics recording failed
        assert result == 0
        mock_logger.warning.assert_any_call("Failed to record job metrics")


def test_main_handles_record_job_exception_on_failure() -> None:
    """Test that main() handles exceptions from record_job on failure path."""
    mock_config = _mock_config()
    mock_config.telescope = _mock_telescope_config()
    mock_crawler = MagicMock()
    mock_crawler.dispatch.return_value = _mock_crawler_result(success=False)
    mock_telescope = MagicMock()
    mock_meter = MagicMock()
    mock_telescope.meter = mock_meter
    mock_job_metrics = MagicMock()
    mock_record_job = MagicMock(side_effect=Exception("Metrics error"))
    mock_job_metrics.start_job.return_value = mock_record_job
    mock_logger = MagicMock()

    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", return_value=mock_config),
        patch("historian.incidentio.main.create_matik_api_client"),
        patch("historian.incidentio.main.create_incidentio_client"),
        patch("historian.incidentio.main.SQSClient"),
        patch("historian.incidentio.main.EnrichmentPublisher"),
        patch("historian.incidentio.main.boto3", _boto3_patch()),
        patch(
            "historian.incidentio.main.IncidentIOIncidentCrawler",
            return_value=mock_crawler,
        ),
        patch("historian.base.runner.TelescopeClient", return_value=mock_telescope),
        patch("historian.incidentio.main.ClientMetrics"),
        patch("historian.incidentio.main.JobMetrics", return_value=mock_job_metrics),
        patch(
            "historian.base.runner.extract_full_service",
            return_value="historian.incidentio",
        ),
        patch("historian.base.runner.extract_service", return_value="historian"),
        patch("historian.incidentio.main.logger", mock_logger),
    ):
        result = main()
        # Should return 1 - job failed
        assert result == 1
        mock_logger.warning.assert_any_call("Failed to record job metrics")


def test_main_handles_record_job_exception_on_crash() -> None:
    """Test that main() handles exceptions from record_job on crash path."""
    mock_config = _mock_config()
    mock_config.telescope = _mock_telescope_config()
    mock_crawler = MagicMock()
    mock_crawler.dispatch.side_effect = Exception("Crash!")
    mock_telescope = MagicMock()
    mock_meter = MagicMock()
    mock_telescope.meter = mock_meter
    mock_job_metrics = MagicMock()
    mock_record_job = MagicMock(side_effect=Exception("Metrics error"))
    mock_job_metrics.start_job.return_value = mock_record_job
    mock_logger = MagicMock()

    with (
        patch("historian.base.runner.load_config", return_value=mock_config),
        patch("historian.base.runner.merge_config", return_value=mock_config),
        patch("historian.incidentio.main.create_matik_api_client"),
        patch("historian.incidentio.main.create_incidentio_client"),
        patch("historian.incidentio.main.SQSClient"),
        patch("historian.incidentio.main.EnrichmentPublisher"),
        patch("historian.incidentio.main.boto3", _boto3_patch()),
        patch(
            "historian.incidentio.main.IncidentIOIncidentCrawler",
            return_value=mock_crawler,
        ),
        patch("historian.base.runner.TelescopeClient", return_value=mock_telescope),
        patch("historian.incidentio.main.ClientMetrics"),
        patch("historian.incidentio.main.JobMetrics", return_value=mock_job_metrics),
        patch(
            "historian.base.runner.extract_full_service",
            return_value="historian.incidentio",
        ),
        patch("historian.base.runner.extract_service", return_value="historian"),
        patch("historian.incidentio.main.logger", mock_logger),
    ):
        result = main()
        # Should return 1 - job crashed
        assert result == 1
        mock_logger.warning.assert_any_call("Failed to record job metrics")
