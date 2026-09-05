"""Unit tests for ghe_client.py."""

import asyncio
import unittest
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from githubkit.exception import RequestFailed

from common.clients.ghe_client import (
    DEFAULT_RETRY_CONFIG,
    GHEClient,
    GHEOrg,
    GHERepo,
    PRFileInfo,
    PRHashCache,
    PRHashInfo,
    RawPullRequest,
    RetryConfig,
    _get_private_key,
    create_ghe_client,
)
from common.models.biztech_github_config import BiztechGitHubConfig
from common.utils.datetime_utils import parse_timestamp_to_utc
from common.utils.github_utils import build_ghe_pr_content


class TestRetryConfig(unittest.TestCase):
    """Tests for RetryConfig model."""

    def test_default_values(self) -> None:
        """Test default retry configuration values."""
        config = RetryConfig()

        assert config.enabled is True
        assert config.backoff_durations == [2.0, 4.0, 8.0, 15.0, 30.0]
        assert config.rate_limit_wait == 3600.0

    def test_custom_values(self) -> None:
        """Test custom retry configuration."""
        config = RetryConfig(
            enabled=False,
            backoff_durations=[1.0, 2.0],
            rate_limit_wait=60.0,
        )

        assert config.enabled is False
        assert config.backoff_durations == [1.0, 2.0]
        assert config.rate_limit_wait == 60.0


class TestPRHashInfo(unittest.TestCase):
    """Tests for PRHashInfo dataclass."""

    def test_creation(self) -> None:
        """Test PRHashInfo creation."""
        info = PRHashInfo(
            pull_request_id=123,
            repository_id=456,
            description_hash="abc123",
            pull_request_summary="Test summary",
        )

        assert info.pull_request_id == 123
        assert info.repository_id == 456
        assert info.description_hash == "abc123"
        assert info.pull_request_summary == "Test summary"


class TestPRHashCache(unittest.TestCase):
    """Tests for PRHashCache dataclass."""

    def test_empty_cache(self) -> None:
        """Test empty cache creation."""
        cache = PRHashCache()
        assert cache.hashes == {}

    def test_cache_with_data(self) -> None:
        """Test cache with pre-populated data."""
        info = PRHashInfo(
            pull_request_id=123,
            repository_id=456,
            description_hash="abc123",
            pull_request_summary="Test",
        )
        cache = PRHashCache(hashes={"123": info})

        assert "123" in cache.hashes
        assert cache.hashes["123"].pull_request_id == 123


class TestSafeDatetime(unittest.TestCase):
    """Tests for parse_timestamp_to_utc used as datetime converter."""

    def test_none_input(self) -> None:
        """Test that None input returns None."""
        result = parse_timestamp_to_utc(None)
        assert result is None

    def test_naive_datetime(self) -> None:
        """Test that naive datetime is returned unchanged."""
        dt = datetime(2024, 1, 1, 12, 0, 0)
        result = parse_timestamp_to_utc(dt)

        assert result == dt
        assert result.tzinfo is None

    def test_aware_datetime_converted_to_utc(self) -> None:
        """Test that aware datetime is converted to naive UTC."""

        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = parse_timestamp_to_utc(dt)

        assert result is not None
        assert result.tzinfo is None
        assert result.hour == 12


class TestGetPrivateKey(unittest.TestCase):
    """Tests for _get_private_key helper function."""

    def test_from_base64_config(self) -> None:
        """Test getting private key from base64 encoded config."""
        import base64

        key_content = (
            "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
        )
        encoded = base64.standard_b64encode(key_content.encode()).decode()

        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_id="12345",
            ghe_app_client_id="client-id",
            ghe_app_installation_id="67890",
            ghe_app_private_key=encoded,
        )

        result = _get_private_key(config)
        assert result == key_content

    @patch("builtins.open", side_effect=FileNotFoundError())
    def test_file_not_found_raises(self, mock_open: MagicMock) -> None:
        """Test that missing file raises ValueError."""
        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_id="12345",
            ghe_app_client_id="client-id",
            ghe_app_installation_id="67890",
        )

        with pytest.raises(ValueError, match="No private key provided"):
            _get_private_key(config)


class TestGHEClientInit(unittest.TestCase):
    """Tests for GHEClient initialization."""

    def _create_config(self) -> BiztechGitHubConfig:
        import base64

        key = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
        return BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_id="12345",
            ghe_app_client_id="client-id",
            ghe_app_installation_id="67890",
            ghe_app_private_key=base64.standard_b64encode(key.encode()).decode(),
            ghe_pr_summary_prompt="Summarize this PR",
            facade_model="gpt-4o",
        )

    @patch("common.clients.ghe_client.GitHub")
    def test_init_creates_github_client(self, mock_github: MagicMock) -> None:
        """Test that initialization creates GitHub client."""
        config = self._create_config()

        client = GHEClient(ghe_config=config)

        mock_github.assert_called_once()
        assert client.retry_config == DEFAULT_RETRY_CONFIG

    @patch("common.clients.ghe_client.GitHub")
    def test_init_with_custom_retry_config(self, mock_github: MagicMock) -> None:
        """Test initialization with custom retry config."""
        config = self._create_config()
        retry_config = RetryConfig(enabled=False)

        client = GHEClient(ghe_config=config, retry_config=retry_config)

        assert client.retry_config.enabled is False

    @patch("common.clients.ghe_client.GitHub")
    def test_init_default_max_concurrent_pr_tasks(self, mock_github: MagicMock) -> None:
        """Test default max_concurrent_pr_tasks is 50."""
        config = self._create_config()
        client = GHEClient(ghe_config=config)
        assert client.max_concurrent_pr_tasks == 50

    @patch("common.clients.ghe_client.GitHub")
    def test_init_with_custom_max_concurrent_pr_tasks(
        self, mock_github: MagicMock
    ) -> None:
        """Test custom max_concurrent_pr_tasks value."""
        config = self._create_config()
        client = GHEClient(ghe_config=config, max_concurrent_pr_tasks=10)
        assert client.max_concurrent_pr_tasks == 10


class TestGHEClientGetOrgs(unittest.TestCase):
    """Tests for GHEClient.get_orgs method."""

    def _create_config(self) -> BiztechGitHubConfig:
        import base64

        key = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
        return BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_id="12345",
            ghe_app_client_id="client-id",
            ghe_app_installation_id="67890",
            ghe_app_private_key=base64.standard_b64encode(key.encode()).decode(),
        )

    @patch("common.clients.ghe_client.GitHub")
    def test_get_orgs_returns_organizations(self, mock_github_class: MagicMock) -> None:
        """Test that get_orgs returns list of GHEOrg."""

        async def run_test() -> list[GHEOrg]:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            # Create mock organization
            mock_org = MagicMock()
            mock_org.login = "test-org"
            mock_org.id = 12345

            # Setup async iterator
            async def mock_paginate(
                *args: Any, **kwargs: Any
            ) -> AsyncIterator[MagicMock]:
                yield mock_org

            mock_github.paginate = mock_paginate

            config = self._create_config()
            client = GHEClient(ghe_config=config)
            return await client.get_orgs()

        orgs = asyncio.run(run_test())
        assert len(orgs) == 1
        assert orgs[0].org_login == "test-org"
        assert orgs[0].org_id == 12345


