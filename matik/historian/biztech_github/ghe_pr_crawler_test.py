"""Tests for GHE PR Crawler."""

import json
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.clients.ghe_client import GHEOrg, GHERepo, PRWithFiles
from common.clients.sqs_publisher import SQSPublisher
from common.constants import TASK_ID_HEADER
from common.models.biztech_github_config import BiztechGitHubConfig
from common.models.ghe_pr import GHEPullRequest
from common.utils.datetime_utils import utc_now_naive
from historian.biztech_github.ghe_pr_crawler import (
    DEFAULT_CUTOFF_DATE,
    DEFAULT_MAX_CONCURRENT_REPOS,
    DEFAULT_PAGE_SIZE,
    DEFAULT_TRACKER_LOOKBACK_DAYS,
    CrawlerMetrics,
    GHEPRCrawler,
)


def make_pr(
    pull_request_id: int,
    pull_request_number: int,
    *,
    title: str = "Test PR",
    merged: bool = True,
    state: str = "closed",
    org_id: int = 100,
    org_login: str = "test-org",
    repo_id: int = 200,
    repo_name: str = "test-repo",
) -> GHEPullRequest:
    """Build a GHEPullRequest fixture with required org/repo identity fields."""
    return GHEPullRequest(
        pull_request_id=pull_request_id,
        pull_request_number=pull_request_number,
        org_id=org_id,
        org_login=org_login,
        repo_id=repo_id,
        repo_name=repo_name,
        title=title,
        merged=merged,
        state=state,
        locked=False,
        created_at=utc_now_naive(),
        target_branch_name="main",
    )


@pytest.fixture
def mock_config() -> BiztechGitHubConfig:
    """Create a mock BiztechGitHubConfig."""
    return BiztechGitHubConfig(
        ghe_base_url="https://github.example.com",
        ghe_app_client_id="test-client-id",
        ghe_app_installation_id="12345",
        ghe_app_id="67890",
        ghe_pr_summary_prompt="Summarize this PR",
        organizations=["test-org"],
        sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/scribe-historian",
    )


@pytest.fixture
def mock_ghe_client() -> MagicMock:
    """Create a mock GHEClient."""
    client = MagicMock()

    # Make async methods return AsyncMock
    client.get_organization = AsyncMock()
    client.get_repos = AsyncMock()
    client.list_pull_requests_with_hash_cache = AsyncMock()

    return client


@pytest.fixture
def mock_matik_client() -> MagicMock:
    """Create a mock MatikApiClient."""
    return MagicMock()


@pytest.fixture
def mock_sqs_publisher() -> MagicMock:
    """Create a mock SQSPublisher."""
    publisher = MagicMock(spec=SQSPublisher)
    publisher.send = AsyncMock()
    return publisher


