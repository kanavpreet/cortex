"""Tests for chronicler.main entry point."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chronicler.main import _load_config, _run, main
from common.models.chronicler_config import (
    ChroniclerConfig,
    ChroniclerKafkaConfig,
)

_OMIT = object()


def _make_matik_config(
    *,
    bootstrap_servers: str = "kafka-staging.kafka-staging:19092",
    scribe_url: str = "https://sqs.example.com/scribe-hp",
    enricher_url: str | None = "https://sqs.example.com/enricher",
    telescope_enabled: bool = False,
    chronicler: object = _OMIT,
) -> MagicMock:
    cfg = MagicMock()
    cfg.common.log_level = "INFO"
    cfg.common.environment = "sandbox"
    if chronicler is _OMIT:
        cfg.chronicler = ChroniclerConfig(
            kafka=ChroniclerKafkaConfig(
                bootstrap_servers=bootstrap_servers,
                group_id="matik-chronicler-sandbox",
                topics=["yoyo.callback.matik_sandbox_ghe_webhook_events"],
            ),
            scribe_queue_url=scribe_url,
            scribe_queue_region="us-east-1",
            enricher_queue_url=enricher_url,
            enricher_queue_region="us-east-1",
        )
    else:
        cfg.chronicler = chronicler
    if telescope_enabled:
        cfg.telescope = MagicMock(enabled=True)
    else:
        cfg.telescope = None
    return cfg


class TestLoadConfig:
    def test_merges_metrics_when_available(self) -> None:
        base = MagicMock()
        merged = MagicMock()
        with (
            patch("chronicler.main.load_config", return_value=base) as load,
            patch("chronicler.main.merge_config", return_value=merged) as merge,
        ):
            result = _load_config()
        load.assert_called_once_with("matik-chronicler-config.yml")
        merge.assert_called_once_with(base, "metrics.yml")
        assert result is merged

    def test_tolerates_missing_metrics_file(self) -> None:
        base = MagicMock()
        with (
            patch("chronicler.main.load_config", return_value=base),
            patch("chronicler.main.merge_config", side_effect=FileNotFoundError()),
        ):
            result = _load_config()
        assert result is base


@pytest.mark.asyncio
async def test_run_raises_when_chronicler_config_missing() -> None:
    cfg = _make_matik_config(chronicler=None)
    with (
        patch("chronicler.main._load_config", return_value=cfg),
        patch("chronicler.main.log_utils.configure"),
        pytest.raises(RuntimeError, match="chronicler config section is required"),
    ):
        await _run()


@pytest.mark.asyncio
async def test_run_raises_when_bootstrap_servers_missing() -> None:
    cfg = _make_matik_config(bootstrap_servers="")
    with (
        patch("chronicler.main._load_config", return_value=cfg),
        patch("chronicler.main.log_utils.configure"),
        pytest.raises(RuntimeError, match="bootstrap_servers is required"),
    ):
        await _run()


@pytest.mark.asyncio
async def test_run_raises_when_scribe_queue_url_missing() -> None:
    cfg = _make_matik_config(scribe_url="")
    with (
        patch("chronicler.main._load_config", return_value=cfg),
        patch("chronicler.main.log_utils.configure"),
        pytest.raises(RuntimeError, match="scribe_queue_url is required"),
    ):
        await _run()


@pytest.mark.asyncio
async def test_run_wires_publishers_and_invokes_loop() -> None:
    cfg = _make_matik_config()
    fake_consumer = MagicMock()
    fake_loop = MagicMock(run=AsyncMock())
    with (
        patch("chronicler.main._load_config", return_value=cfg),
        patch("chronicler.main.log_utils.configure"),
        patch("chronicler.main.boto3.Session") as boto3_session,
        patch("chronicler.main.SQSPublisher") as scribe_pub_cls,
        patch("chronicler.main.SQSClient") as enricher_sqs_cls,
        patch("chronicler.main.EnrichmentPublisher") as enricher_pub_cls,
        patch("chronicler.main.build_consumer", return_value=fake_consumer) as build,
        patch("chronicler.main.KafkaConsumerLoop", return_value=fake_loop) as loop_cls,
        patch("chronicler.main.install_signal_handlers") as install_sigs,
    ):
        boto3_session.return_value.client.return_value = MagicMock()
        scribe_pub_cls.return_value = MagicMock()
        enricher_pub_cls.return_value = MagicMock()
        enricher_sqs_cls.return_value = MagicMock()

        await _run()

    boto3_session.assert_called_once_with(region_name="us-east-1")
    scribe_pub_cls.assert_called_once()
    enricher_pub_cls.assert_called_once()
    build.assert_called_once_with(cfg.chronicler.kafka)
    loop_cls.assert_called_once()
    install_sigs.assert_called_once_with(fake_loop)
    fake_loop.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_without_enricher_queue_skips_enrichment_publisher() -> None:
    cfg = _make_matik_config(enricher_url=None)
    fake_loop = MagicMock(run=AsyncMock())
    with (
        patch("chronicler.main._load_config", return_value=cfg),
        patch("chronicler.main.log_utils.configure"),
        patch("chronicler.main.boto3.Session"),
        patch("chronicler.main.SQSPublisher"),
        patch("chronicler.main.EnrichmentPublisher") as enricher_pub_cls,
        patch("chronicler.main.build_consumer"),
        patch("chronicler.main.KafkaConsumerLoop", return_value=fake_loop),
        patch("chronicler.main.install_signal_handlers"),
    ):
        await _run()

    enricher_pub_cls.assert_not_called()
    fake_loop.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_starts_and_shuts_down_telescope() -> None:
    cfg = _make_matik_config(telescope_enabled=True)
    fake_loop = MagicMock(run=AsyncMock())
    telescope_instance = MagicMock()
    with (
        patch("chronicler.main._load_config", return_value=cfg),
        patch("chronicler.main.log_utils.configure"),
        patch("chronicler.main.boto3.Session"),
        patch("chronicler.main.SQSPublisher"),
        patch("chronicler.main.SQSClient"),
        patch("chronicler.main.EnrichmentPublisher"),
        patch(
            "chronicler.main.TelescopeClient", return_value=telescope_instance
        ) as telescope_cls,
        patch("chronicler.main.build_consumer"),
        patch("chronicler.main.KafkaConsumerLoop", return_value=fake_loop),
        patch("chronicler.main.install_signal_handlers"),
    ):
        await _run()

    telescope_cls.assert_called_once()
    telescope_instance.start.assert_called_once()
    telescope_instance.shutdown.assert_called_once()


@pytest.mark.asyncio
async def test_run_constructs_metrics_when_telescope_enabled() -> None:
    """ChroniclerMetrics + SQSPublisherMetrics are built from telescope.meter
    and passed into the consumer + scribe publisher."""
    cfg = _make_matik_config(telescope_enabled=True)
    fake_loop = MagicMock(run=AsyncMock())
    telescope_instance = MagicMock()
    chronicler_metrics_instance = MagicMock()
    publisher_metrics_instance = MagicMock()
    with (
        patch("chronicler.main._load_config", return_value=cfg),
        patch("chronicler.main.log_utils.configure"),
        patch("chronicler.main.boto3.Session"),
        patch("chronicler.main.SQSPublisher") as scribe_pub_cls,
        patch("chronicler.main.SQSClient"),
        patch("chronicler.main.EnrichmentPublisher"),
        patch("chronicler.main.TelescopeClient", return_value=telescope_instance),
        patch(
            "chronicler.main.ChroniclerMetrics",
            return_value=chronicler_metrics_instance,
        ) as chronicler_metrics_cls,
        patch(
            "chronicler.main.SQSPublisherMetrics",
            return_value=publisher_metrics_instance,
        ) as publisher_metrics_cls,
        patch("chronicler.main.build_consumer"),
        patch("chronicler.main.KafkaConsumerLoop", return_value=fake_loop) as loop_cls,
        patch("chronicler.main.install_signal_handlers"),
    ):
        await _run()

    chronicler_metrics_cls.assert_called_once_with(
        telescope_instance.meter, "chronicler"
    )
    publisher_metrics_cls.assert_called_once_with(
        telescope_instance.meter, "chronicler"
    )
    # Scribe SQSPublisher receives the publisher metrics.
    scribe_kwargs = scribe_pub_cls.call_args.kwargs
    assert scribe_kwargs["metrics"] is publisher_metrics_instance
    # Consumer loop receives the chronicler metrics.
    loop_kwargs = loop_cls.call_args.kwargs
    assert loop_kwargs["metrics"] is chronicler_metrics_instance


@pytest.mark.asyncio
async def test_run_passes_none_metrics_when_telescope_disabled() -> None:
    cfg = _make_matik_config(telescope_enabled=False)
    fake_loop = MagicMock(run=AsyncMock())
    with (
        patch("chronicler.main._load_config", return_value=cfg),
        patch("chronicler.main.log_utils.configure"),
        patch("chronicler.main.boto3.Session"),
        patch("chronicler.main.SQSPublisher") as scribe_pub_cls,
        patch("chronicler.main.SQSClient"),
        patch("chronicler.main.EnrichmentPublisher"),
        patch("chronicler.main.build_consumer"),
        patch("chronicler.main.KafkaConsumerLoop", return_value=fake_loop) as loop_cls,
        patch("chronicler.main.install_signal_handlers"),
    ):
        await _run()

    assert scribe_pub_cls.call_args.kwargs["metrics"] is None
    assert loop_cls.call_args.kwargs["metrics"] is None


class TestMain:
    def test_returns_zero_on_clean_exit(self) -> None:
        with patch("chronicler.main.asyncio.run") as run:
            run.return_value = None
            assert main() == 0

    def test_returns_one_on_unhandled_exception(self) -> None:
        with patch("chronicler.main.asyncio.run", side_effect=RuntimeError("boom")):
            assert main() == 1