class TestGHEClientGetRepos(unittest.TestCase):
    """Tests for GHEClient.get_repos method."""

    def _create_config(self) -> BiztechGitHubConfig:
        import base64

        key = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
        return BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_id="12345",
            ghe_app_client_id="client-id",
            ghe_app_installation_id="67890",
            ghe_app_private_key=base64.standard_b64encode(key.encode()).decode(),
        )

    @patch("common.clients.ghe_client.GitHub")
    def test_get_repos_returns_repositories(self, mock_github_class: MagicMock) -> None:
        """Test that get_repos returns list of GHERepo."""

        async def run_test() -> list[GHERepo]:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            # Create mock repository
            mock_repo = MagicMock()
            mock_repo.name = "test-repo"
            mock_repo.id = 54321
            mock_repo.description = "Test description"
            mock_repo.private = False
            mock_repo.archived = False
            mock_repo.pushed_at = None

            # Setup async mock for repos.async_list_for_org
            mock_response = MagicMock()
            mock_response.parsed_data = [mock_repo]

            # First call returns repo, second call returns empty list
            mock_github.rest.repos.async_list_for_org = AsyncMock(
                side_effect=[mock_response, MagicMock(parsed_data=[])]
            )

            config = self._create_config()
            client = GHEClient(ghe_config=config)

            org = GHEOrg(org_id=12345, org_login="test-org")
            return await client.get_repos(org)

        repos = asyncio.run(run_test())
        assert len(repos) == 1
        assert repos[0].repo_name == "test-repo"
        assert repos[0].repo_id == 54321

    @patch("common.clients.ghe_client.GitHub")
    def test_get_repos_multiple_pages(self, mock_github_class: MagicMock) -> None:
        """Test pagination through multiple pages of repos."""

        async def run_test() -> list[GHERepo]:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            # Create mock repos for two pages
            mock_repo1 = MagicMock()
            mock_repo1.name = "repo1"
            mock_repo1.id = 1
            mock_repo1.description = "Repo 1"
            mock_repo1.private = False
            mock_repo1.archived = False
            mock_repo1.pushed_at = None

            mock_repo2 = MagicMock()
            mock_repo2.name = "repo2"
            mock_repo2.id = 2
            mock_repo2.description = "Repo 2"
            mock_repo2.private = False
            mock_repo2.archived = False
            mock_repo2.pushed_at = None

            # Page 1 returns full page (page_size), page 2 returns partial
            page_calls = 0

            async def mock_list_for_org(**kwargs: Any) -> MagicMock:
                nonlocal page_calls
                page_calls += 1
                response = MagicMock()
                if page_calls == 1:
                    response.parsed_data = [mock_repo1]  # Full page (1 repo for test)
                elif page_calls == 2:
                    response.parsed_data = [mock_repo2]  # Another page
                else:
                    response.parsed_data = []  # End pagination
                return response

            mock_github.rest.repos.async_list_for_org = AsyncMock(
                side_effect=mock_list_for_org
            )

            config = self._create_config()
            client = GHEClient(ghe_config=config)

            org = GHEOrg(org_id=12345, org_login="test-org")
            # Use page_size=1 to trigger multiple pages
            return await client.get_repos(org, page_size=1)

        repos = asyncio.run(run_test())
        assert len(repos) == 2
        assert repos[0].repo_name == "repo1"
        assert repos[1].repo_name == "repo2"

    @patch("common.clients.ghe_client.GitHub")
    def test_get_repos_limits_page_size(self, mock_github_class: MagicMock) -> None:
        """Test that page_size is capped at 100."""

        async def run_test() -> None:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            # Setup async mock that returns empty to stop pagination
            mock_response = MagicMock()
            mock_response.parsed_data = []
            mock_github.rest.repos.async_list_for_org = AsyncMock(
                return_value=mock_response
            )

            config = self._create_config()
            client = GHEClient(ghe_config=config)

            org = GHEOrg(org_id=12345, org_login="test-org")
            await client.get_repos(org, page_size=200)

            # Verify page_size was capped at 100
            call_kwargs = mock_github.rest.repos.async_list_for_org.call_args.kwargs
            assert call_kwargs.get("per_page") == 100

        asyncio.run(run_test())


class TestGHEClientGetReposActivityFilter(unittest.TestCase):
    """Tests for get_repos dormant-repo skipping and pushed sorting."""

    def _create_config(self) -> BiztechGitHubConfig:
        import base64

        key = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
        return BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_id="12345",
            ghe_app_client_id="client-id",
            ghe_app_installation_id="67890",
            ghe_app_private_key=base64.standard_b64encode(key.encode()).decode(),
        )

    @staticmethod
    def _repo(
        repo_id: int,
        name: str,
        pushed_at: datetime | None,
        *,
        archived: bool = False,
    ) -> MagicMock:
        repo = MagicMock()
        repo.id = repo_id
        repo.name = name
        repo.pushed_at = pushed_at
        repo.archived = archived
        return repo

    @patch("common.clients.ghe_client.GitHub")
    def test_sorts_by_pushed_desc(self, mock_github_class: MagicMock) -> None:
        """Listing requests pushed/desc so we can early-exit by activity."""

        async def run_test() -> None:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github
            mock_github.rest.repos.async_list_for_org = AsyncMock(
                return_value=MagicMock(parsed_data=[])
            )

            client = GHEClient(ghe_config=self._create_config())
            org = GHEOrg(org_id=1, org_login="o")
            await client.get_repos(org)

            kwargs = mock_github.rest.repos.async_list_for_org.call_args.kwargs
            assert kwargs.get("sort") == "pushed"
            assert kwargs.get("direction") == "desc"

        asyncio.run(run_test())

    @patch("common.clients.ghe_client.GitHub")
    def test_skips_archived_repos(self, mock_github_class: MagicMock) -> None:
        """Archived repos are excluded even on a full scan."""

        async def run_test() -> list[GHERepo]:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github
            repos = [
                self._repo(1, "active", datetime(2024, 5, 1, 0, 0, 0)),
                self._repo(2, "dead", datetime(2024, 5, 2, 0, 0, 0), archived=True),
            ]
            mock_github.rest.repos.async_list_for_org = AsyncMock(
                side_effect=[MagicMock(parsed_data=repos), MagicMock(parsed_data=[])]
            )

            client = GHEClient(ghe_config=self._create_config())
            return await client.get_repos(GHEOrg(org_id=1, org_login="o"))

        result = asyncio.run(run_test())
        assert [r.repo_name for r in result] == ["active"]

    @patch("common.clients.ghe_client.GitHub")
    def test_filters_and_early_exits_on_pushed_since(
        self, mock_github_class: MagicMock
    ) -> None:
        """Repos pushed before the cutoff are skipped and paging stops early."""

        async def run_test() -> tuple[list[GHERepo], int]:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github
            # Sorted pushed desc: newest first, last entry is oldest on the page.
            page1 = [
                self._repo(1, "fresh", datetime(2024, 6, 10, 0, 0, 0)),
                self._repo(2, "stale", datetime(2024, 1, 1, 0, 0, 0)),
            ]
            call_count = 0

            async def list_for_org(**_: Any) -> MagicMock:
                nonlocal call_count
                call_count += 1
                return MagicMock(parsed_data=page1 if call_count == 1 else [])

            mock_github.rest.repos.async_list_for_org = AsyncMock(
                side_effect=list_for_org
            )

            client = GHEClient(ghe_config=self._create_config())
            repos = await client.get_repos(
                GHEOrg(org_id=1, org_login="o"),
                pushed_since=datetime(2024, 5, 1, 0, 0, 0),
            )
            return repos, call_count

        result, calls = asyncio.run(run_test())
        assert [r.repo_name for r in result] == ["fresh"]
        # Early exit: only the first page is fetched (oldest < cutoff).
        assert calls == 1

    @patch("common.clients.ghe_client.GitHub")
    def test_pushed_since_none_includes_all_and_carries_pushed_at(
        self, mock_github_class: MagicMock
    ) -> None:
        """No watermark lists every non-archived repo and carries pushed_at."""

        async def run_test() -> list[GHERepo]:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github
            pushed = datetime(2024, 1, 1, 0, 0, 0)
            repos = [self._repo(1, "old-but-listed", pushed)]
            mock_github.rest.repos.async_list_for_org = AsyncMock(
                side_effect=[MagicMock(parsed_data=repos), MagicMock(parsed_data=[])]
            )

            client = GHEClient(ghe_config=self._create_config())
            return await client.get_repos(GHEOrg(org_id=1, org_login="o"))

        result = asyncio.run(run_test())
        assert len(result) == 1
        assert result[0].pushed_at == datetime(2024, 1, 1, 0, 0, 0)