class TestGHEPRCrawlerDefaults:
    """Test default value handling."""

    def test_get_page_size_with_config(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test page size from config."""
        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_client_id="test-client-id",
            ghe_app_installation_id="12345",
            ghe_app_id="67890",
            organizations=["test-org"],
            page_size=50,
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/scribe-historian",
        )
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=config,
        )
        assert crawler._get_page_size() == 50

    def test_get_page_size_without_config(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test page size default when not set in config."""
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )
        assert crawler._get_page_size() == DEFAULT_PAGE_SIZE

    def test_get_configured_cutoff_with_config(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test cutoff date from config."""
        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_client_id="test-client-id",
            ghe_app_installation_id="12345",
            ghe_app_id="67890",
            organizations=["test-org"],
            cutoff_date="2024-01-01T00:00:00",
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/scribe-historian",
        )
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=config,
        )
        result = crawler._get_configured_cutoff()
        assert result == datetime(2024, 1, 1, 0, 0, 0)

    def test_get_configured_cutoff_without_config(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test cutoff date default when not set in config."""
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )
        assert crawler._get_configured_cutoff() == DEFAULT_CUTOFF_DATE

    def test_get_tracker_lookback_days_with_config(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test tracker lookback days from config."""
        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_client_id="test-client-id",
            ghe_app_installation_id="12345",
            ghe_app_id="67890",
            organizations=["test-org"],
            tracker_lookback_days=3,
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/scribe-historian",
        )
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=config,
        )
        assert crawler._get_tracker_lookback_days() == 3

    def test_get_tracker_lookback_days_without_config(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test tracker lookback days default when not set in config."""
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )
        assert crawler._get_tracker_lookback_days() == DEFAULT_TRACKER_LOOKBACK_DAYS

    def test_get_configured_cutoff_invalid_format(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test cutoff date fallback on invalid format."""
        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_client_id="test-client-id",
            ghe_app_installation_id="12345",
            ghe_app_id="67890",
            organizations=["test-org"],
            cutoff_date="not-a-valid-date",
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/scribe-historian",
        )
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=config,
        )
        assert crawler._get_configured_cutoff() == DEFAULT_CUTOFF_DATE


class TestGHEPRCrawlerRun:
    """Test the main run method."""

    @pytest.mark.asyncio
    async def test_run_processes_organizations(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test that run() processes all configured organizations."""
        # Setup mock responses
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_ghe_client.get_organization.return_value = mock_org
        mock_ghe_client.get_repos.return_value = []

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        result = await crawler.run()

        assert result == 0
        mock_ghe_client.get_organization.assert_called_once_with("test-org")


class TestGHEPRCrawlerProcessRepository:
    """Test repository processing."""

    @pytest.mark.asyncio
    async def test_process_repository_full_flow(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test full repository processing flow."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_repo = GHERepo(repo_id=200, repo_name="test-repo", org_id=100)

        # Mock PR to be returned (client already stamped org/repo identity)
        mock_pr = make_pr(300, 300)
        mock_ghe_client.list_pull_requests_with_hash_cache.return_value = [
            PRWithFiles(pr=mock_pr, files=[])
        ]

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        # Setup matik_client read mocks (tracker cutoff and hash cache only)
        def get_side_effect(
            path: str, params: Any = None, headers: Any = None
        ) -> bytes:
            if "/v1/ghe/tracker/" in path:
                return json.dumps({"exists": False}).encode()
            elif "/v1/ghe/pr/hashes/" in path:
                return json.dumps([]).encode()
            return b"{}"

        mock_matik_client.get_request.side_effect = get_side_effect

        result = await crawler.process_repository(mock_org, mock_repo)

        assert result == 1  # One PR processed
        # PR and tracker messages sent via SQS
        assert mock_sqs_publisher.send.call_count == 2  # 1 PR + 1 tracker


class TestGHEPRCrawlerTracker:
    """Test tracker handling."""

    @pytest.mark.asyncio
    async def test_get_tracker_cutoff_exists(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test tracker cutoff when tracker exists."""
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        mock_matik_client.get_request.return_value = json.dumps(
            {
                "exists": True,
                "cutoff_date": "2024-06-01T00:00:00",
            }
        ).encode()

        result = await crawler._get_tracker_cutoff(100, 200)

        assert result == datetime(2024, 6, 1, 0, 0, 0)

    @pytest.mark.asyncio
    async def test_get_tracker_cutoff_not_exists(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test tracker cutoff when tracker doesn't exist."""
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        mock_matik_client.get_request.return_value = json.dumps(
            {"exists": False}
        ).encode()

        result = await crawler._get_tracker_cutoff(100, 200)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_tracker_cutoff_returns_none_for_none_ids(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test tracker cutoff returns None for None IDs."""
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        result = await crawler._get_tracker_cutoff(None, None)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_tracker_cutoff_returns_none_on_error(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test tracker cutoff returns None on API error."""
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        mock_matik_client.get_request.side_effect = Exception("API error")

        result = await crawler._get_tracker_cutoff(100, 200)

        assert result is None


class TestGHEPRCrawlerUpdateTracker:
    """Test tracker update handling."""

    @pytest.mark.asyncio
    async def test_update_tracker_success(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test successful tracker update sends to SQS."""
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        await crawler._update_tracker(100, 200, 10)

        mock_sqs_publisher.send.assert_called_once()
        assert crawler.metrics.sqs_messages_sent == 1

    @pytest.mark.asyncio
    async def test_update_tracker_skipped_for_none_ids(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test tracker update skipped for None IDs."""
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        await crawler._update_tracker(None, None, 10)

        mock_sqs_publisher.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_tracker_handles_error(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test tracker update handles SQS error gracefully."""
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        mock_sqs_publisher.send.side_effect = Exception("SQS error")

        # Should not raise, just log
        await crawler._update_tracker(100, 200, 10)


class TestGHEPRCrawlerPostPRsBatch:
    """Test PR batch posting."""

    @pytest.mark.asyncio
    async def test_post_prs_batch_success(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test successful PR batch sending to SQS."""
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        mock_prs = [
            make_pr(100, 100, title="PR 1", merged=True, state="closed"),
            make_pr(101, 101, title="PR 2", merged=False, state="open"),
        ]

        result = await crawler._post_prs_batch(mock_prs)

        assert result == 2
        assert mock_sqs_publisher.send.call_count == 2
        assert crawler.metrics.sqs_messages_sent == 2

    @pytest.mark.asyncio
    async def test_post_prs_batch_stamps_entered_at(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """DLQ retry staleness guard (ADR 024): Historian stamps entered_at on
        every published base message."""
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        await crawler._post_prs_batch([make_pr(100, 100, title="PR 1")])

        published_message = mock_sqs_publisher.send.call_args[0][0]
        assert published_message.entered_at is not None


class TestGHEPRCrawlerExceptionHandling:
    """Test exception handling in crawler methods."""

    @pytest.mark.asyncio
    async def test_run_handles_org_exception(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test run() continues processing on org failure."""
        # Config with two orgs
        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_client_id="test-client-id",
            ghe_app_installation_id="12345",
            ghe_app_id="67890",
            ghe_pr_summary_prompt="Summarize this PR",
            organizations=["org1", "org2"],
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/scribe-historian",
        )

        # First org fails, second succeeds
        mock_org2 = GHEOrg(org_id=200, org_login="org2")
        mock_ghe_client.get_organization.side_effect = [
            Exception("API error for org1"),
            mock_org2,
        ]
        mock_ghe_client.get_repos.return_value = []

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=config,
        )

        # Should not raise, should continue to org2
        result = await crawler.run()

        assert result == 0  # No PRs from org2 (empty repos)
        assert mock_ghe_client.get_organization.call_count == 2

    @pytest.mark.asyncio
    async def test_process_organization_handles_repo_exception(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test process_organization continues on repo failure."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")

        # Two repos - first will fail, second will succeed
        repo1 = GHERepo(repo_id=1, repo_name="repo1", org_id=100)
        repo2 = GHERepo(repo_id=2, repo_name="repo2", org_id=100)

        mock_ghe_client.get_organization.return_value = mock_org
        mock_ghe_client.get_repos.return_value = [repo1, repo2]

        # First repo's PR fetch fails, second succeeds
        call_count = 0

        async def list_prs_side_effect(
            org: GHEOrg,
            repo: GHERepo,
            page_size: int,
            cutoff: Any,
            hash_cache: Any,
        ) -> list[PRWithFiles]:
            nonlocal call_count
            call_count += 1
            if repo.repo_name == "repo1":
                raise Exception("Repo1 API error")
            return [
                PRWithFiles(
                    pr=make_pr(300, 300, repo_id=2, repo_name="repo2"), files=[]
                )
            ]

        mock_ghe_client.list_pull_requests_with_hash_cache.side_effect = (
            list_prs_side_effect
        )

        def get_side_effect(
            path: str, params: Any = None, headers: Any = None
        ) -> bytes:
            if "/v1/ghe/tracker/" in path:
                return json.dumps({"exists": False}).encode()
            elif "/v1/ghe/pr/hashes/" in path:
                return json.dumps([]).encode()
            return b"{}"

        mock_matik_client.get_request.side_effect = get_side_effect

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        # Should not raise, should continue to repo2
        result = await crawler.process_organization("test-org")

        # One PR from repo2
        assert result == 1


class TestGHEPRCrawlerRequestID:
    """Test X-Task-ID handling."""

    def test_get_request_headers_with_task_id(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test _get_request_headers returns X-Task-ID when task_id is set."""
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        with patch("historian.biztech_github.ghe_pr_crawler.log_utils") as mock_log:
            mock_log.get_task_id.return_value = "test-request-id-123"

            headers = crawler._get_request_headers()

            assert headers == {TASK_ID_HEADER: "test-request-id-123"}
            mock_log.get_task_id.assert_called_once()

    def test_get_request_headers_without_task_id(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test _get_request_headers returns empty dict when no task_id."""
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        with patch("historian.biztech_github.ghe_pr_crawler.log_utils") as mock_log:
            mock_log.get_task_id.return_value = None

            headers = crawler._get_request_headers()

            assert headers == {}

    @pytest.mark.asyncio
    async def test_run_sets_and_clears_task_id(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test run() sets task_id at start and clears at end."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_ghe_client.get_organization.return_value = mock_org
        mock_ghe_client.get_repos.return_value = []

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        with patch("historian.biztech_github.ghe_pr_crawler.log_utils") as mock_log:
            mock_log.generate_task_id.return_value = "generated-request-id"
            mock_log.get_task_id.return_value = "generated-request-id"

            await crawler.run()

            mock_log.generate_task_id.assert_called_once()
            mock_log.clear_task_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_clears_task_id_on_exception(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test run() clears task_id even when exception occurs."""
        mock_ghe_client.get_organization.side_effect = Exception("API error")

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        with patch("historian.biztech_github.ghe_pr_crawler.log_utils") as mock_log:
            mock_log.generate_task_id.return_value = "generated-request-id"

            # Should not raise - exception is caught internally
            await crawler.run()

            mock_log.generate_task_id.assert_called_once()
            mock_log.clear_task_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_tracker_cutoff_passes_headers(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test _get_tracker_cutoff passes X-Task-ID header."""
        mock_matik_client.get_request.return_value = json.dumps(
            {"exists": False}
        ).encode()

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        with patch("historian.biztech_github.ghe_pr_crawler.log_utils") as mock_log:
            mock_log.get_task_id.return_value = "test-request-id"

            await crawler._get_tracker_cutoff(100, 200)

            # Check that get_request was called with headers (3rd positional arg)
            call_args = mock_matik_client.get_request.call_args
            assert call_args[0][2] == {TASK_ID_HEADER: "test-request-id"}


class TestGHEPRCrawlerConcurrency:
    """Test concurrent repository processing."""

    def test_get_max_concurrent_repos_with_config(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test max concurrent repos from config."""
        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_client_id="test-client-id",
            ghe_app_installation_id="12345",
            ghe_app_id="67890",
            organizations=["test-org"],
            max_concurrent_repos=10,
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/scribe-historian",
        )
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=config,
        )
        assert crawler._get_max_concurrent_repos() == 10

    def test_get_max_concurrent_repos_without_config(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test max concurrent repos default when no config."""
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )
        assert crawler._get_max_concurrent_repos() == DEFAULT_MAX_CONCURRENT_REPOS

    @pytest.mark.asyncio
    async def test_process_organization_concurrent_repos(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test that repos are processed concurrently."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")

        # Create multiple repos
        repos = [GHERepo(repo_id=i, repo_name=f"repo{i}", org_id=100) for i in range(3)]

        mock_ghe_client.get_organization.return_value = mock_org
        mock_ghe_client.get_repos.return_value = repos
        mock_ghe_client.list_pull_requests_with_hash_cache.return_value = []

        def get_side_effect(
            path: str, params: Any = None, headers: Any = None
        ) -> bytes:
            if "/v1/ghe/tracker/" in path:
                return json.dumps({"exists": False}).encode()
            elif "/v1/ghe/pr/hashes/" in path:
                return json.dumps([]).encode()
            return b"{}"

        mock_matik_client.get_request.side_effect = get_side_effect

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        result = await crawler.process_organization("test-org")

        # All repos should be processed
        assert result == 0  # No PRs, but repos processed
        assert crawler.metrics.repositories_processed == 3
        # PRs fetched once per repo
        assert mock_ghe_client.list_pull_requests_with_hash_cache.call_count == 3

    @pytest.mark.asyncio
    async def test_process_organization_partial_failure_continues(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test that failure in one repo doesn't stop others."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")

        # Create multiple repos - one will fail
        repos = [
            GHERepo(repo_id=1, repo_name="repo1", org_id=100),
            GHERepo(repo_id=2, repo_name="failing-repo", org_id=100),
            GHERepo(repo_id=3, repo_name="repo3", org_id=100),
        ]

        mock_ghe_client.get_organization.return_value = mock_org
        mock_ghe_client.get_repos.return_value = repos

        async def list_prs_side_effect(
            org: GHEOrg,
            repo: GHERepo,
            page_size: int,
            cutoff: Any,
            hash_cache: Any,
        ) -> list[PRWithFiles]:
            if repo.repo_name == "failing-repo":
                raise Exception("Repository API error")
            return [
                PRWithFiles(
                    pr=make_pr(
                        100, 100, repo_id=repo.repo_id, repo_name=repo.repo_name
                    ),
                    files=[],
                )
            ]

        mock_ghe_client.list_pull_requests_with_hash_cache.side_effect = (
            list_prs_side_effect
        )

        def get_side_effect(
            path: str, params: Any = None, headers: Any = None
        ) -> bytes:
            if "/v1/ghe/tracker/" in path:
                return json.dumps({"exists": False}).encode()
            elif "/v1/ghe/pr/hashes/" in path:
                return json.dumps([]).encode()
            return b"{}"

        mock_matik_client.get_request.side_effect = get_side_effect

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        # Should not raise, should return PRs from successful repos
        result = await crawler.process_organization("test-org")

        # 2 successful repos * 1 PR each = 2 PRs
        assert result == 2

    @pytest.mark.asyncio
    async def test_process_organization_respects_semaphore_limit(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test that semaphore limits concurrent processing."""
        # Set max_concurrent_repos to 2
        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_client_id="test-client-id",
            ghe_app_installation_id="12345",
            ghe_app_id="67890",
            organizations=["test-org"],
            max_concurrent_repos=2,
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/scribe-historian",
        )

        mock_org = GHEOrg(org_id=100, org_login="test-org")

        # Create more repos than the concurrency limit
        repos = [GHERepo(repo_id=i, repo_name=f"repo{i}", org_id=100) for i in range(5)]

        mock_ghe_client.get_organization.return_value = mock_org
        mock_ghe_client.get_repos.return_value = repos
        mock_ghe_client.list_pull_requests_with_hash_cache.return_value = []

        repo_ids_processed = []

        async def list_prs_side_effect(
            org: GHEOrg,
            repo: GHERepo,
            page_size: int,
            cutoff: Any,
            hash_cache: Any,
        ) -> list[PRWithFiles]:
            repo_ids_processed.append(repo.repo_id)
            return []

        mock_ghe_client.list_pull_requests_with_hash_cache.side_effect = (
            list_prs_side_effect
        )

        def get_side_effect(
            path: str, params: Any = None, headers: Any = None
        ) -> bytes:
            if "/v1/ghe/tracker/" in path:
                return json.dumps({"exists": False}).encode()
            elif "/v1/ghe/pr/hashes/" in path:
                return json.dumps([]).encode()
            return b"{}"

        mock_matik_client.get_request.side_effect = get_side_effect

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=config,
        )

        result = await crawler.process_organization("test-org")

        # All repos should be processed
        assert result == 0
        assert len(repo_ids_processed) == 5
        # Verify all unique repo_ids were processed
        assert set(repo_ids_processed) == {0, 1, 2, 3, 4}


class TestCrawlerMetrics:
    """Test CrawlerMetrics dataclass functionality."""

    def test_default_values(self) -> None:
        """Test that all default values are zero/empty."""
        metrics = CrawlerMetrics()
        assert metrics.organizations_processed == 0
        assert metrics.repositories_processed == 0
        assert metrics.prs_crawled == 0
        assert metrics.prs_upserted == 0
        assert metrics.api_calls_ghe == 0
        assert metrics.api_calls_matik == 0
        assert metrics.sqs_messages_sent == 0
        assert metrics.total_run_duration == 0.0
        assert metrics.org_durations == []
        assert metrics.repo_durations == []
        assert metrics.org_errors == 0
        assert metrics.repo_errors == 0

    def test_to_dict_empty_metrics(self) -> None:
        """Test to_dict() with default values."""
        metrics = CrawlerMetrics()
        result = metrics.to_dict()

        assert result["organizations_processed"] == 0
        assert result["repositories_processed"] == 0
        assert result["prs_crawled"] == 0
        assert result["prs_upserted"] == 0
        assert result["api_calls_ghe"] == 0
        assert result["api_calls_matik"] == 0
        assert result["sqs_messages_sent"] == 0
        assert result["total_api_calls"] == 0
        assert result["total_run_duration_sec"] == 0
        assert result["avg_org_duration_sec"] == 0
        assert result["avg_repo_duration_sec"] == 0
        assert result["org_errors"] == 0
        assert result["repo_errors"] == 0

    def test_to_dict_with_populated_values(self) -> None:
        """Test to_dict() with real data."""
        metrics = CrawlerMetrics(
            organizations_processed=2,
            repositories_processed=5,
            prs_crawled=100,
            prs_upserted=90,
            api_calls_ghe=20,
            api_calls_matik=30,
            total_run_duration=120.5,
            org_durations=[30.0, 60.0],
            repo_durations=[5.0, 10.0, 15.0, 20.0, 10.0],
            org_errors=1,
            repo_errors=2,
        )
        result = metrics.to_dict()

        assert result["organizations_processed"] == 2
        assert result["repositories_processed"] == 5
        assert result["prs_crawled"] == 100
        assert result["prs_upserted"] == 90
        assert result["api_calls_ghe"] == 20
        assert result["api_calls_matik"] == 30
        assert result["total_run_duration_sec"] == 120.5
        assert result["org_errors"] == 1
        assert result["repo_errors"] == 2

    def test_to_dict_avg_org_duration_calculation(self) -> None:
        """Test average org duration calculation."""
        metrics = CrawlerMetrics(org_durations=[10.0, 20.0, 30.0])
        result = metrics.to_dict()
        # (10 + 20 + 30) / 3 = 20
        assert result["avg_org_duration_sec"] == 20.0

    def test_to_dict_avg_repo_duration_calculation(self) -> None:
        """Test average repo duration calculation."""
        metrics = CrawlerMetrics(repo_durations=[5.0, 10.0, 15.0, 20.0])
        result = metrics.to_dict()
        # (5 + 10 + 15 + 20) / 4 = 12.5
        assert result["avg_repo_duration_sec"] == 12.5

    def test_to_dict_empty_durations_returns_zero(self) -> None:
        """Test that empty durations lists return 0 for averages."""
        metrics = CrawlerMetrics(org_durations=[], repo_durations=[])
        result = metrics.to_dict()
        assert result["avg_org_duration_sec"] == 0
        assert result["avg_repo_duration_sec"] == 0

    def test_to_dict_total_api_calls_sum(self) -> None:
        """Test that total_api_calls equals ghe + matik."""
        metrics = CrawlerMetrics(api_calls_ghe=15, api_calls_matik=25)
        result = metrics.to_dict()
        assert result["total_api_calls"] == 40


class TestGHEPRCrawlerMetricsTracking:
    """Test metrics tracking in crawler methods."""

    @pytest.mark.asyncio
    async def test_run_tracks_total_duration(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test that run() tracks total_run_duration."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_ghe_client.get_organization.return_value = mock_org
        mock_ghe_client.get_repos.return_value = []

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        await crawler.run()

        assert crawler.metrics.total_run_duration > 0

    @pytest.mark.asyncio
    async def test_run_tracks_org_durations(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test that run() populates org_durations list."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_ghe_client.get_organization.return_value = mock_org
        mock_ghe_client.get_repos.return_value = []

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        await crawler.run()

        assert len(crawler.metrics.org_durations) == 1
        assert crawler.metrics.org_durations[0] > 0

    @pytest.mark.asyncio
    async def test_run_increments_organizations_processed(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test that run() increments organizations_processed on success."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_ghe_client.get_organization.return_value = mock_org
        mock_ghe_client.get_repos.return_value = []

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        await crawler.run()

        assert crawler.metrics.organizations_processed == 1

    @pytest.mark.asyncio
    async def test_run_increments_org_errors_on_failure(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test that run() increments org_errors on exception."""
        mock_ghe_client.get_organization.side_effect = Exception("API error")

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        await crawler.run()

        assert crawler.metrics.org_errors == 1
        assert crawler.metrics.organizations_processed == 0

    @pytest.mark.asyncio
    async def test_run_logs_metrics_at_end(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test that run() logs metrics at the end."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_ghe_client.get_organization.return_value = mock_org
        mock_ghe_client.get_repos.return_value = []

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        with patch("historian.biztech_github.ghe_pr_crawler.logger") as mock_logger:
            await crawler.run()

            # Check that logger.info was called with metrics
            metrics_call_found = False
            for call in mock_logger.info.call_args_list:
                if len(call.args) > 0 and "Crawler metrics" in str(call.args[0]):
                    metrics_call_found = True
                    break
            assert metrics_call_found, "Expected logger.info to be called with metrics"

    @pytest.mark.asyncio
    async def test_process_organization_tracks_ghe_api_calls(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test that process_organization tracks GHE API calls."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_ghe_client.get_organization.return_value = mock_org
        mock_ghe_client.get_repos.return_value = []

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        await crawler.process_organization("test-org")

        # get_organization and get_repos each count as 1 API call
        assert crawler.metrics.api_calls_ghe == 2

    @pytest.mark.asyncio
    async def test_process_organization_tracks_repo_durations(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test that process_organization tracks repo_durations."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_repo = GHERepo(repo_id=200, repo_name="test-repo", org_id=100)

        mock_ghe_client.get_organization.return_value = mock_org
        mock_ghe_client.get_repos.return_value = [mock_repo]
        mock_ghe_client.list_pull_requests_with_hash_cache.return_value = []

        def get_side_effect(
            path: str, params: Any = None, headers: Any = None
        ) -> bytes:
            if "/v1/ghe/tracker/" in path:
                return json.dumps({"exists": False}).encode()
            elif "/v1/ghe/pr/hashes/" in path:
                return json.dumps([]).encode()
            return b"{}"

        mock_matik_client.get_request.side_effect = get_side_effect

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        await crawler.process_organization("test-org")

        assert len(crawler.metrics.repo_durations) == 1
        assert crawler.metrics.repo_durations[0] > 0

    @pytest.mark.asyncio
    async def test_process_organization_increments_repo_errors(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test that process_organization increments repo_errors on failure."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_repo = GHERepo(repo_id=200, repo_name="test-repo", org_id=100)

        mock_ghe_client.get_organization.return_value = mock_org
        mock_ghe_client.get_repos.return_value = [mock_repo]
        mock_ghe_client.list_pull_requests_with_hash_cache.side_effect = Exception(
            "PR fetch API error"
        )

        def get_side_effect(
            path: str, params: Any = None, headers: Any = None
        ) -> bytes:
            if "/v1/ghe/tracker/" in path:
                return json.dumps({"exists": False}).encode()
            elif "/v1/ghe/pr/hashes/" in path:
                return json.dumps([]).encode()
            return b"{}"

        mock_matik_client.get_request.side_effect = get_side_effect

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        await crawler.process_organization("test-org")

        assert crawler.metrics.repo_errors == 1

    @pytest.mark.asyncio
    async def test_process_repository_increments_repositories_processed(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test that process_repository increments repositories_processed."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_repo = GHERepo(repo_id=200, repo_name="test-repo", org_id=100)

        mock_ghe_client.list_pull_requests_with_hash_cache.return_value = []

        def get_side_effect(
            path: str, params: Any = None, headers: Any = None
        ) -> bytes:
            if "/v1/ghe/tracker/" in path:
                return json.dumps({"exists": False}).encode()
            elif "/v1/ghe/pr/hashes/" in path:
                return json.dumps([]).encode()
            return b"{}"

        mock_matik_client.get_request.side_effect = get_side_effect

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        await crawler.process_repository(mock_org, mock_repo)

        assert crawler.metrics.repositories_processed == 1

    @pytest.mark.asyncio
    async def test_process_repository_tracks_prs_crawled(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test that process_repository tracks prs_crawled count."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_repo = GHERepo(repo_id=200, repo_name="test-repo", org_id=100)

        mock_prs = [
            make_pr(300, 300, title="Test PR", merged=True, state="closed"),
            make_pr(301, 301, title="Test PR 2", merged=False, state="open"),
        ]
        mock_ghe_client.list_pull_requests_with_hash_cache.return_value = [
            PRWithFiles(pr=p, files=[]) for p in mock_prs
        ]

        def get_side_effect(
            path: str, params: Any = None, headers: Any = None
        ) -> bytes:
            if "/v1/ghe/tracker/" in path:
                return json.dumps({"exists": False}).encode()
            elif "/v1/ghe/pr/hashes/" in path:
                return json.dumps([]).encode()
            return b"{}"

        mock_matik_client.get_request.side_effect = get_side_effect

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        await crawler.process_repository(mock_org, mock_repo)

        assert crawler.metrics.prs_crawled == 2

    @pytest.mark.asyncio
    async def test_process_repository_tracks_prs_upserted(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test that process_repository tracks prs_upserted as messages sent."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_repo = GHERepo(repo_id=200, repo_name="test-repo", org_id=100)

        mock_prs = [
            make_pr(300, 300, title="Test PR", merged=True, state="closed"),
        ]
        mock_ghe_client.list_pull_requests_with_hash_cache.return_value = [
            PRWithFiles(pr=p, files=[]) for p in mock_prs
        ]

        def get_side_effect(
            path: str, params: Any = None, headers: Any = None
        ) -> bytes:
            if "/v1/ghe/tracker/" in path:
                return json.dumps({"exists": False}).encode()
            elif "/v1/ghe/pr/hashes/" in path:
                return json.dumps([]).encode()
            return b"{}"

        mock_matik_client.get_request.side_effect = get_side_effect

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        await crawler.process_repository(mock_org, mock_repo)

        assert crawler.metrics.prs_upserted == 1

    @pytest.mark.asyncio
    async def test_process_repository_tracks_ghe_api_call(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """Test that process_repository tracks GHE API call for list_pull_requests."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_repo = GHERepo(repo_id=200, repo_name="test-repo", org_id=100)

        mock_ghe_client.list_pull_requests_with_hash_cache.return_value = []

        def get_side_effect(
            path: str, params: Any = None, headers: Any = None
        ) -> bytes:
            if "/v1/ghe/tracker/" in path:
                return json.dumps({"exists": False}).encode()
            elif "/v1/ghe/pr/hashes/" in path:
                return json.dumps([]).encode()
            return b"{}"

        mock_matik_client.get_request.side_effect = get_side_effect

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        await crawler.process_repository(mock_org, mock_repo)

        # list_pull_requests_with_hash_cache counts as 1 GHE API call
        assert crawler.metrics.api_calls_ghe == 1


class TestBuildServiceIndex:
    """Test _build_service_index method."""

    def _make_crawler(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> GHEPRCrawler:
        return GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

    def test_single_repo(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test index with a single repo entry."""
        crawler = self._make_crawler(
            mock_ghe_client, mock_matik_client, mock_config, mock_sqs_publisher
        )
        summary = {
            "svc_foo": {
                "git_url": "https://github.example.com/org/repo",
                "git_path": "/",
            },
        }
        result = crawler._build_service_index(summary)
        assert result == {
            "https://github.example.com/org/repo": [("svc_foo", "/")],
        }

    def test_multiple_repos_and_monorepo(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test index groups entries by repo URL, including monorepo."""
        crawler = self._make_crawler(
            mock_ghe_client, mock_matik_client, mock_config, mock_sqs_publisher
        )
        summary = {
            "svc_parent": {
                "git_url": "https://github.example.com/org/monorepo",
                "git_path": "/",
            },
            "svc_child_a": {
                "git_url": "https://github.example.com/org/monorepo",
                "git_path": "projects/child_a/",
            },
            "svc_other": {
                "git_url": "https://github.example.com/org/other-repo",
                "git_path": "/",
            },
        }
        result = crawler._build_service_index(summary)
        assert len(result) == 2
        monorepo_entries = result["https://github.example.com/org/monorepo"]
        assert len(monorepo_entries) == 2
        names = {name for name, _ in monorepo_entries}
        assert names == {"svc_parent", "svc_child_a"}
        assert result["https://github.example.com/org/other-repo"] == [
            ("svc_other", "/"),
        ]

    def test_empty_summary(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test index with empty summary data."""
        crawler = self._make_crawler(
            mock_ghe_client, mock_matik_client, mock_config, mock_sqs_publisher
        )
        result = crawler._build_service_index({})
        assert result == {}

    def test_missing_git_url_skipped(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test entries without git_url are skipped."""
        crawler = self._make_crawler(
            mock_ghe_client, mock_matik_client, mock_config, mock_sqs_publisher
        )
        summary = {
            "svc_no_url": {"git_path": "/"},
            "svc_with_url": {
                "git_url": "https://github.example.com/org/repo",
                "git_path": "/",
            },
        }
        result = crawler._build_service_index(summary)
        assert result == {
            "https://github.example.com/org/repo": [("svc_with_url", "/")],
        }


class TestMatchServices:
    """Test _match_services method."""

    def _make_crawler(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> GHEPRCrawler:
        return GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

    def test_non_monorepo_single_entry(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test non-monorepo returns the single service regardless of files."""
        crawler = self._make_crawler(
            mock_ghe_client, mock_matik_client, mock_config, mock_sqs_publisher
        )
        entries = [("svc_foo", "/")]
        result = crawler._match_services(entries, ["any/file.py"])
        assert result == ["svc_foo"]

    def test_monorepo_matches_specific_path(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test monorepo matches files to specific git_path entries."""
        crawler = self._make_crawler(
            mock_ghe_client, mock_matik_client, mock_config, mock_sqs_publisher
        )
        entries = [
            ("svc_parent", "/"),
            ("svc_child_a", "projects/child_a/"),
            ("svc_child_b", "projects/child_b/"),
        ]
        result = crawler._match_services(entries, ["projects/child_a/main.py"])
        assert result == ["svc_child_a"]

    def test_monorepo_matches_multiple_services(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test monorepo matches files spanning multiple services."""
        crawler = self._make_crawler(
            mock_ghe_client, mock_matik_client, mock_config, mock_sqs_publisher
        )
        entries = [
            ("svc_parent", "/"),
            ("svc_child_a", "projects/child_a/"),
            ("svc_child_b", "projects/child_b/"),
        ]
        result = crawler._match_services(
            entries,
            ["projects/child_a/main.py", "projects/child_b/config.yml"],
        )
        assert result is not None
        assert set(result) == {"svc_child_a", "svc_child_b"}

    def test_monorepo_skips_root_entry(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test monorepo skips root '/' entries."""
        crawler = self._make_crawler(
            mock_ghe_client, mock_matik_client, mock_config, mock_sqs_publisher
        )
        entries = [
            ("svc_parent", "/"),
            ("svc_child", "projects/child/"),
        ]
        result = crawler._match_services(entries, ["projects/child/app.py"])
        assert result == ["svc_child"]

    def test_monorepo_no_match_returns_empty_list(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test monorepo returns empty list when no specific paths match."""
        crawler = self._make_crawler(
            mock_ghe_client, mock_matik_client, mock_config, mock_sqs_publisher
        )
        entries = [
            ("svc_parent", "/"),
            ("svc_child", "projects/child/"),
        ]
        result = crawler._match_services(entries, ["some/unrelated/file.py"])
        assert result == []


class TestGHEPRCrawlerJobMetrics:
    """Test job_metrics integration in crawler."""

    @pytest.mark.asyncio
    async def test_run_starts_and_records_job_on_success(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test run() calls start_job and record_job on success."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_ghe_client.get_organization.return_value = mock_org
        mock_ghe_client.get_repos.return_value = []

        mock_job_metrics = MagicMock()
        mock_record_job = MagicMock()
        mock_job_metrics.start_job.return_value = mock_record_job

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
            job_metrics=mock_job_metrics,
        )

        result = await crawler.run()

        assert result == 0
        mock_job_metrics.start_job.assert_called_once_with("ghe_pr")
        mock_record_job.assert_called_once_with(0, None)

    @pytest.mark.asyncio
    async def test_run_records_job_error_on_org_failure(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test run() calls record_job_error when org processing fails."""
        mock_ghe_client.get_organization.side_effect = Exception("API error")

        mock_job_metrics = MagicMock()
        mock_record_job = MagicMock()
        mock_job_metrics.start_job.return_value = mock_record_job

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
            job_metrics=mock_job_metrics,
        )

        await crawler.run()

        mock_job_metrics.record_job_error.assert_called_once_with(
            "ghe_pr", "org_processing"
        )
        # record_job should still be called (success path in try block completes)
        mock_record_job.assert_called_once_with(0, None)

    @pytest.mark.asyncio
    async def test_run_records_job_failure_on_unexpected_exception(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test run() calls record_job with exception when outer try fails."""
        mock_ghe_client.get_organization.side_effect = Exception("org error")

        mock_job_metrics = MagicMock()
        mock_record_job = MagicMock()
        mock_job_metrics.start_job.return_value = mock_record_job

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
            job_metrics=mock_job_metrics,
        )

        # Use a list subclass that raises on append. This causes the inner
        # finally block to raise, which escapes the inner try/except and is
        # caught by the outer except block calling record_job(0, e).
        class RaisingList(list[float]):
            def append(self, item: float) -> None:
                raise RuntimeError("unexpected")

        crawler.metrics.org_durations = RaisingList()

        with pytest.raises(RuntimeError, match="unexpected"):
            await crawler.run()

        # The outer except should have called record_job(0, exception)
        mock_record_job.assert_called_once()
        assert mock_record_job.call_args[0][0] == 0
        assert isinstance(mock_record_job.call_args[0][1], RuntimeError)

    @pytest.mark.asyncio
    async def test_process_organization_records_job_error_on_repo_failure(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test process_organization calls record_job_error on repo failure."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_repo = GHERepo(repo_id=200, repo_name="test-repo", org_id=100)

        mock_ghe_client.get_organization.return_value = mock_org
        mock_ghe_client.get_repos.return_value = [mock_repo]
        mock_ghe_client.list_pull_requests_with_hash_cache.side_effect = Exception(
            "PR fetch API error"
        )

        def get_side_effect(
            path: str, params: Any = None, headers: Any = None
        ) -> bytes:
            if "/v1/ghe/tracker/" in path:
                return json.dumps({"exists": False}).encode()
            elif "/v1/ghe/pr/hashes/" in path:
                return json.dumps([]).encode()
            return b"{}"

        mock_matik_client.get_request.side_effect = get_side_effect

        mock_job_metrics = MagicMock()

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
            job_metrics=mock_job_metrics,
        )

        await crawler.process_organization("test-org")

        mock_job_metrics.record_job_error.assert_called_with(
            "ghe_pr", "repo_processing"
        )


class TestGHEPRCrawlerCacheMetrics:
    """Test cache_metrics integration in crawler."""

    @pytest.mark.asyncio
    async def test_run_exports_cache_metrics(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test run() exports crawler stats to cache_metrics."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_ghe_client.get_organization.return_value = mock_org
        mock_ghe_client.get_repos.return_value = []

        mock_cache_metrics = MagicMock()

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
            cache_metrics=mock_cache_metrics,
        )

        await crawler.run()

        # Two Matik calls even with no repos: the per-org watermark GET (read)
        # and the watermark POST (write) on this clean run.
        mock_cache_metrics.record_crawler_stats.assert_called_once_with(
            orgs_processed=1,
            repos_processed=0,
            prs_upserted=0,
            api_calls_ghe=2,
            api_calls_matik=2,
        )


class TestGHEPRCrawlerServiceTagging:
    """Test service tagging in process_repository with artifactory_client."""

    @pytest.mark.asyncio
    async def test_process_repository_tags_prs_with_services(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test process_repository tags PRs with services from Artifactory."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_repo = GHERepo(repo_id=200, repo_name="test-repo", org_id=100)

        mock_pr = make_pr(300, 300)
        mock_ghe_client.list_pull_requests_with_hash_cache.return_value = [
            PRWithFiles(pr=mock_pr, files=["src/main.py"])
        ]

        def get_side_effect(
            path: str, params: Any = None, headers: Any = None
        ) -> bytes:
            if "/v1/ghe/tracker/" in path:
                return json.dumps({"exists": False}).encode()
            elif "/v1/ghe/pr/hashes/" in path:
                return json.dumps([]).encode()
            return b"{}"

        mock_matik_client.get_request.side_effect = get_side_effect

        service_index = {
            "https://github.example.com/test-org/test-repo": [("my_service", "/")],
        }

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        result = await crawler.process_repository(mock_org, mock_repo, service_index)

        assert result == 1
        # PR should have been tagged with the service
        assert mock_pr.services == ["my_service"]

    @pytest.mark.asyncio
    async def test_process_repository_tags_monorepo_prs(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test process_repository tags monorepo PRs based on file paths."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_repo = GHERepo(repo_id=200, repo_name="monorepo", org_id=100)

        mock_pr1 = make_pr(300, 300, title="PR in child_a", repo_name="monorepo")
        mock_pr2 = make_pr(301, 301, title="PR with no files", repo_name="monorepo")
        mock_ghe_client.list_pull_requests_with_hash_cache.return_value = [
            PRWithFiles(pr=mock_pr1, files=["projects/child_a/app.py"]),
            PRWithFiles(pr=mock_pr2, files=[]),
        ]

        def get_side_effect(
            path: str, params: Any = None, headers: Any = None
        ) -> bytes:
            if "/v1/ghe/tracker/" in path:
                return json.dumps({"exists": False}).encode()
            elif "/v1/ghe/pr/hashes/" in path:
                return json.dumps([]).encode()
            return b"{}"

        mock_matik_client.get_request.side_effect = get_side_effect

        service_index = {
            "https://github.example.com/test-org/monorepo": [
                ("svc_parent", "/"),
                ("svc_child_a", "projects/child_a/"),
            ],
        }

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        result = await crawler.process_repository(mock_org, mock_repo, service_index)

        assert result == 2
        # PR1 should be tagged with child_a (monorepo path match)
        assert mock_pr1.services == ["svc_child_a"]
        # PR2 has no files — marked as "tried, no matches" (empty list)
        assert mock_pr2.services == []

    @pytest.mark.asyncio
    async def test_process_repository_no_summary_match(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test process_repository when summary has no entries for repo."""
        mock_org = GHEOrg(org_id=100, org_login="test-org")
        mock_repo = GHERepo(repo_id=200, repo_name="test-repo", org_id=100)

        mock_pr = make_pr(300, 300)
        mock_ghe_client.list_pull_requests_with_hash_cache.return_value = [
            PRWithFiles(pr=mock_pr, files=["src/main.py"])
        ]

        def get_side_effect(
            path: str, params: Any = None, headers: Any = None
        ) -> bytes:
            if "/v1/ghe/tracker/" in path:
                return json.dumps({"exists": False}).encode()
            elif "/v1/ghe/pr/hashes/" in path:
                return json.dumps([]).encode()
            return b"{}"

        mock_matik_client.get_request.side_effect = get_side_effect

        service_index = {
            "https://github.example.com/other-org/other-repo": [
                ("other_service", "/"),
            ],
        }

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        result = await crawler.process_repository(mock_org, mock_repo, service_index)

        assert result == 1
        # PR should NOT be tagged since no entries match the repo
        assert mock_pr.services is None


class TestGHEPRCrawlerApiCallCounting:
    """Test API call counting in each method."""

    @pytest.mark.asyncio
    async def test_get_tracker_cutoff_increments_matik_api_calls(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test that _get_tracker_cutoff increments api_calls_matik."""
        mock_matik_client.get_request.return_value = json.dumps(
            {"exists": False}
        ).encode()

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        await crawler._get_tracker_cutoff(100, 200)

        assert crawler.metrics.api_calls_matik == 1

    @pytest.mark.asyncio
    async def test_post_prs_batch_increments_sqs_messages_sent(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test that _post_prs_batch increments sqs_messages_sent (not api_calls_matik)."""
        mock_prs = [
            make_pr(100, 100),
        ]

        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        await crawler._post_prs_batch(mock_prs)

        assert crawler.metrics.sqs_messages_sent == 1
        assert crawler.metrics.api_calls_matik == 0
        mock_sqs_publisher.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_tracker_increments_sqs_messages_sent(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_config: BiztechGitHubConfig,
        mock_sqs_publisher: MagicMock,
    ) -> None:
        """Test that _update_tracker increments sqs_messages_sent (not api_calls_matik)."""
        crawler = GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

        await crawler._update_tracker(100, 200, 10)

        assert crawler.metrics.sqs_messages_sent == 1
        assert crawler.metrics.api_calls_matik == 0
        mock_sqs_publisher.send.assert_called_once()


class TestGHEPRCrawlerOrgWatermark:
    """Tests for the per-org dormant-repo skip watermark."""

    @staticmethod
    def _get_side_effect(*, watermark_iso: str | None) -> Any:
        """Build a matik get_request side effect for the three GET paths."""

        def side_effect(path: str, params: Any = None, headers: Any = None) -> bytes:
            if "/v1/ghe/org-crawl-tracker/" in path:
                if watermark_iso is None:
                    return json.dumps({"exists": False}).encode()
                return json.dumps(
                    {"exists": True, "last_crawled_at": watermark_iso}
                ).encode()
            if "/v1/ghe/tracker/" in path:
                return json.dumps({"exists": False}).encode()
            if "/v1/ghe/pr/hashes/" in path:
                return json.dumps([]).encode()
            return b"{}"

        return side_effect

    def _crawler(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> GHEPRCrawler:
        return GHEPRCrawler(
            ghe_client=mock_ghe_client,
            matik_client=mock_matik_client,
            sqs_publisher=mock_sqs_publisher,
            config=mock_config,
        )

    @pytest.mark.asyncio
    async def test_passes_pushed_since_from_watermark(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """get_repos receives watermark minus the default buffer as pushed_since."""
        watermark = datetime(2024, 6, 1, 12, 0, 0)
        mock_ghe_client.get_organization.return_value = GHEOrg(
            org_id=100, org_login="test-org"
        )
        mock_ghe_client.get_repos.return_value = []
        mock_matik_client.get_request.side_effect = self._get_side_effect(
            watermark_iso=watermark.isoformat()
        )

        crawler = self._crawler(
            mock_ghe_client, mock_matik_client, mock_sqs_publisher, mock_config
        )
        await crawler.process_organization("test-org")

        pushed_since = mock_ghe_client.get_repos.call_args.kwargs["pushed_since"]
        assert pushed_since == watermark - timedelta(minutes=15)

    @pytest.mark.asyncio
    async def test_first_run_passes_none_pushed_since(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """No watermark yet -> full scan (pushed_since=None)."""
        mock_ghe_client.get_organization.return_value = GHEOrg(
            org_id=100, org_login="test-org"
        )
        mock_ghe_client.get_repos.return_value = []
        mock_matik_client.get_request.side_effect = self._get_side_effect(
            watermark_iso=None
        )

        crawler = self._crawler(
            mock_ghe_client, mock_matik_client, mock_sqs_publisher, mock_config
        )
        await crawler.process_organization("test-org")

        assert mock_ghe_client.get_repos.call_args.kwargs["pushed_since"] is None

    @pytest.mark.asyncio
    async def test_writes_watermark_on_clean_run(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """A clean run advances the per-org watermark via the API."""
        mock_ghe_client.get_organization.return_value = GHEOrg(
            org_id=100, org_login="test-org"
        )
        mock_ghe_client.get_repos.return_value = [
            GHERepo(repo_id=1, repo_name="repo1", org_id=100)
        ]
        mock_ghe_client.list_pull_requests_with_hash_cache.return_value = []
        mock_matik_client.get_request.side_effect = self._get_side_effect(
            watermark_iso=None
        )

        crawler = self._crawler(
            mock_ghe_client, mock_matik_client, mock_sqs_publisher, mock_config
        )
        await crawler.process_organization("test-org")

        mock_matik_client.post_json_request.assert_called_once()
        path = mock_matik_client.post_json_request.call_args.args[0]
        body = mock_matik_client.post_json_request.call_args.args[1]
        assert path == "/v1/ghe/org-crawl-tracker"
        assert body["org_id"] == 100
        assert body["last_crawled_at"] is not None

    @pytest.mark.asyncio
    async def test_skips_watermark_write_on_repo_error(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """A repo failure suppresses the watermark write so the repo is retried."""
        mock_ghe_client.get_organization.return_value = GHEOrg(
            org_id=100, org_login="test-org"
        )
        mock_ghe_client.get_repos.return_value = [
            GHERepo(repo_id=1, repo_name="repo1", org_id=100)
        ]
        mock_ghe_client.list_pull_requests_with_hash_cache.side_effect = Exception(
            "boom"
        )
        mock_matik_client.get_request.side_effect = self._get_side_effect(
            watermark_iso=None
        )

        crawler = self._crawler(
            mock_ghe_client, mock_matik_client, mock_sqs_publisher, mock_config
        )
        await crawler.process_organization("test-org")

        mock_matik_client.post_json_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_watermark_none_org_id(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """_get_org_watermark short-circuits on a None org_id."""
        crawler = self._crawler(
            mock_ghe_client, mock_matik_client, mock_sqs_publisher, mock_config
        )
        assert await crawler._get_org_watermark(None) is None
        mock_matik_client.get_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_watermark_handles_error(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """_get_org_watermark returns None when the API call fails."""
        mock_matik_client.get_request.side_effect = Exception("api down")
        crawler = self._crawler(
            mock_ghe_client, mock_matik_client, mock_sqs_publisher, mock_config
        )
        assert await crawler._get_org_watermark(100) is None

    @pytest.mark.asyncio
    async def test_set_watermark_none_org_id(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """_set_org_watermark short-circuits on a None org_id."""
        crawler = self._crawler(
            mock_ghe_client, mock_matik_client, mock_sqs_publisher, mock_config
        )
        await crawler._set_org_watermark(None, datetime(2024, 6, 1, 0, 0, 0))
        mock_matik_client.post_json_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_watermark_handles_error(
        self,
        mock_ghe_client: MagicMock,
        mock_matik_client: MagicMock,
        mock_sqs_publisher: MagicMock,
        mock_config: BiztechGitHubConfig,
    ) -> None:
        """_set_org_watermark swallows API errors (does not raise)."""
        mock_matik_client.post_json_request.side_effect = Exception("api down")
        crawler = self._crawler(
            mock_ghe_client, mock_matik_client, mock_sqs_publisher, mock_config
        )
        # Should not raise.
        await crawler._set_org_watermark(100, datetime(2024, 6, 1, 0, 0, 0))
