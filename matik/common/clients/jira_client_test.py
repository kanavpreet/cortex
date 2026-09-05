"""Unit tests for jira_client.py."""

import unittest
from unittest.mock import MagicMock, patch

import httpx

from common.clients.jira_client import JiraClient, JiraHashCache, create_jira_client
from common.daos.jira_issues_dao import JiraHashInfo
from common.metrics.client_metrics import ClientMetrics
from common.metrics.jira_cache_metrics import JiraCacheMetrics
from common.models.jira_config import JiraConfig


class TestJiraClientInit(unittest.TestCase):
    """Tests for JiraClient initialization."""

    def _create_jira_config(self) -> JiraConfig:
        return JiraConfig(
            base_url="https://jira.example.com",
            username="test-user",
            password="test-pass",
            pagination_max_results=100,
            llm_description_prompt="Summarize this issue",
            llm_comments_prompt="Summarize these comments",
        )

    def test_init_sets_base_url_and_auth(self) -> None:
        """Test initialization sets base URL and auth from config."""
        config = self._create_jira_config()

        client = JiraClient(jira_config=config)

        assert client._base_url == "https://jira.example.com"
        assert client._auth == ("test-user", "test-pass")

    def test_init_strips_trailing_slash(self) -> None:
        """Test that trailing slash is stripped from base URL."""
        config = JiraConfig(
            base_url="https://jira.example.com/",
            username="test-user",
            password="test-pass",
        )

        client = JiraClient(jira_config=config)

        assert client._base_url == "https://jira.example.com"


class TestJiraClientGetAllIssues(unittest.TestCase):
    """Tests for JiraClient._get_all_issues method."""

    def _create_jira_config(self) -> JiraConfig:
        return JiraConfig(
            base_url="https://jira.example.com",
            username="test-user",
            password="test-pass",
            pagination_max_results=50,
        )

    @patch("common.clients.jira_client.httpx.Client")
    def test_get_all_issues_single_page(self, mock_client_class: MagicMock) -> None:
        """Test fetching issues with single page of results."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "issues": [
                {"id": "1", "key": "TEST-1", "fields": {"summary": "Issue 1"}},
                {"id": "2", "key": "TEST-2", "fields": {"summary": "Issue 2"}},
            ],
            "total": 2,
        }
        mock_client.get.return_value = mock_response

        config = self._create_jira_config()
        client = JiraClient(jira_config=config)
        issues = client._get_all_issues("project = TEST")

        assert len(issues) == 2
        assert issues[0]["key"] == "TEST-1"
        assert issues[1]["key"] == "TEST-2"

    @patch("common.clients.jira_client.httpx.Client")
    def test_get_all_issues_uses_configured_timeout(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test that configured client_timeout is passed to httpx.Client."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {"issues": [], "total": 0}
        mock_client.get.return_value = mock_response

        config = JiraConfig(
            base_url="https://jira.example.com",
            username="test-user",
            password="test-pass",
            client_timeout=120.0,  # Custom timeout
        )
        client = JiraClient(jira_config=config)
        client._get_all_issues("project = TEST")

        # Verify httpx.Client was called with the configured timeout
        mock_client_class.assert_called_once_with(timeout=120.0)

    @patch("common.clients.jira_client.httpx.Client")
    def test_get_all_issues_uses_default_timeout(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test that default timeout (60.0) is used when not configured."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {"issues": [], "total": 0}
        mock_client.get.return_value = mock_response

        config = JiraConfig(
            base_url="https://jira.example.com",
            username="test-user",
            password="test-pass",
            # No client_timeout specified - uses default
        )
        client = JiraClient(jira_config=config)
        client._get_all_issues("project = TEST")

        # Verify httpx.Client was called with the default timeout
        mock_client_class.assert_called_once_with(timeout=60.0)

    @patch("common.clients.jira_client.httpx.Client")
    def test_get_all_issues_pagination(self, mock_client_class: MagicMock) -> None:
        """Test fetching issues with pagination."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        # First page
        first_response = MagicMock()
        first_response.json.return_value = {
            "issues": [{"id": "1", "key": "TEST-1", "fields": {}}],
            "total": 2,
        }

        # Second page
        second_response = MagicMock()
        second_response.json.return_value = {
            "issues": [{"id": "2", "key": "TEST-2", "fields": {}}],
            "total": 2,
        }

        mock_client.get.side_effect = [first_response, second_response]

        config = JiraConfig(
            base_url="https://jira.example.com",
            username="test-user",
            password="test-pass",
            pagination_max_results=1,  # Force pagination
        )
        client = JiraClient(jira_config=config)
        issues = client._get_all_issues("project = TEST")

        assert len(issues) == 2
        assert mock_client.get.call_count == 2