class TestCreateGHEClient(unittest.TestCase):
    """Tests for create_ghe_client factory function."""

    def _create_config(self) -> BiztechGitHubConfig:
        import base64

        key = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
        return BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_id="12345",
            ghe_app_client_id="client-id",
            ghe_app_installation_id="67890",
            ghe_app_private_key=base64.standard_b64encode(key.encode()).decode(),
        )

    @patch("common.clients.ghe_client.GitHub")
    def test_create_ghe_client_basic(self, mock_github: MagicMock) -> None:
        """Test factory function creates async client correctly."""
        config = self._create_config()

        client = create_ghe_client(config)

        assert isinstance(client, GHEClient)

    @patch("common.clients.ghe_client.GitHub")
    def test_create_ghe_client_with_configs(self, mock_github: MagicMock) -> None:
        """Test factory function with optional configs."""
        ghe_config = self._create_config()
        retry_config = RetryConfig(enabled=False)

        client = create_ghe_client(ghe_config, retry_config=retry_config)

        assert isinstance(client, GHEClient)
        assert client.retry_config.enabled is False


class TestGetPrivateKeyFromFile(unittest.TestCase):
    """Tests for _get_private_key reading from files."""

    @patch("builtins.open")
    def test_from_file_path(self, mock_open: MagicMock) -> None:
        """Test getting private key from specified file path."""
        key_content = (
            b"-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
        )
        mock_open.return_value.__enter__.return_value.read.return_value = key_content

        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_id="12345",
            ghe_app_client_id="client-id",
            ghe_app_installation_id="67890",
            ghe_app_private_key_file_name="/path/to/key.pem",
        )

        result = _get_private_key(config)
        assert result == key_content.decode("utf-8")
        mock_open.assert_called_once_with("/path/to/key.pem", "rb")

    @patch("builtins.open")
    def test_from_default_file(self, mock_open: MagicMock) -> None:
        """Test getting private key from default file."""
        key_content = (
            b"-----BEGIN RSA PRIVATE KEY-----\ndefault\n-----END RSA PRIVATE KEY-----"
        )
        mock_open.return_value.__enter__.return_value.read.return_value = key_content

        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_id="12345",
            ghe_app_client_id="client-id",
            ghe_app_installation_id="67890",
        )

        result = _get_private_key(config)
        assert result == key_content.decode("utf-8")
        mock_open.assert_called_with("ghe_key_pcs8.pem", "rb")


class TestGHEClientGetOrganization(unittest.TestCase):
    """Tests for GHEClient.get_organization method."""

    def _create_config(self) -> BiztechGitHubConfig:
        import base64

        key = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
        return BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_id="12345",
            ghe_app_client_id="client-id",
            ghe_app_installation_id="67890",
            ghe_app_private_key=base64.standard_b64encode(key.encode()).decode(),
        )

    @patch("common.clients.ghe_client.GitHub")
    def test_get_organization_returns_org(self, mock_github_class: MagicMock) -> None:
        """Test that get_organization returns organization model."""

        async def run_test() -> GHEOrg:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            mock_org = MagicMock()
            mock_org.login = "test-org"
            mock_org.id = 99999

            mock_response = MagicMock()
            mock_response.parsed_data = mock_org

            async def mock_get(org: str) -> MagicMock:
                return mock_response

            mock_github.rest.orgs.async_get = mock_get

            config = self._create_config()
            client = GHEClient(ghe_config=config)
            return await client.get_organization("test-org")

        org = asyncio.run(run_test())
        assert org.org_login == "test-org"
        assert org.org_id == 99999


class TestGHEClientGetPullRequests(unittest.TestCase):
    """Tests for GHEClient.get_pull_requests method."""

    def _create_config(self) -> BiztechGitHubConfig:
        import base64

        key = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
        return BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_id="12345",
            ghe_app_client_id="client-id",
            ghe_app_installation_id="67890",
            ghe_app_private_key=base64.standard_b64encode(key.encode()).decode(),
        )

    @patch("common.clients.ghe_client.GitHub")
    def test_get_pull_requests_returns_prs(self, mock_github_class: MagicMock) -> None:
        """Test that get_pull_requests returns list of PRs."""

        async def run_test() -> list[Any]:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            # Use raw dict format instead of MagicMock (now using response.json())
            mock_pr_data = {
                "id": 123,
                "number": 42,
                "title": "Test PR",
                "body": "Test body",
                "state": "closed",
                "locked": False,
                "created_at": "2024-06-01T12:00:00Z",
                "closed_at": None,
                "merged_at": "2024-06-02T12:00:00Z",
                "base": {"ref": "main"},
            }

            # Mock manual pagination - first call returns PR, second returns empty
            call_count = 0

            async def mock_async_list(**_kwargs: Any) -> MagicMock:
                nonlocal call_count
                call_count += 1
                response = MagicMock()
                if call_count == 1:
                    response.json.return_value = [mock_pr_data]
                else:
                    response.json.return_value = []
                return response

            mock_github.rest.pulls.async_list = mock_async_list

            config = self._create_config()
            client = GHEClient(ghe_config=config)
            return await client.get_pull_requests("test-org", "test-repo")

        prs = asyncio.run(run_test())
        assert len(prs) == 1
        assert prs[0].id == 123
        assert prs[0].number == 42
        assert prs[0].title == "Test PR"

    @patch("common.clients.ghe_client.GitHub")
    def test_get_pull_requests_with_cutoff_date(
        self, mock_github_class: MagicMock
    ) -> None:
        """Test that PRs are filtered by cutoff date."""

        async def run_test() -> list[Any]:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            # PR before cutoff (will be filtered out - no merged_at)
            old_pr_data = {
                "id": 1,
                "number": 10,
                "title": "Old PR",
                "body": "Old body",
                "state": "closed",
                "locked": False,
                "created_at": "2024-01-01T12:00:00Z",
                "closed_at": "2024-01-02T12:00:00Z",
                "merged_at": "2024-01-02T12:00:00Z",
                "base": {"ref": "main"},
            }

            # PR after cutoff (will be included)
            new_pr_data = {
                "id": 2,
                "number": 11,
                "title": "New PR",
                "body": "New body",
                "state": "closed",
                "locked": False,
                "created_at": "2024-06-15T12:00:00Z",
                "closed_at": "2024-06-16T12:00:00Z",
                "merged_at": "2024-06-16T12:00:00Z",
                "base": {"ref": "main"},
            }

            # Mock manual pagination - returns PRs in desc order (newest first)
            # old_pr at [-1] triggers early exit filtering
            call_count = 0

            async def mock_async_list(**kwargs: Any) -> MagicMock:
                nonlocal call_count
                call_count += 1
                response = MagicMock()
                if call_count == 1:
                    # Return in desc order: new_pr first, old_pr last
                    response.json.return_value = [new_pr_data, old_pr_data]
                else:
                    response.json.return_value = []
                return response

            mock_github.rest.pulls.async_list = mock_async_list

            config = self._create_config()
            client = GHEClient(ghe_config=config)
            cutoff = datetime(2024, 6, 1, 0, 0, 0, tzinfo=UTC)
            return await client.get_pull_requests(
                "test-org", "test-repo", cutoff_date=cutoff
            )

        prs = asyncio.run(run_test())
        assert len(prs) == 1
        assert prs[0].id == 2

    @patch("common.clients.ghe_client.GitHub")
    def test_get_pull_requests_caps_page_size(
        self, mock_github_class: MagicMock
    ) -> None:
        """Test that page_size > 100 is capped to 100."""

        async def run_test() -> None:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            captured_per_page = None

            async def mock_async_list(**kwargs: Any) -> MagicMock:
                nonlocal captured_per_page
                captured_per_page = kwargs.get("per_page")
                response = MagicMock()
                response.json.return_value = []
                return response

            mock_github.rest.pulls.async_list = mock_async_list

            config = self._create_config()
            client = GHEClient(ghe_config=config)
            # Request page_size of 200, should be capped to 100
            await client.get_pull_requests("test-org", "test-repo", page_size=200)

            assert captured_per_page == 100

        asyncio.run(run_test())

    @patch("common.clients.ghe_client.GitHub")
    def test_get_pull_requests_cutoff_skips_non_merged(
        self, mock_github_class: MagicMock
    ) -> None:
        """Test that non-merged PRs in cutoff range are skipped."""

        async def run_test() -> list[Any]:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            # PR in cutoff range but NOT merged (should be skipped)
            non_merged_pr = {
                "id": 1,
                "number": 20,
                "title": "Non-merged PR",
                "body": "Closed but not merged",
                "state": "closed",
                "locked": False,
                "created_at": "2024-06-15T12:00:00Z",
                "closed_at": "2024-06-16T12:00:00Z",
                "merged_at": None,  # Not merged
                "base": {"ref": "main"},
            }

            # PR in cutoff range and merged (should be included)
            merged_pr = {
                "id": 2,
                "number": 21,
                "title": "Merged PR",
                "body": "Merged PR body",
                "state": "closed",
                "locked": False,
                "created_at": "2024-06-15T12:00:00Z",
                "closed_at": "2024-06-16T12:00:00Z",
                "merged_at": "2024-06-16T12:00:00Z",
                "base": {"ref": "main"},
            }

            # PR before cutoff (triggers early exit)
            old_pr = {
                "id": 3,
                "number": 22,
                "title": "Old PR",
                "body": "Old body",
                "state": "closed",
                "locked": False,
                "created_at": "2024-01-01T12:00:00Z",
                "closed_at": "2024-01-02T12:00:00Z",
                "merged_at": "2024-01-02T12:00:00Z",
                "base": {"ref": "main"},
            }

            call_count = 0

            async def mock_async_list(**kwargs: Any) -> MagicMock:
                nonlocal call_count
                call_count += 1
                response = MagicMock()
                if call_count == 1:
                    # Return in desc order: newest first, oldest last
                    response.json.return_value = [merged_pr, non_merged_pr, old_pr]
                else:
                    response.json.return_value = []
                return response

            mock_github.rest.pulls.async_list = mock_async_list

            config = self._create_config()
            client = GHEClient(ghe_config=config)
            cutoff = datetime(2024, 6, 1, 0, 0, 0, tzinfo=UTC)
            return await client.get_pull_requests(
                "test-org", "test-repo", cutoff_date=cutoff
            )

        prs = asyncio.run(run_test())
        # Only merged PR (id=2) should be included, non-merged (id=1) skipped
        assert len(prs) == 1
        assert prs[0].id == 2


class TestGHEClientListPullRequestsWithHashCache(unittest.TestCase):
    """Tests for GHEClient.list_pull_requests_with_hash_cache method."""

    def _create_config(self) -> BiztechGitHubConfig:
        import base64

        key = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
        return BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_id="12345",
            ghe_app_client_id="client-id",
            ghe_app_installation_id="67890",
            ghe_app_private_key=base64.standard_b64encode(key.encode()).decode(),
            ghe_pr_summary_prompt="Summarize this PR",
            facade_model="gpt-4o",
        )

    @patch("common.clients.ghe_client.GitHub")
    def test_list_prs_logs_error_for_failed_pr_processing(
        self, mock_github_class: MagicMock
    ) -> None:
        """Test that failed PR processing (BaseException) is logged and skipped."""

        async def run_test() -> list[Any]:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            mock_prs = [
                {
                    "id": 1,
                    "number": 60,
                    "title": "PR 1",
                    "body": "Body 1",
                    "state": "closed",
                    "locked": False,
                    "created_at": "2024-06-01T12:00:00Z",
                    "closed_at": None,
                    "merged_at": "2024-06-02T12:00:00Z",
                    "base": {"ref": "main"},
                },
            ]

            call_count = 0

            async def mock_async_list(**kwargs: Any) -> MagicMock:
                nonlocal call_count
                call_count += 1
                response = MagicMock()
                if call_count == 1:
                    response.json.return_value = mock_prs
                else:
                    response.json.return_value = []
                return response

            mock_github.rest.pulls.async_list = mock_async_list

            config = self._create_config()
            client = GHEClient(ghe_config=config)

            # Patch _process_pr_with_cache to raise an exception
            async def mock_process_pr_with_cache(*args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("Simulated processing failure")

            client._process_pr_with_cache = mock_process_pr_with_cache  # type: ignore[method-assign]

            org = GHEOrg(org_id=1, org_login="test-org")
            repo = GHERepo(repo_id=1, repo_name="test-repo", org_id=1)

            return await client.list_pull_requests_with_hash_cache(org, repo)

        results = asyncio.run(run_test())
        # PR processing failed, so no PRs returned
        assert len(results) == 0


class TestGHEClientClose(unittest.TestCase):
    """Tests for GHEClient.close method."""

    def _create_config(self) -> BiztechGitHubConfig:
        import base64

        key = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
        return BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_id="12345",
            ghe_app_client_id="client-id",
            ghe_app_installation_id="67890",
            ghe_app_private_key=base64.standard_b64encode(key.encode()).decode(),
        )

    @patch("common.clients.ghe_client.GitHub")
    def test_close_calls_aexit_when_available(
        self, mock_github_class: MagicMock
    ) -> None:
        """Test that close calls __aexit__ when available."""

        async def run_test() -> None:
            mock_github = MagicMock()
            mock_aexit = AsyncMock()
            mock_github.__aexit__ = mock_aexit
            mock_github_class.return_value = mock_github

            config = self._create_config()
            client = GHEClient(ghe_config=config)

            await client.close()

            mock_aexit.assert_called_once_with(None, None, None)

        asyncio.run(run_test())

    @patch("common.clients.ghe_client.GitHub")
    def test_close_calls_close_when_no_aexit(
        self, mock_github_class: MagicMock
    ) -> None:
        """Test that close calls close() when __aexit__ not available."""

        async def run_test() -> None:
            mock_github = MagicMock(spec=["close"])
            mock_close = AsyncMock()
            mock_github.close = mock_close
            mock_github_class.return_value = mock_github

            config = self._create_config()
            client = GHEClient(ghe_config=config)

            await client.close()

            mock_close.assert_called_once()

        asyncio.run(run_test())

    @patch("common.clients.ghe_client.GitHub")
    def test_close_handles_no_cleanup_methods(
        self, mock_github_class: MagicMock
    ) -> None:
        """Test that close handles case where no cleanup methods exist."""

        async def run_test() -> None:
            mock_github = MagicMock(spec=[])  # No __aexit__ or close
            mock_github_class.return_value = mock_github

            config = self._create_config()
            client = GHEClient(ghe_config=config)

            # Should not raise
            await client.close()

        asyncio.run(run_test())

    @patch("common.clients.ghe_client.GitHub")
    def test_close_handles_uninitialized_async_client(
        self, mock_github_class: MagicMock
    ) -> None:
        """Test that close handles AttributeError from uninitialized async client.

        This can happen with githubkit when __aexit__ is called but the internal
        async HTTP client was never initialized (e.g., no async requests made).
        """

        async def run_test() -> None:
            mock_github = MagicMock()
            # Simulate githubkit's behavior when async client not initialized
            mock_github.__aexit__ = AsyncMock(
                side_effect=AttributeError(
                    "'NoneType' object has no attribute 'aclose'"
                )
            )
            mock_github_class.return_value = mock_github

            config = self._create_config()
            client = GHEClient(ghe_config=config)

            # Should not raise - AttributeError should be caught
            await client.close()

        asyncio.run(run_test())