class TestJiraClientGetIssue(unittest.TestCase):
    """Tests for JiraClient.get_issue method."""

    def _create_jira_config(self) -> JiraConfig:
        return JiraConfig(
            base_url="https://jira.example.com",
            username="test-user",
            password="test-pass",
            pagination_max_results=50,
        )

    @patch("common.clients.jira_client.httpx.Client")
    def test_returns_issue_when_found(self, mock_client_class: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "issues": [{"id": "1", "key": "TCMR-1234", "fields": {"summary": "Issue"}}],
            "total": 1,
        }
        mock_client.get.return_value = mock_response

        config = self._create_jira_config()
        client = JiraClient(jira_config=config)
        issue = client.get_issue("TCMR-1234")

        assert issue is not None
        assert issue["key"] == "TCMR-1234"

    @patch("common.clients.jira_client.httpx.Client")
    def test_uses_key_scoped_jql(self, mock_client_class: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {"issues": [], "total": 0}
        mock_client.get.return_value = mock_response

        config = self._create_jira_config()
        client = JiraClient(jira_config=config)
        client.get_issue("TCMR-1234")

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params"))
        assert params["jql"] == "key = TCMR-1234"

    @patch("common.clients.jira_client.httpx.Client")
    def test_returns_none_when_not_found(self, mock_client_class: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {"issues": [], "total": 0}
        mock_client.get.return_value = mock_response

        config = self._create_jira_config()
        client = JiraClient(jira_config=config)
        issue = client.get_issue("TCMR-9999")

        assert issue is None


class TestJiraClientConvertIssue(unittest.TestCase):
    """Tests for JiraClient._convert_issue method."""

    def _create_jira_config(self) -> JiraConfig:
        return JiraConfig(
            base_url="https://jira.example.com",
            username="test-user",
            password="test-pass",
        )

    def test_convert_issue_full_data(self) -> None:
        """Test converting issue with full data."""
        config = self._create_jira_config()
        client = JiraClient(jira_config=config)

        raw = {
            "id": "12345",
            "self": "https://jira.example.com/rest/api/2/issue/12345",
            "key": "TEST-1",
            "fields": {
                "summary": "Test issue",
                "environment": "Production",
                "status": {"self": "", "id": "1", "name": "Open"},
                "resolution": {"self": "", "id": "1", "name": "Fixed"},
                "created": "2024-01-01T10:00:00.000+0000",
                "updated": "2024-01-02T10:00:00.000+0000",
                "labels": ["bug", "critical"],
                "parent": {"id": "12344", "key": "TEST-PARENT"},
                "comment": {
                    "comments": [
                        {
                            "id": "1",
                            "body": "First comment",
                            "author": {"name": "user1"},
                        }
                    ]
                },
                "issuelinks": [
                    {
                        "id": "100",
                        "type": {
                            "id": "1",
                            "name": "Blocks",
                            "inward": "is blocked by",
                            "outward": "blocks",
                        },
                    }
                ],
            },
        }

        issue = client._convert_issue(raw)

        assert issue.id == "12345"
        assert issue.key == "TEST-1"
        assert issue.fields is not None
        assert issue.fields.summary == "Test issue"
        assert issue.fields.environment == "Production"
        assert issue.fields.status is not None
        assert issue.fields.status.name == "Open"
        assert issue.fields.resolution is not None
        assert issue.fields.resolution.name == "Fixed"
        assert issue.fields.labels == ["bug", "critical"]
        assert issue.fields.parent is not None
        assert issue.fields.parent.key == "TEST-PARENT"
        assert issue.fields.comments is not None
        assert issue.fields.comments.comments is not None
        assert len(issue.fields.comments.comments) == 1
        assert issue.fields.issuelinks is not None
        assert len(issue.fields.issuelinks) == 1

    def test_convert_issue_minimal_data(self) -> None:
        """Test converting issue with minimal data."""
        config = self._create_jira_config()
        client = JiraClient(jira_config=config)

        raw = {
            "id": "12345",
            "key": "TEST-1",
            "fields": {},
        }

        issue = client._convert_issue(raw)

        assert issue.id == "12345"
        assert issue.key == "TEST-1"
        assert issue.fields is not None
        assert issue.fields.status is None
        assert issue.fields.resolution is None


class TestCreateJiraClient(unittest.TestCase):
    """Tests for create_jira_client factory function."""

    def test_create_jira_client_basic(self) -> None:
        """Test factory function creates client correctly."""
        config = JiraConfig(
            base_url="https://jira.example.com",
            username="test-user",
            password="test-pass",
        )

        client = create_jira_client(config)

        assert isinstance(client, JiraClient)
        assert client._base_url == "https://jira.example.com"


class TestJiraHashCache(unittest.TestCase):
    """Tests for JiraHashCache dataclass."""

    def test_create_empty_cache(self) -> None:
        """Test creating empty hash cache."""
        cache = JiraHashCache()

        assert cache.hashes == {}

    def test_create_with_hashes(self) -> None:
        """Test creating hash cache with pre-populated data."""
        hash_info = JiraHashInfo(
            issue_key="TCMR-123",
            summary_hash="abc123",
            comments_hash="def456",
            issue_summary="Cached summary",
            issue_comments_summary="Cached comments summary",
        )
        cache = JiraHashCache(hashes={"TCMR-123": hash_info})

        assert len(cache.hashes) == 1
        assert "TCMR-123" in cache.hashes
        assert cache.hashes["TCMR-123"].issue_summary == "Cached summary"


class TestJiraClientListIssuesWithHashCache(unittest.TestCase):
    """Tests for JiraClient.list_issues_with_hash_cache method."""

    def _create_jira_config(self) -> JiraConfig:
        return JiraConfig(
            base_url="https://jira.example.com",
            username="test-user",
            password="test-pass",
            pagination_max_results=100,
            llm_description_prompt="Summarize this issue",
            llm_comments_prompt="Summarize these comments",
        )

    @patch("common.clients.jira_client.httpx.Client")
    def test_list_issues_without_publisher_returns_base_issues(
        self, mock_client_class: MagicMock
    ) -> None:
        """Without an enrichment_publisher, issues are returned unsummarized."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "issues": [
                {
                    "id": "12345",
                    "key": "TEST-1",
                    "fields": {"summary": "Test issue summary"},
                },
            ],
            "total": 1,
        }
        mock_client.get.return_value = mock_response

        config = self._create_jira_config()
        client = JiraClient(jira_config=config)
        issues = client.list_issues_with_hash_cache("project = TEST")

        assert len(issues) == 1
        assert issues[0].key == "TEST-1"
        assert issues[0].issue_summary is None

    @patch("common.clients.jira_client.httpx.Client")
    def test_list_issues_publisher_path_forwards_to_sqs(
        self, mock_client_class: MagicMock
    ) -> None:
        """With an enrichment_publisher set, raw content is forwarded to SQS.

        The historian no longer summarizes inline; it publishes the issue's
        summary and aggregated comments to the enricher, which owns hash dedup.
        """
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "issues": [
                {
                    "id": "12345",
                    "key": "TEST-1",
                    "fields": {
                        "summary": "Test issue summary",
                        "comment": {"comments": [{"id": "1", "body": "A comment"}]},
                    },
                },
            ],
            "total": 1,
        }
        mock_client.get.return_value = mock_response

        mock_publisher = MagicMock()
        mock_publisher.publish = MagicMock()

        config = self._create_jira_config()
        client = JiraClient(jira_config=config, enrichment_publisher=mock_publisher)
        issues = client.list_issues_with_hash_cache("project = TEST")

        assert len(issues) == 1
        # Raw content forwarded to the enricher queue; no inline summary set.
        mock_publisher.publish.assert_called_once()
        published = mock_publisher.publish.call_args[0][0]
        assert published.source_type == "jira"
        assert published.entity_id == {"issue_key": "TEST-1"}
        assert published.content["issue_description"] == "Test issue summary"
        assert "aggregated_comments" in published.content

    @patch("common.clients.jira_client.httpx.Client")
    def test_list_issues_new_vs_existing_counted_via_hash_cache(
        self, mock_client_class: MagicMock
    ) -> None:
        """hash_cache membership only distinguishes new vs existing for stats;
        it no longer gates any inline LLM call or summary reuse."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "issues": [
                {"id": "1", "key": "TEST-1", "fields": {"summary": "Summary 1"}},
                {"id": "2", "key": "TEST-2", "fields": {"summary": "Summary 2"}},
            ],
            "total": 2,
        }
        mock_client.get.return_value = mock_response

        hash_info = JiraHashInfo(
            issue_key="TEST-1",
            summary_hash="some_hash",
            comments_hash=None,
            issue_summary="Existing summary",
            issue_comments_summary=None,
        )
        cache = JiraHashCache(hashes={"TEST-1": hash_info})

        config = self._create_jira_config()
        client = JiraClient(jira_config=config)
        issues = client.list_issues_with_hash_cache("project = TEST", cache)

        assert len(issues) == 2
        assert {i.key for i in issues} == {"TEST-1", "TEST-2"}
        # Base fields only — no inline summarization occurs regardless of cache membership.
        assert all(i.issue_summary is None for i in issues)


class TestJiraClientMetrics(unittest.TestCase):
    """Tests for JiraClient metrics instrumentation."""

    def _create_jira_config(self) -> JiraConfig:
        return JiraConfig(
            base_url="https://jira.example.com",
            username="test-user",
            password="test-pass",
            pagination_max_results=50,
        )

    @patch("common.clients.jira_client.httpx.Client")
    def test_get_all_issues_records_metrics_on_success(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test that metrics are recorded on successful API call."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"issues": [], "total": 0}
        mock_client.get.return_value = mock_response

        # Create mock client metrics
        mock_client_metrics = MagicMock(spec=ClientMetrics)

        config = self._create_jira_config()
        client = JiraClient(jira_config=config, client_metrics=mock_client_metrics)
        client._get_all_issues("project = TEST")

        # Verify metrics were recorded
        mock_client_metrics.record_request.assert_called_once()
        call_args = mock_client_metrics.record_request.call_args
        assert call_args[0][0] == "GET"  # method
        assert call_args[0][1] == "/rest/api/2/search"  # endpoint
        assert call_args[0][3] == 200  # status_code

    @patch("common.clients.jira_client.httpx.Client")
    def test_get_all_issues_records_metrics_on_http_error(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test that metrics are recorded on HTTP error."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        # Create a proper HTTPStatusError
        mock_response = MagicMock()
        mock_response.status_code = 500
        http_error = httpx.HTTPStatusError(
            "Server error", request=MagicMock(), response=mock_response
        )
        mock_client.get.side_effect = http_error

        # Create mock client metrics
        mock_client_metrics = MagicMock(spec=ClientMetrics)

        config = self._create_jira_config()
        client = JiraClient(jira_config=config, client_metrics=mock_client_metrics)

        with self.assertRaises(httpx.HTTPStatusError):
            client._get_all_issues("project = TEST")

        # Verify metrics were recorded with error
        mock_client_metrics.record_request.assert_called_once()
        call_args = mock_client_metrics.record_request.call_args
        assert call_args[0][0] == "GET"  # method
        assert call_args[0][1] == "/rest/api/2/search"  # endpoint
        assert call_args[0][3] == 500  # status_code
        assert call_args[0][4] is http_error  # error

    @patch("common.clients.jira_client.httpx.Client")
    def test_get_all_issues_without_metrics(self, mock_client_class: MagicMock) -> None:
        """Test that client works without metrics (client_metrics=None)."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "issues": [{"id": "1", "key": "TEST-1", "fields": {}}],
            "total": 1,
        }
        mock_client.get.return_value = mock_response

        config = self._create_jira_config()
        client = JiraClient(jira_config=config, client_metrics=None)
        issues = client._get_all_issues("project = TEST")

        assert len(issues) == 1

    def test_create_jira_client_with_metrics(self) -> None:
        """Test factory function accepts metrics parameters."""
        config = self._create_jira_config()
        mock_client_metrics = MagicMock(spec=ClientMetrics)
        mock_cache_metrics = MagicMock(spec=JiraCacheMetrics)

        client = create_jira_client(
            config,
            client_metrics=mock_client_metrics,
            cache_metrics=mock_cache_metrics,
        )

        assert client.client_metrics is mock_client_metrics
        assert client.cache_metrics is mock_cache_metrics


class TestJiraClientCrawlerStats(unittest.TestCase):
    """Tests for JiraClient crawler-stats instrumentation."""

    def _create_jira_config(self) -> JiraConfig:
        return JiraConfig(
            base_url="https://jira.example.com",
            username="test-user",
            password="test-pass",
            pagination_max_results=100,
            llm_description_prompt="Summarize this issue",
            llm_comments_prompt="Summarize these comments",
        )

    @patch("common.clients.jira_client.httpx.Client")
    def test_records_crawler_stats(self, mock_client_class: MagicMock) -> None:
        """Test that crawler stats are recorded after processing issues."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "issues": [
                {
                    "id": "12345",
                    "key": "TEST-1",
                    "fields": {"summary": "New issue summary"},
                },
            ],
            "total": 1,
        }
        mock_client.get.return_value = mock_response

        # Create mock cache metrics
        mock_cache_metrics = MagicMock(spec=JiraCacheMetrics)

        # Empty cache - all issues are new
        cache = JiraHashCache()

        config = self._create_jira_config()
        client = JiraClient(jira_config=config, cache_metrics=mock_cache_metrics)
        client.list_issues_with_hash_cache("project = TEST", cache)

        # Verify crawler stats were recorded
        mock_cache_metrics.record_crawler_stats.assert_called_once()


if __name__ == "__main__":
    unittest.main()