class TestGHEClientRetryWithBackoff(unittest.TestCase):
    """Tests for GHEClient._retry_with_backoff method."""

    def _create_config(self) -> BiztechGitHubConfig:
        import base64

        key = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
        return BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_id="12345",
            ghe_app_client_id="client-id",
            ghe_app_installation_id="67890",
            ghe_app_private_key=base64.standard_b64encode(key.encode()).decode(),
        )

    @patch("common.clients.ghe_client.GitHub")
    def test_retry_records_metrics_on_success(
        self, mock_github_class: MagicMock
    ) -> None:
        """Test that client_metrics records successful request."""

        async def run_test() -> str:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            mock_metrics = MagicMock()
            config = self._create_config()
            client = GHEClient(ghe_config=config, client_metrics=mock_metrics)

            async def successful_operation() -> str:
                return "success"

            result = await client._retry_with_backoff(successful_operation, "test_op")

            # Verify metrics recorded
            mock_metrics.record_request.assert_called_once()
            call_args = mock_metrics.record_request.call_args
            assert call_args[0][0] == "GET"
            assert call_args[0][1] == "test_op"
            assert call_args[0][3] == 200  # status code

            return str(result)

        result = asyncio.run(run_test())
        assert result == "success"

    @patch("common.clients.ghe_client.GitHub")
    def test_retry_disabled_records_metrics_on_failure(
        self, mock_github_class: MagicMock
    ) -> None:
        """Test that client_metrics records failure when retries disabled."""

        async def run_test() -> None:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            mock_metrics = MagicMock()
            config = self._create_config()
            retry_config = RetryConfig(enabled=False)
            client = GHEClient(
                ghe_config=config,
                retry_config=retry_config,
                client_metrics=mock_metrics,
            )

            async def failing_operation() -> str:
                raise ValueError("Test error")

            with pytest.raises(ValueError):
                await client._retry_with_backoff(failing_operation, "test_op")

            # Verify metrics recorded failure
            mock_metrics.record_request.assert_called_once()
            call_args = mock_metrics.record_request.call_args
            assert call_args[0][3] == 500  # status code

        asyncio.run(run_test())

    @patch("common.clients.ghe_client.asyncio.sleep")
    @patch("common.clients.ghe_client.is_github_rate_limit_error")
    @patch("common.clients.ghe_client.GitHub")
    def test_retry_rate_limit_records_cache_metrics(
        self,
        mock_github_class: MagicMock,
        mock_is_rate_limit: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        """Test that cache_metrics records rate limit event."""

        async def run_test() -> str:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github
            mock_sleep.return_value = None
            mock_is_rate_limit.return_value = True

            mock_cache_metrics = MagicMock()
            mock_client_metrics = MagicMock()
            config = self._create_config()
            retry_config = RetryConfig(enabled=True, rate_limit_wait=0.001)
            client = GHEClient(
                ghe_config=config,
                retry_config=retry_config,
                cache_metrics=mock_cache_metrics,
                client_metrics=mock_client_metrics,
            )

            call_count = 0

            async def rate_limited_operation() -> str:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise Exception("Rate limit exceeded")
                return "success"

            result = await client._retry_with_backoff(rate_limited_operation, "test_op")

            # Verify cache metrics recorded rate limit
            mock_cache_metrics.record_rate_limit.assert_called_once_with("test_op")
            # Verify client metrics recorded retry
            mock_client_metrics.record_retry.assert_called()

            return str(result)

        result = asyncio.run(run_test())
        assert result == "success"

    @patch("common.clients.ghe_client.asyncio.sleep")
    @patch("common.clients.ghe_client.is_github_rate_limit_error")
    @patch("common.clients.ghe_client.GitHub")
    def test_retry_rate_limit_failure_records_metrics(
        self,
        mock_github_class: MagicMock,
        mock_is_rate_limit: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        """Test that client_metrics records failure after rate limit retry fails."""

        async def run_test() -> None:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github
            mock_sleep.return_value = None
            mock_is_rate_limit.return_value = True

            mock_client_metrics = MagicMock()
            config = self._create_config()
            retry_config = RetryConfig(enabled=True, rate_limit_wait=0.001)
            client = GHEClient(
                ghe_config=config,
                retry_config=retry_config,
                client_metrics=mock_client_metrics,
            )

            async def always_failing() -> str:
                raise Exception("Still failing")

            with pytest.raises(Exception, match="Still failing"):
                await client._retry_with_backoff(always_failing, "test_op")

            # Verify metrics recorded 500 error
            assert mock_client_metrics.record_request.call_count >= 1
            last_call = mock_client_metrics.record_request.call_args
            assert last_call[0][3] == 500

        asyncio.run(run_test())

    @patch("common.clients.ghe_client.asyncio.sleep")
    @patch("common.clients.ghe_client.is_github_rate_limit_error")
    @patch("common.clients.ghe_client.GitHub")
    def test_retry_backoff_records_metrics_on_success(
        self,
        mock_github_class: MagicMock,
        mock_is_rate_limit: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        """Test that client_metrics records success after backoff retry."""

        async def run_test() -> str:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github
            mock_sleep.return_value = None
            mock_is_rate_limit.return_value = False  # Not a rate limit error

            mock_client_metrics = MagicMock()
            config = self._create_config()
            retry_config = RetryConfig(enabled=True, backoff_durations=[0.001])
            client = GHEClient(
                ghe_config=config,
                retry_config=retry_config,
                client_metrics=mock_client_metrics,
            )

            call_count = 0

            async def flaky_operation() -> str:
                nonlocal call_count
                call_count += 1
                if call_count < 2:
                    raise ValueError("Transient error")
                return "success"

            result = await client._retry_with_backoff(flaky_operation, "test_op")

            # Verify retry was recorded
            mock_client_metrics.record_retry.assert_called()
            # Verify final success was recorded
            last_call = mock_client_metrics.record_request.call_args
            assert last_call[0][3] == 200

            return str(result)

        result = asyncio.run(run_test())
        assert result == "success"

    @patch("common.clients.ghe_client.asyncio.sleep")
    @patch("common.clients.ghe_client.is_github_rate_limit_error")
    @patch("common.clients.ghe_client.GitHub")
    def test_retry_backoff_rate_limit_records_cache_metrics(
        self,
        mock_github_class: MagicMock,
        mock_is_rate_limit: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        """Test rate limit during backoff records cache metrics."""

        async def run_test() -> str:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github
            mock_sleep.return_value = None

            mock_cache_metrics = MagicMock()
            mock_client_metrics = MagicMock()
            config = self._create_config()
            retry_config = RetryConfig(
                enabled=True, backoff_durations=[0.001], rate_limit_wait=0.001
            )
            client = GHEClient(
                ghe_config=config,
                retry_config=retry_config,
                cache_metrics=mock_cache_metrics,
                client_metrics=mock_client_metrics,
            )

            call_count = 0

            def side_effect(_err: Exception) -> bool:
                nonlocal call_count
                return call_count == 2  # Rate limit on second call

            mock_is_rate_limit.side_effect = side_effect

            async def flaky_operation() -> str:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise Exception("Generic error")
                if call_count == 2:
                    raise Exception("Rate limit during backoff")
                return "success"

            result = await client._retry_with_backoff(flaky_operation, "test_op")

            # Verify cache metrics recorded rate limit
            mock_cache_metrics.record_rate_limit.assert_called_once_with("test_op")

            return str(result)

        result = asyncio.run(run_test())
        assert result == "success"

    @patch("common.clients.ghe_client.asyncio.sleep")
    @patch("common.clients.ghe_client.is_github_rate_limit_error")
    @patch("common.clients.ghe_client.GitHub")
    def test_retry_all_exhausted_records_final_failure_metrics(
        self,
        mock_github_class: MagicMock,
        mock_is_rate_limit: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        """Test that final failure after all retries records metrics."""

        async def run_test() -> None:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github
            mock_sleep.return_value = None
            mock_is_rate_limit.return_value = False

            mock_client_metrics = MagicMock()
            config = self._create_config()
            retry_config = RetryConfig(enabled=True, backoff_durations=[0.001, 0.001])
            client = GHEClient(
                ghe_config=config,
                retry_config=retry_config,
                client_metrics=mock_client_metrics,
            )

            async def always_failing() -> str:
                raise Exception("Persistent failure")

            with pytest.raises(Exception, match="Persistent failure"):
                await client._retry_with_backoff(always_failing, "test_op")

            # Verify final failure metrics recorded
            last_call = mock_client_metrics.record_request.call_args
            assert last_call[0][3] == 500

        asyncio.run(run_test())

    @patch("common.clients.ghe_client.GitHub")
    def test_retry_disabled_raises_immediately(
        self, mock_github_class: MagicMock
    ) -> None:
        """Test that errors are raised immediately when retries disabled."""

        async def run_test() -> None:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            config = self._create_config()
            retry_config = RetryConfig(enabled=False)
            client = GHEClient(ghe_config=config, retry_config=retry_config)

            async def failing_operation() -> None:
                raise ValueError("Test error")

            with pytest.raises(ValueError, match="Test error"):
                # Pass the function, not the coroutine - allows retry to create fresh coroutines
                await client._retry_with_backoff(failing_operation, "test_op")

        asyncio.run(run_test())

    @patch("common.clients.ghe_client.asyncio.sleep")
    @patch("common.clients.ghe_client.GitHub")
    def test_retry_on_generic_error(
        self, mock_github_class: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Test retry with backoff on generic errors."""

        async def run_test() -> str:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github
            mock_sleep.return_value = None

            config = self._create_config()
            retry_config = RetryConfig(
                enabled=True,
                backoff_durations=[0.001, 0.002],
            )
            client = GHEClient(ghe_config=config, retry_config=retry_config)

            call_count = 0

            async def flaky_operation() -> str:
                nonlocal call_count
                call_count += 1
                if call_count < 2:
                    raise ValueError("Transient error")
                return "success"

            # Pass the function (not coroutine) so retry can create fresh coroutines
            result = await client._retry_with_backoff(flaky_operation, "test_op")
            return str(result)

        result = asyncio.run(run_test())
        assert result == "success"

    @patch("common.clients.ghe_client.asyncio.sleep")
    @patch("common.clients.ghe_client.is_github_rate_limit_error")
    @patch("common.clients.ghe_client.GitHub")
    def test_retry_on_rate_limit_error(
        self,
        mock_github_class: MagicMock,
        mock_is_rate_limit: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        """Test rate limit triggers wait and retry."""

        async def run_test() -> str:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github
            mock_sleep.return_value = None
            mock_is_rate_limit.return_value = True  # First error is rate limit

            config = self._create_config()
            retry_config = RetryConfig(
                enabled=True,
                rate_limit_wait=0.001,  # Short wait for test
            )
            client = GHEClient(ghe_config=config, retry_config=retry_config)

            call_count = 0

            async def rate_limited_operation() -> str:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise Exception("Rate limit exceeded")
                return "success after rate limit"

            result = await client._retry_with_backoff(rate_limited_operation, "test_op")
            return str(result)

        result = asyncio.run(run_test())
        assert result == "success after rate limit"

    @patch("common.clients.ghe_client.asyncio.sleep")
    @patch("common.clients.ghe_client.is_github_rate_limit_error")
    @patch("common.clients.ghe_client.GitHub")
    def test_retry_rate_limit_then_fails(
        self,
        mock_github_class: MagicMock,
        mock_is_rate_limit: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        """Test rate limit → wait → retry fails → raises."""

        async def run_test() -> None:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github
            mock_sleep.return_value = None
            mock_is_rate_limit.return_value = True  # Error is rate limit

            config = self._create_config()
            retry_config = RetryConfig(
                enabled=True,
                rate_limit_wait=0.001,
            )
            client = GHEClient(ghe_config=config, retry_config=retry_config)

            async def always_failing_operation() -> str:
                raise Exception("Still failing after rate limit wait")

            with pytest.raises(Exception, match="Still failing after rate limit wait"):
                await client._retry_with_backoff(always_failing_operation, "test_op")

        asyncio.run(run_test())

    @patch("common.clients.ghe_client.asyncio.sleep")
    @patch("common.clients.ghe_client.is_github_rate_limit_error")
    @patch("common.clients.ghe_client.GitHub")
    def test_retry_backoff_hits_rate_limit_then_succeeds(
        self,
        mock_github_class: MagicMock,
        mock_is_rate_limit: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        """Test generic error → backoff → rate limit → wait → succeeds."""

        async def run_test() -> str:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github
            mock_sleep.return_value = None

            config = self._create_config()
            retry_config = RetryConfig(
                enabled=True,
                backoff_durations=[0.001],
                rate_limit_wait=0.001,
            )
            client = GHEClient(ghe_config=config, retry_config=retry_config)

            call_count = 0

            # First call: generic error (not rate limit)
            # Second call (during backoff): rate limit error
            # Third call (after rate limit wait): success
            def side_effect(_err: Exception) -> bool:
                nonlocal call_count
                # Return True for rate limit on second call
                return call_count == 2

            mock_is_rate_limit.side_effect = side_effect

            async def flaky_operation() -> str:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise Exception("Generic error")
                if call_count == 2:
                    raise Exception("Rate limit during backoff")
                return "success after rate limit in backoff"

            result = await client._retry_with_backoff(flaky_operation, "test_op")
            return str(result)

        result = asyncio.run(run_test())
        assert result == "success after rate limit in backoff"

    @patch("common.clients.ghe_client.asyncio.sleep")
    @patch("common.clients.ghe_client.is_github_rate_limit_error")
    @patch("common.clients.ghe_client.GitHub")
    def test_retry_backoff_hits_rate_limit_then_fails(
        self,
        mock_github_class: MagicMock,
        mock_is_rate_limit: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        """Test generic error → backoff → rate limit → wait → fails."""

        async def run_test() -> None:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github
            mock_sleep.return_value = None

            config = self._create_config()
            retry_config = RetryConfig(
                enabled=True,
                backoff_durations=[0.001],
                rate_limit_wait=0.001,
            )
            client = GHEClient(ghe_config=config, retry_config=retry_config)

            call_count = 0

            # Return True for rate limit on second call
            def side_effect(_err: Exception) -> bool:
                nonlocal call_count
                return call_count == 2

            mock_is_rate_limit.side_effect = side_effect

            async def flaky_operation() -> str:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise Exception("Generic error")
                if call_count == 2:
                    raise Exception("Rate limit during backoff")
                # Third call also fails
                raise Exception("Failed after rate limit in backoff")

            with pytest.raises(Exception, match="Failed after rate limit in backoff"):
                await client._retry_with_backoff(flaky_operation, "test_op")

        asyncio.run(run_test())

    @patch("common.clients.ghe_client.asyncio.sleep")
    @patch("common.clients.ghe_client.is_github_rate_limit_error")
    @patch("common.clients.ghe_client.GitHub")
    def test_retry_all_attempts_exhausted(
        self,
        mock_github_class: MagicMock,
        mock_is_rate_limit: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        """Test generic error → all backoff attempts fail → raises."""

        async def run_test() -> None:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github
            mock_sleep.return_value = None
            mock_is_rate_limit.return_value = False  # Not a rate limit error

            config = self._create_config()
            retry_config = RetryConfig(
                enabled=True,
                backoff_durations=[0.001, 0.001],  # Two retries
            )
            client = GHEClient(ghe_config=config, retry_config=retry_config)

            async def always_failing() -> str:
                raise Exception("Persistent failure")

            with pytest.raises(Exception, match="Persistent failure"):
                await client._retry_with_backoff(always_failing, "test_op")

        asyncio.run(run_test())


class TestGHEClientGetPRFiles(unittest.TestCase):
    """Tests for GHEClient.get_pr_files method."""

    def _create_config(self) -> BiztechGitHubConfig:
        import base64

        key = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
        return BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_id="12345",
            ghe_app_client_id="client-id",
            ghe_app_installation_id="67890",
            ghe_app_private_key=base64.standard_b64encode(key.encode()).decode(),
        )

    @patch("common.clients.ghe_client.GitHub")
    def test_get_pr_files_returns_file_list(self, mock_github_class: MagicMock) -> None:
        """Test basic file list retrieval."""

        async def run_test() -> list[PRFileInfo]:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            mock_response = MagicMock()
            mock_response.json.return_value = [
                {
                    "filename": "src/main.py",
                    "status": "modified",
                    "additions": 10,
                    "deletions": 5,
                    "changes": 15,
                },
                {
                    "filename": "src/utils.py",
                    "status": "added",
                    "additions": 20,
                    "deletions": 0,
                    "changes": 20,
                    "previous_filename": None,
                },
            ]

            mock_github.rest.pulls.async_list_files = AsyncMock(
                return_value=mock_response
            )

            config = self._create_config()
            client = GHEClient(ghe_config=config)
            return await client.get_pr_files("test-org", "test-repo", 42)

        files = asyncio.run(run_test())
        assert len(files) == 2
        assert files[0].filename == "src/main.py"
        assert files[0].status == "modified"
        assert files[1].filename == "src/utils.py"
        assert files[1].status == "added"

    @patch("common.clients.ghe_client.GitHub")
    def test_get_pr_files_empty_response(self, mock_github_class: MagicMock) -> None:
        """Test empty file list response."""

        async def run_test() -> list[PRFileInfo]:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            mock_response = MagicMock()
            mock_response.json.return_value = []

            mock_github.rest.pulls.async_list_files = AsyncMock(
                return_value=mock_response
            )

            config = self._create_config()
            client = GHEClient(ghe_config=config)
            return await client.get_pr_files("test-org", "test-repo", 42)

        files = asyncio.run(run_test())
        assert len(files) == 0

    @patch("common.clients.ghe_client.GitHub")
    def test_get_pr_files_pagination(self, mock_github_class: MagicMock) -> None:
        """Test that pagination fetches all files across pages."""

        async def run_test() -> list[PRFileInfo]:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            call_count = 0

            async def mock_list_files(**kwargs: Any) -> MagicMock:
                nonlocal call_count
                call_count += 1
                response = MagicMock()
                per_page = kwargs.get("per_page", 100)
                if call_count == 1:
                    # Return exactly per_page items to trigger next page
                    response.json.return_value = [
                        {
                            "filename": f"file_{i}.py",
                            "status": "modified",
                            "additions": 1,
                            "deletions": 0,
                            "changes": 1,
                        }
                        for i in range(per_page)
                    ]
                else:
                    # Second page has fewer items
                    response.json.return_value = [
                        {
                            "filename": "last_file.py",
                            "status": "added",
                            "additions": 5,
                            "deletions": 0,
                            "changes": 5,
                        }
                    ]
                return response

            mock_github.rest.pulls.async_list_files = mock_list_files

            config = self._create_config()
            client = GHEClient(ghe_config=config)
            return await client.get_pr_files("test-org", "test-repo", 42, per_page=3)

        files = asyncio.run(run_test())
        # 3 from first page + 1 from second page
        assert len(files) == 4
        assert files[-1].filename == "last_file.py"

    @patch("common.clients.ghe_client.GitHub")
    def test_get_pr_files_with_previous_filename(
        self, mock_github_class: MagicMock
    ) -> None:
        """Test file with previous_filename (renamed file)."""

        async def run_test() -> list[PRFileInfo]:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            mock_response = MagicMock()
            mock_response.json.return_value = [
                {
                    "filename": "src/new_name.py",
                    "status": "renamed",
                    "additions": 0,
                    "deletions": 0,
                    "changes": 0,
                    "previous_filename": "src/old_name.py",
                },
            ]

            mock_github.rest.pulls.async_list_files = AsyncMock(
                return_value=mock_response
            )

            config = self._create_config()
            client = GHEClient(ghe_config=config)
            return await client.get_pr_files("test-org", "test-repo", 42)

        files = asyncio.run(run_test())
        assert len(files) == 1
        assert files[0].filename == "src/new_name.py"
        assert files[0].previous_filename == "src/old_name.py"
        assert files[0].status == "renamed"


class TestGHEClientGetPullRequest(unittest.TestCase):
    """Tests for GHEClient.get_pull_request method."""

    def _create_config(self) -> BiztechGitHubConfig:
        import base64

        key = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
        return BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_id="12345",
            ghe_app_client_id="client-id",
            ghe_app_installation_id="67890",
            ghe_app_private_key=base64.standard_b64encode(key.encode()).decode(),
        )

    @patch("common.clients.ghe_client.GitHub")
    def test_returns_raw_pull_request_when_found(
        self, mock_github_class: MagicMock
    ) -> None:
        async def run_test() -> RawPullRequest | None:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "id": 999,
                "number": 42,
                "title": "Fix the bug",
                "body": "Description",
                "state": "closed",
                "locked": False,
                "created_at": "2024-01-15T10:00:00Z",
                "updated_at": "2024-01-15T11:00:00Z",
                "closed_at": "2024-01-15T11:00:00Z",
                "merged_at": "2024-01-15T11:00:00Z",
                "base": {"ref": "main"},
            }
            mock_github.rest.pulls.async_get = AsyncMock(return_value=mock_response)

            config = self._create_config()
            client = GHEClient(ghe_config=config)
            return await client.get_pull_request("test-org", "test-repo", 42)

        pr = asyncio.run(run_test())
        assert pr is not None
        assert pr.id == 999
        assert pr.number == 42
        assert pr.base_ref == "main"

    @patch("common.clients.ghe_client.GitHub")
    def test_returns_none_on_404(self, mock_github_class: MagicMock) -> None:
        async def run_test() -> RawPullRequest | None:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            mock_response = MagicMock()
            mock_response.status_code = 404

            async def raise_not_found(*args: Any, **kwargs: Any) -> Any:
                raise RequestFailed(mock_response)

            mock_github.rest.pulls.async_get = raise_not_found

            config = self._create_config()
            client = GHEClient(ghe_config=config)
            return await client.get_pull_request("test-org", "test-repo", 9999)

        pr = asyncio.run(run_test())
        assert pr is None

    @patch("common.clients.ghe_client.GitHub")
    def test_reraises_non_404_errors(self, mock_github_class: MagicMock) -> None:
        async def run_test() -> None:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            mock_response = MagicMock()
            mock_response.status_code = 500

            async def raise_server_error(*args: Any, **kwargs: Any) -> Any:
                raise RequestFailed(mock_response)

            mock_github.rest.pulls.async_get = raise_server_error

            config = self._create_config()
            client = GHEClient(ghe_config=config)
            await client.get_pull_request("test-org", "test-repo", 42)

        with pytest.raises(RequestFailed):
            asyncio.run(run_test())


class TestGHEClientGetPRComments(unittest.TestCase):
    """Tests for GHEClient.get_pr_comments method."""

    def _create_config(self) -> BiztechGitHubConfig:
        import base64

        key = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
        return BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_id="12345",
            ghe_app_client_id="client-id",
            ghe_app_installation_id="67890",
            ghe_app_private_key=base64.standard_b64encode(key.encode()).decode(),
        )

    @patch("common.clients.ghe_client.GitHub")
    def test_get_pr_comments_returns_bodies_and_skips_empty(
        self, mock_github_class: MagicMock
    ) -> None:
        """Non-empty comment bodies are returned in order; blanks are skipped."""

        async def run_test() -> list[str]:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            sl = {"login": "spacelift-prod[bot]"}
            mock_response = MagicMock()
            mock_response.json.return_value = [
                {"body": "First comment", "user": sl},
                {"body": "   ", "user": sl},  # whitespace-only — skipped
                {"body": None, "user": sl},  # null body — skipped
                {"body": "Second comment", "user": sl},
            ]
            mock_github.rest.issues.async_list_comments = AsyncMock(
                return_value=mock_response
            )

            client = GHEClient(ghe_config=self._create_config())
            return await client.get_pr_comments("test-org", "test-repo", 42)

        comments = asyncio.run(run_test())
        assert comments == ["First comment", "Second comment"]

    @patch("common.clients.ghe_client.GitHub")
    def test_get_pr_comments_pagination(self, mock_github_class: MagicMock) -> None:
        """Pagination fetches all comments across pages."""

        async def run_test() -> list[str]:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            call_count = 0

            async def mock_list_comments(**kwargs: Any) -> MagicMock:
                nonlocal call_count
                call_count += 1
                response = MagicMock()
                sl = {"login": "spacelift-prod[bot]"}
                per_page = kwargs.get("per_page", 100)
                if call_count == 1:
                    response.json.return_value = [
                        {"body": f"comment_{i}", "user": sl} for i in range(per_page)
                    ]
                else:
                    response.json.return_value = [{"body": "last comment", "user": sl}]
                return response

            mock_github.rest.issues.async_list_comments = mock_list_comments

            client = GHEClient(ghe_config=self._create_config())
            return await client.get_pr_comments("test-org", "test-repo", 42, per_page=3)

        comments = asyncio.run(run_test())
        assert len(comments) == 4
        assert comments[-1] == "last comment"

    @patch("common.clients.ghe_client.GitHub")
    def test_get_pr_comments_empty(self, mock_github_class: MagicMock) -> None:
        """An empty response yields no comments."""

        async def run_test() -> list[str]:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github
            mock_response = MagicMock()
            mock_response.json.return_value = []
            mock_github.rest.issues.async_list_comments = AsyncMock(
                return_value=mock_response
            )
            client = GHEClient(ghe_config=self._create_config())
            return await client.get_pr_comments("test-org", "test-repo", 42)

        assert asyncio.run(run_test()) == []

    @patch("common.clients.ghe_client.GitHub")
    def test_get_pr_comments_filters_by_author_and_title(
        self, mock_github_class: MagicMock
    ) -> None:
        """Only spacelift comments and the jenkins 'Review Summary' are kept."""

        async def run_test() -> list[str]:
            mock_github = MagicMock()
            mock_github_class.return_value = mock_github

            mock_response = MagicMock()
            mock_response.json.return_value = [
                {
                    "body": "### Spacelift update: apply succeeded.",
                    "user": {"login": "spacelift-prod[bot]"},
                },
                {
                    "body": "### Review Summary *(jenkins-airchat-review)*\nGood.",
                    "user": {"login": "jenkins-prod[bot]"},
                },
                {
                    "body": "### Review Issues *(jenkins-airchat-review)*\nIssue.",
                    "user": {"login": "jenkins-prod[bot]"},
                },
                {"body": "LGTM, merging", "user": {"login": "some-engineer"}},
            ]
            mock_github.rest.issues.async_list_comments = AsyncMock(
                return_value=mock_response
            )

            client = GHEClient(ghe_config=self._create_config())
            return await client.get_pr_comments("test-org", "test-repo", 42)

        comments = asyncio.run(run_test())
        assert comments == [
            "### Spacelift update: apply succeeded.",
            "### Review Summary *(jenkins-airchat-review)*\nGood.",
        ]


class TestGHEClientProcessPRPublisherComments(unittest.TestCase):
    """Comments are folded into the enrichment request on the publisher path."""

    def _create_config(self) -> BiztechGitHubConfig:
        import base64

        key = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
        return BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_id="12345",
            ghe_app_client_id="client-id",
            ghe_app_installation_id="67890",
            ghe_app_private_key=base64.standard_b64encode(key.encode()).decode(),
            ghe_pr_summary_prompt="Summarize this PR",
        )

    @patch("common.clients.ghe_client.GitHub")
    def test_publisher_path_includes_comments(
        self, mock_github_class: MagicMock
    ) -> None:
        """The published EnrichmentRequest content carries description + comments."""
        from common.clients.ghe_client import RawPullRequest

        async def run_test() -> list[Any]:
            mock_github_class.return_value = MagicMock()
            client = GHEClient(ghe_config=self._create_config())
            client.get_pr_files = AsyncMock(return_value=[])  # type: ignore[method-assign]
            client.get_pr_comments = AsyncMock(  # type: ignore[method-assign]
                return_value=["Please add a test", "LGTM"]
            )

            published: list[Any] = []

            async def fake_publish(msg: Any) -> str:
                published.append(msg)
                return "msg-id"

            client.enrichment_publisher = MagicMock()
            client.enrichment_publisher.publish = fake_publish

            pr = RawPullRequest(
                id=123,
                number=42,
                title="Test PR",
                body="Adds a feature",
                state="closed",
                locked=False,
                created_at=datetime(2024, 6, 1, 12, 0, 0),
                updated_at=datetime(2024, 6, 2, 12, 0, 0),
                closed_at=None,
                merged_at=datetime(2024, 6, 2, 12, 0, 0),
                base_ref="main",
            )
            org = GHEOrg(org_id=1, org_login="test-org")
            repo = GHERepo(repo_id=1, repo_name="test-repo", org_id=1)
            await client._process_pr_with_cache(pr, org, repo, PRHashCache())
            return published

        published = asyncio.run(run_test())
        assert len(published) == 1
        content = published[0].content
        assert content == {
            "original_description": build_ghe_pr_content(
                "Test PR", "Adds a feature", ["Please add a test", "LGTM"]
            )
        }

    @patch("common.clients.ghe_client.GitHub")
    def test_process_pr_extracts_jira_tcmr_key(
        self, mock_github_class: MagicMock
    ) -> None:
        """A TCMR browse URL in the PR body is captured as jira_tcmr_key."""
        from common.clients.ghe_client import RawPullRequest

        async def run_test() -> Any:
            mock_github_class.return_value = MagicMock()
            client = GHEClient(ghe_config=self._create_config())
            client.get_pr_files = AsyncMock(return_value=[])  # type: ignore[method-assign]
            client.get_pr_comments = AsyncMock(return_value=[])  # type: ignore[method-assign]

            pr = RawPullRequest(
                id=200,
                number=7,
                title="Roll out change",
                body="Change per https://jira.airbnb.biz/browse/TCMR-12345 review",
                state="closed",
                locked=False,
                created_at=datetime(2024, 6, 1, 12, 0, 0),
                updated_at=datetime(2024, 6, 2, 12, 0, 0),
                closed_at=None,
                merged_at=datetime(2024, 6, 2, 12, 0, 0),
                base_ref="main",
            )
            org = GHEOrg(org_id=1, org_login="test-org")
            repo = GHERepo(repo_id=1, repo_name="test-repo", org_id=1)
            ghe_pr, _cache_hit, _new, _files = await client._process_pr_with_cache(
                pr, org, repo, PRHashCache()
            )
            return ghe_pr

        ghe_pr = asyncio.run(run_test())
        assert ghe_pr.jira_tcmr_key == "TCMR-12345"

    @patch("common.clients.ghe_client.GitHub")
    def test_process_pr_without_tcmr_link_sets_none(
        self, mock_github_class: MagicMock
    ) -> None:
        """A PR with no TCMR browse URL leaves jira_tcmr_key as None."""
        from common.clients.ghe_client import RawPullRequest

        async def run_test() -> Any:
            mock_github_class.return_value = MagicMock()
            client = GHEClient(ghe_config=self._create_config())
            client.get_pr_files = AsyncMock(return_value=[])  # type: ignore[method-assign]
            client.get_pr_comments = AsyncMock(return_value=[])  # type: ignore[method-assign]

            pr = RawPullRequest(
                id=201,
                number=8,
                title="Refactor module",
                body="No ticket linked here, just a bare TCMR-99 mention",
                state="closed",
                locked=False,
                created_at=datetime(2024, 6, 1, 12, 0, 0),
                updated_at=datetime(2024, 6, 2, 12, 0, 0),
                closed_at=None,
                merged_at=datetime(2024, 6, 2, 12, 0, 0),
                base_ref="main",
            )
            org = GHEOrg(org_id=1, org_login="test-org")
            repo = GHERepo(repo_id=1, repo_name="test-repo", org_id=1)
            ghe_pr, _cache_hit, _new, _files = await client._process_pr_with_cache(
                pr, org, repo, PRHashCache()
            )
            return ghe_pr

        ghe_pr = asyncio.run(run_test())
        assert ghe_pr.jira_tcmr_key is None


if __name__ == "__main__":
    unittest.main()
