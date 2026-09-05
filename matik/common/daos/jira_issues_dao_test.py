"""Tests for JIRA Issues DAO."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from common.daos.base_dao import WriteOutcome
from common.daos.jira_issues_dao import JiraHashInfo, JiraIssuesDAO
from common.metrics import DBMetrics
from common.models.jira_issue_record import JiraIssueRecord


class TestJiraIssuesDAO:
    """Test suite for JiraIssuesDAO class."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> JiraIssuesDAO:
        """Create DAO instance with mock engine."""
        return JiraIssuesDAO(mock_engine)

    def test_constructor(self, mock_engine: MagicMock) -> None:
        """Test DAO constructor stores engine."""
        dao = JiraIssuesDAO(mock_engine)
        assert dao._engine is mock_engine
        assert dao._metrics is None

    def test_constructor_with_metrics(self, mock_engine: MagicMock) -> None:
        """Test DAO constructor stores metrics."""
        mock_metrics = MagicMock(spec=DBMetrics)
        dao = JiraIssuesDAO(mock_engine, metrics=mock_metrics)
        assert dao._engine is mock_engine
        assert dao._metrics is mock_metrics


class TestFindIssuesByKeys:
    """Tests for find_issues_by_keys method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> JiraIssuesDAO:
        return JiraIssuesDAO(mock_engine)

    def _make_mock_row(self, issue_key: str) -> MagicMock:
        mock_row = MagicMock()
        mock_row._mapping = {
            "id": 1,
            "issue_id": "10001",
            "issue_key": issue_key,
            "ticket_type": "tcmr",
            "summary": f"Summary for {issue_key}",
            "status_name": "Open",
            "created_at": datetime(2024, 1, 15, 10, 30, 0),
            "issue_summary": None,
            "issue_comments_summary": None,
            "summary_hash": None,
            "comments_hash": None,
            "tcmr_related_git_pr_link": None,
            "tcmr_related_services": None,
            "services": None,
            "tcmr_planned_start_date": None,
            "tcmr_planned_end_date": None,
        }
        return mock_row

    def test_returns_matching_issues(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test returns list of issues for matching issue_keys."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [
            self._make_mock_row("TCMR-1"),
            self._make_mock_row("TCMR-2"),
        ]

        result = dao.find_issues_by_keys(["TCMR-1", "TCMR-2"])

        assert len(result) == 2
        assert result[0].issue_key == "TCMR-1"
        assert result[1].issue_key == "TCMR-2"

    def test_returns_empty_list_for_empty_input(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test returns empty list without hitting the database for empty input."""
        result = dao.find_issues_by_keys([])

        assert result == []
        mock_engine.connect.assert_not_called()

    def test_returns_empty_list_when_none_found(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test returns empty list when no issues match."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []

        result = dao.find_issues_by_keys(["TCMR-nonexistent"])

        assert result == []

    def test_returns_empty_list_on_database_error(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test returns empty list on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        result = dao.find_issues_by_keys(["TCMR-1", "TCMR-2"])

        assert result == []


class TestFindIssueByKey:
    """Tests for find_issue_by_key method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> JiraIssuesDAO:
        """Create DAO instance with mock engine."""
        return JiraIssuesDAO(mock_engine)

    def test_find_success(self, dao: JiraIssuesDAO, mock_engine: MagicMock) -> None:
        """Test find returns issue when found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_row = MagicMock()
        mock_row._mapping = {
            "id": 1,
            "issue_id": "12345",
            "issue_key": "TCMR-123",
            "ticket_type": "tcmr",
            "summary": "Test issue summary",
            "status_name": "Open",
            "created_at": datetime(2024, 1, 15, 10, 30, 0),
        }
        result = MagicMock()
        result.fetchone.return_value = mock_row
        conn.execute.return_value = result

        issue = dao.find_issue_by_key("TCMR-123")

        assert issue is not None
        assert issue.id == 1
        assert issue.issue_id == "12345"
        assert issue.issue_key == "TCMR-123"
        assert issue.ticket_type == "tcmr"
        assert issue.summary == "Test issue summary"
        assert issue.status_name == "Open"

    def test_find_not_found(self, dao: JiraIssuesDAO, mock_engine: MagicMock) -> None:
        """Test find returns None when issue not found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchone.return_value = None
        conn.execute.return_value = result

        issue = dao.find_issue_by_key("NONEXISTENT-999")

        assert issue is None

    def test_find_database_error(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test find returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database connection error")

        issue = dao.find_issue_by_key("TCMR-123")

        assert issue is None


class TestUpdateLlmFields:
    """Tests for update_llm_fields method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> JiraIssuesDAO:
        """Create DAO instance with mock engine."""
        return JiraIssuesDAO(mock_engine)

    def test_update_llm_fields_success_all_fields(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test update_llm_fields returns True with all fields provided."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.rowcount = 1
        conn.execute.return_value = result

        success = dao.update_llm_fields(
            issue_key="TCMR-123",
            issue_summary="LLM summary",
            summary_hash="hash123",
            issue_comments_summary="LLM comments summary",
            comments_hash="hash456",
        )

        assert success is WriteOutcome.WRITTEN
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_update_llm_fields_partial_summary_only(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test update_llm_fields with only summary fields."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.rowcount = 1
        conn.execute.return_value = result

        success = dao.update_llm_fields(
            issue_key="TCMR-123",
            issue_summary="LLM summary",
            summary_hash="hash123",
        )

        assert success is WriteOutcome.WRITTEN
        conn.execute.assert_called_once()

    def test_update_llm_fields_partial_comments_only(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test update_llm_fields with only comments fields."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.rowcount = 1
        conn.execute.return_value = result

        success = dao.update_llm_fields(
            issue_key="TCMR-123",
            issue_comments_summary="LLM comments",
            comments_hash="hash789",
        )

        assert success is WriteOutcome.WRITTEN
        conn.execute.assert_called_once()

    def test_update_llm_fields_no_values_returns_true(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test update_llm_fields returns WRITTEN when no non-None values provided."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        success = dao.update_llm_fields(issue_key="TCMR-123")

        assert success is WriteOutcome.WRITTEN
        conn.execute.assert_not_called()

    def test_update_llm_fields_no_match(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test update_llm_fields returns BASE_NOT_FOUND when no row matches."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.rowcount = 0
        conn.execute.return_value = result

        success = dao.update_llm_fields(
            issue_key="NONEXISTENT-999",
            issue_summary="Summary",
            summary_hash="hash",
        )

        assert success is WriteOutcome.BASE_NOT_FOUND

    def test_update_llm_fields_database_error(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test update_llm_fields returns ERROR on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Update error")

        success = dao.update_llm_fields(
            issue_key="TCMR-123",
            issue_summary="Summary",
            summary_hash="hash",
        )

        assert success is WriteOutcome.ERROR


class TestGetIssuesByType:
    """Tests for get_issues_by_type method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> JiraIssuesDAO:
        """Create DAO instance with mock engine."""
        return JiraIssuesDAO(mock_engine)

    def test_get_issues_success(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_issues_by_type returns list when issues found."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        mock_row1 = MagicMock()
        mock_row1._mapping = {
            "id": 1,
            "issue_id": "12345",
            "issue_key": "TCMR-123",
            "ticket_type": "tcmr",
            "summary": "Issue 1",
            "status_name": "Open",
            "created_at": datetime(2024, 1, 15, 10, 30, 0),
        }
        mock_row2 = MagicMock()
        mock_row2._mapping = {
            "id": 2,
            "issue_id": "12346",
            "issue_key": "TCMR-124",
            "ticket_type": "tcmr",
            "summary": "Issue 2",
            "status_name": "In Progress",
            "created_at": datetime(2024, 1, 16, 10, 30, 0),
        }

        result = MagicMock()
        result.fetchall.return_value = [mock_row1, mock_row2]
        conn.execute.return_value = result

        issues = dao.get_issues_by_type("tcmr")

        assert issues is not None
        assert len(issues) == 2
        assert issues[0].issue_key == "TCMR-123"
        assert issues[1].issue_key == "TCMR-124"

    def test_get_issues_with_limit(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_issues_by_type respects limit parameter."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        mock_row = MagicMock()
        mock_row._mapping = {
            "id": 1,
            "issue_id": "12345",
            "issue_key": "TCMR-123",
            "ticket_type": "tcmr",
            "summary": "Issue 1",
            "status_name": "Open",
            "created_at": datetime(2024, 1, 15, 10, 30, 0),
        }

        result = MagicMock()
        result.fetchall.return_value = [mock_row]
        conn.execute.return_value = result

        issues = dao.get_issues_by_type("tcmr", limit=1)

        assert issues is not None
        assert len(issues) == 1

    def test_get_issues_empty(self, dao: JiraIssuesDAO, mock_engine: MagicMock) -> None:
        """Test get_issues_by_type returns empty list when no issues found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchall.return_value = []
        conn.execute.return_value = result

        issues = dao.get_issues_by_type("nonexistent")

        assert issues is not None
        assert len(issues) == 0

    def test_get_issues_database_error(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_issues_by_type returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database connection error")

        issues = dao.get_issues_by_type("tcmr")

        assert issues is None


class TestInsertNewIssue:
    """Tests for insert_new_issue method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> JiraIssuesDAO:
        """Create DAO instance with mock engine."""
        return JiraIssuesDAO(mock_engine)

    def test_insert_success(self, dao: JiraIssuesDAO, mock_engine: MagicMock) -> None:
        """Test successful insert returns new ID."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.lastrowid = 123
        conn.execute.return_value = result

        issue = JiraIssueRecord(
            issue_id="12345",
            issue_key="TCMR-123",
            ticket_type="tcmr",
            summary="Test issue",
            status_name="Open",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
        )
        issue_id = dao.insert_new_issue(issue)

        assert issue_id == 123
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_insert_database_error(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test insert returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Duplicate key error")

        issue = JiraIssueRecord(
            issue_id="12345",
            issue_key="TCMR-123",
            ticket_type="tcmr",
            summary="Test issue",
            status_name="Open",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
        )
        issue_id = dao.insert_new_issue(issue)

        assert issue_id is None


class TestUpdateIssue:
    """Tests for update_issue method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> JiraIssuesDAO:
        """Create DAO instance with mock engine."""
        return JiraIssuesDAO(mock_engine)

    def test_update_success(self, dao: JiraIssuesDAO, mock_engine: MagicMock) -> None:
        """Test successful update returns issue ID."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        conn.execute.return_value = result

        issue = JiraIssueRecord(
            id=1,
            issue_id="12345",
            issue_key="TCMR-123",
            ticket_type="tcmr",
            summary="Updated summary",
            status_name="Closed",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
        )
        issue_id = dao.update_issue(issue)

        assert issue_id == 1
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_update_database_error(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test update returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Update error")

        issue = JiraIssueRecord(
            id=1,
            issue_id="12345",
            issue_key="TCMR-123",
            ticket_type="tcmr",
            summary="Updated summary",
            status_name="Closed",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
        )
        issue_id = dao.update_issue(issue)

        assert issue_id is None


class TestInsertOrUpdateIssue:
    """Tests for insert_or_update_issue method (upsert)."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> JiraIssuesDAO:
        """Create DAO instance with mock engine."""
        return JiraIssuesDAO(mock_engine)

    def test_insert_when_not_exists(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test upsert inserts when issue doesn't exist."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        # First call: find returns None (not found)
        find_result = MagicMock()
        find_result.fetchone.return_value = None

        # Second call: insert returns new ID
        insert_result = MagicMock()
        insert_result.lastrowid = 123

        conn.execute.side_effect = [find_result, insert_result]

        issue = JiraIssueRecord(
            issue_id="12345",
            issue_key="TCMR-123",
            ticket_type="tcmr",
            summary="New issue",
            status_name="Open",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
        )
        issue_id = dao.insert_or_update_issue(issue)

        assert issue_id == 123
        assert conn.execute.call_count == 2

    def test_update_when_exists(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test upsert updates when issue exists."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        # First call: find returns existing issue
        mock_row = MagicMock()
        mock_row._mapping = {
            "id": 1,
            "issue_id": "12345",
            "issue_key": "TCMR-123",
            "ticket_type": "tcmr",
            "summary": "Existing issue",
            "status_name": "Open",
            "created_at": datetime(2024, 1, 15, 10, 30, 0),
        }
        find_result = MagicMock()
        find_result.fetchone.return_value = mock_row

        # Second call: update succeeds
        update_result = MagicMock()

        conn.execute.side_effect = [find_result, update_result]

        issue = JiraIssueRecord(
            issue_id="12345",
            issue_key="TCMR-123",
            ticket_type="tcmr",
            summary="Updated issue",
            status_name="Closed",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
        )
        issue_id = dao.insert_or_update_issue(issue)

        assert issue_id == 1
        assert issue.id == 1  # ID should be updated from existing
        assert conn.execute.call_count == 2


class TestUpsertJiraIssuesBatch:
    """Tests for upsert_jira_issues_batch method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> JiraIssuesDAO:
        """Create DAO instance with mock engine."""
        return JiraIssuesDAO(mock_engine)

    def test_batch_empty_list(self, dao: JiraIssuesDAO, mock_engine: MagicMock) -> None:
        """Test batch upsert with empty list returns 0."""
        result = dao.upsert_jira_issues_batch([])

        assert result == 0

    def test_batch_single_chunk(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test batch upsert with less than BATCH_SIZE issues."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.rowcount = 5
        conn.execute.return_value = result

        issues = [
            JiraIssueRecord(
                issue_id=f"{i}",
                issue_key=f"TCMR-{i}",
                ticket_type="tcmr",
                summary=f"Issue {i}",
                status_name="Open",
                created_at=datetime(2024, 1, 15, 10, 30, 0),
            )
            for i in range(5)
        ]
        affected = dao.upsert_jira_issues_batch(issues)

        assert affected == 5
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_batch_multiple_chunks(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test batch upsert with more than BATCH_SIZE issues."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result1 = MagicMock()
        result1.rowcount = 500
        result2 = MagicMock()
        result2.rowcount = 100
        conn.execute.side_effect = [result1, result2]

        issues = [
            JiraIssueRecord(
                issue_id=f"{i}",
                issue_key=f"TCMR-{i}",
                ticket_type="tcmr",
                summary=f"Issue {i}",
                status_name="Open",
                created_at=datetime(2024, 1, 15, 10, 30, 0),
            )
            for i in range(600)
        ]
        affected = dao.upsert_jira_issues_batch(issues)

        assert affected == 600
        assert conn.execute.call_count == 2
        assert conn.commit.call_count == 2

    def test_batch_error_in_chunk(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test batch upsert returns None when a chunk fails."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        issues = [
            JiraIssueRecord(
                issue_id=f"{i}",
                issue_key=f"TCMR-{i}",
                ticket_type="tcmr",
                summary=f"Issue {i}",
                status_name="Open",
                created_at=datetime(2024, 1, 15, 10, 30, 0),
            )
            for i in range(5)
        ]
        affected = dao.upsert_jira_issues_batch(issues)

        assert affected is None


class TestUpsertBatch:
    """Tests for _upsert_batch method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> JiraIssuesDAO:
        """Create DAO instance with mock engine."""
        return JiraIssuesDAO(mock_engine)

    def test_upsert_batch_update_services_true_includes_services_in_update(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """When update_services=True (default), services is included in the UPDATE clause."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.rowcount = 1
        conn.execute.return_value = result

        issue = JiraIssueRecord(
            issue_id="123",
            issue_key="TCMR-123",
            ticket_type="tcmr",
            summary="Test issue",
            status_name="Open",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            services=["service-a"],
        )
        dao._upsert_batch([issue], update_services=True)

        sql_text = str(conn.execute.call_args[0][0])
        # jira has a base_entered_at_column staleness guard (ADR 024), so the
        # update value is wrapped in a CASE WHEN fresher-or-first-write guard
        # rather than a bare `services = VALUES(services)` mapping.
        assert "services = CASE WHEN" in sql_text
        assert "THEN VALUES(services) ELSE jira_issues.services END" in sql_text

    def test_upsert_batch_update_services_false_excludes_services_from_update(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Regression: when update_services=False, services must be excluded from the
        ON DUPLICATE KEY UPDATE clause so existing values are not overwritten.

        This is used when backstage_mapping is None and enrichment was skipped entirely.
        """
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.rowcount = 1
        conn.execute.return_value = result

        issue = JiraIssueRecord(
            issue_id="123",
            issue_key="TCMR-123",
            ticket_type="tcmr",
            summary="Test issue",
            status_name="Open",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            services=None,  # enrichment was skipped
        )
        dao._upsert_batch([issue], update_services=False)

        sql_text = str(conn.execute.call_args[0][0])
        assert "services = VALUES(services)" not in sql_text

    def test_upsert_batch_success(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test _upsert_batch returns rowcount on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.rowcount = 3
        conn.execute.return_value = result

        issues = [
            JiraIssueRecord(
                issue_id=f"{i}",
                issue_key=f"TCMR-{i}",
                ticket_type="tcmr",
                summary=f"Issue {i}",
                status_name="Open",
                created_at=datetime(2024, 1, 15, 10, 30, 0),
            )
            for i in range(3)
        ]
        affected = dao._upsert_batch(issues)

        assert affected == 3

    def test_upsert_batch_empty(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test _upsert_batch returns 0 for empty list."""
        affected = dao._upsert_batch([])

        assert affected == 0

    def test_upsert_batch_database_error(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test _upsert_batch returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        issues = [
            JiraIssueRecord(
                issue_id="12345",
                issue_key="TCMR-123",
                ticket_type="tcmr",
                summary="Test issue",
                status_name="Open",
                created_at=datetime(2024, 1, 15, 10, 30, 0),
            )
        ]
        affected = dao._upsert_batch(issues)

        assert affected is None


class TestJiraHashInfo:
    """Tests for JiraHashInfo dataclass."""

    def test_create_with_all_fields(self) -> None:
        """Test creating JiraHashInfo with all fields populated."""
        hash_info = JiraHashInfo(
            issue_key="TCMR-123",
            summary_hash="a" * 64,
            comments_hash="b" * 64,
            issue_summary="LLM generated summary",
            issue_comments_summary="LLM generated comments summary",
        )
        assert hash_info.issue_key == "TCMR-123"
        assert hash_info.summary_hash == "a" * 64
        assert hash_info.comments_hash == "b" * 64
        assert hash_info.issue_summary == "LLM generated summary"
        assert hash_info.issue_comments_summary == "LLM generated comments summary"

    def test_create_with_none_values(self) -> None:
        """Test creating JiraHashInfo with None values."""
        hash_info = JiraHashInfo(
            issue_key="TCMR-123",
            summary_hash=None,
            comments_hash=None,
            issue_summary=None,
            issue_comments_summary=None,
        )
        assert hash_info.issue_key == "TCMR-123"
        assert hash_info.summary_hash is None
        assert hash_info.comments_hash is None
        assert hash_info.issue_summary is None
        assert hash_info.issue_comments_summary is None


class TestGetIssueHashesByKeys:
    """Tests for get_issue_hashes_by_keys method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> JiraIssuesDAO:
        """Create DAO instance with mock engine."""
        return JiraIssuesDAO(mock_engine)

    def test_get_hashes_empty_list(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_issue_hashes_by_keys returns empty dict for empty input."""
        result = dao.get_issue_hashes_by_keys([])

        assert result == {}
        mock_engine.connect.assert_not_called()

    def test_get_hashes_success(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_issue_hashes_by_keys returns hash map on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        mock_row1 = MagicMock()
        mock_row1.issue_key = "TCMR-123"
        mock_row1.summary_hash = "abc123" + "0" * 58
        mock_row1.comments_hash = "def456" + "0" * 58
        mock_row1.issue_summary = "Summary for 123"
        mock_row1.issue_comments_summary = "Comments summary for 123"

        mock_row2 = MagicMock()
        mock_row2.issue_key = "TCMR-124"
        mock_row2.summary_hash = "ghi789" + "0" * 58
        mock_row2.comments_hash = None
        mock_row2.issue_summary = "Summary for 124"
        mock_row2.issue_comments_summary = None

        result = MagicMock()
        result.fetchall.return_value = [mock_row1, mock_row2]
        conn.execute.return_value = result

        hash_map = dao.get_issue_hashes_by_keys(["TCMR-123", "TCMR-124"])

        assert hash_map is not None
        assert len(hash_map) == 2
        assert "TCMR-123" in hash_map
        assert "TCMR-124" in hash_map
        assert hash_map["TCMR-123"].summary_hash == "abc123" + "0" * 58
        assert hash_map["TCMR-123"].issue_summary == "Summary for 123"
        assert hash_map["TCMR-124"].comments_hash is None

    def test_get_hashes_partial_results(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_issue_hashes_by_keys when some keys are not in DB."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        # Only one of the two keys exists
        mock_row = MagicMock()
        mock_row.issue_key = "TCMR-123"
        mock_row.summary_hash = "abc123" + "0" * 58
        mock_row.comments_hash = "def456" + "0" * 58
        mock_row.issue_summary = "Summary"
        mock_row.issue_comments_summary = "Comments"

        result = MagicMock()
        result.fetchall.return_value = [mock_row]
        conn.execute.return_value = result

        hash_map = dao.get_issue_hashes_by_keys(["TCMR-123", "TCMR-NONEXISTENT"])

        assert hash_map is not None
        assert len(hash_map) == 1
        assert "TCMR-123" in hash_map
        assert "TCMR-NONEXISTENT" not in hash_map

    def test_get_hashes_database_error(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_issue_hashes_by_keys returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database connection error")

        result = dao.get_issue_hashes_by_keys(["TCMR-123"])

        assert result is None

    def test_get_hashes_database_error_with_metrics(
        self, mock_engine: MagicMock
    ) -> None:
        """Test get_issue_hashes_by_keys records metrics on error."""
        mock_metrics = MagicMock(spec=DBMetrics)
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = JiraIssuesDAO(mock_engine, metrics=mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Database error")
        conn.execute.side_effect = error

        result = dao.get_issue_hashes_by_keys(["TCMR-123"])

        assert result is None
        record_fn.assert_called_once_with(error)

    def test_get_hashes_batching(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_issue_hashes_by_keys processes in batches."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        # Create mock results for two batches
        batch1_results = [MagicMock() for _ in range(500)]
        for i, row in enumerate(batch1_results):
            row.issue_key = f"TCMR-{i}"
            row.summary_hash = f"hash{i}"
            row.comments_hash = None
            row.issue_summary = f"Summary {i}"
            row.issue_comments_summary = None

        batch2_results = [MagicMock() for _ in range(100)]
        for i, row in enumerate(batch2_results):
            row.issue_key = f"TCMR-{500 + i}"
            row.summary_hash = f"hash{500 + i}"
            row.comments_hash = None
            row.issue_summary = f"Summary {500 + i}"
            row.issue_comments_summary = None

        result1 = MagicMock()
        result1.fetchall.return_value = batch1_results
        result2 = MagicMock()
        result2.fetchall.return_value = batch2_results

        conn.execute.side_effect = [result1, result2]

        # Request 600 keys (exceeds BATCH_SIZE of 500)
        keys = [f"TCMR-{i}" for i in range(600)]
        hash_map = dao.get_issue_hashes_by_keys(keys)

        assert hash_map is not None
        assert len(hash_map) == 600
        assert conn.execute.call_count == 2  # Two batches


class TestFindIssuesByTimeRange:
    """Tests for find_issues_by_time_range method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> JiraIssuesDAO:
        """Create DAO instance with mock engine."""
        return JiraIssuesDAO(mock_engine)

    def test_find_issues_success(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test find_issues_by_time_range returns issues within range."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_row = MagicMock()
        mock_row._mapping = {
            "id": 1,
            "issue_id": "12345",
            "issue_key": "TCMR-123",
            "ticket_type": "tcmr",
            "summary": "Test issue",
            "status_name": "Open",
            "created_at": datetime(2024, 1, 15, 10, 30, 0),
        }
        result = MagicMock()
        result.fetchall.return_value = [mock_row]
        conn.execute.return_value = result

        issues = dao.find_issues_by_time_range(
            start_time=datetime(2024, 1, 15, 0, 0, 0),
            end_time=datetime(2024, 1, 16, 23, 59, 59),
        )

        assert issues is not None
        assert len(issues) == 1
        assert issues[0].issue_key == "TCMR-123"

    def test_find_issues_empty(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test find_issues_by_time_range returns empty list when none found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchall.return_value = []
        conn.execute.return_value = result

        issues = dao.find_issues_by_time_range(
            start_time=datetime(2024, 1, 15, 0, 0, 0),
            end_time=datetime(2024, 1, 16, 23, 59, 59),
        )

        assert issues is not None
        assert len(issues) == 0

    def test_find_issues_with_services_filter(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test find_issues_by_time_range with services filter."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchall.return_value = []
        conn.execute.return_value = result

        issues = dao.find_issues_by_time_range(
            start_time=datetime(2024, 1, 15, 0, 0, 0),
            end_time=datetime(2024, 1, 16, 23, 59, 59),
            services=["service-checkout"],
        )

        assert issues is not None
        conn.execute.assert_called_once()

    def test_find_issues_invalid_time_field(self, dao: JiraIssuesDAO) -> None:
        """Test find_issues_by_time_range raises ValueError for invalid time_field."""
        with pytest.raises(ValueError, match="Invalid time_field"):
            dao.find_issues_by_time_range(
                start_time=datetime(2024, 1, 15, 0, 0, 0),
                end_time=datetime(2024, 1, 16, 23, 59, 59),
                time_field="updated_at",
            )

    def test_find_issues_database_error(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Test find_issues_by_time_range returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        issues = dao.find_issues_by_time_range(
            start_time=datetime(2024, 1, 15, 0, 0, 0),
            end_time=datetime(2024, 1, 16, 23, 59, 59),
        )

        assert issues is None

    def test_find_issues_metrics_on_success(self, mock_engine: MagicMock) -> None:
        """Test find_issues_by_time_range records metrics on success."""
        mock_metrics = MagicMock(spec=DBMetrics)
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = JiraIssuesDAO(mock_engine, metrics=mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchall.return_value = []
        conn.execute.return_value = result

        dao.find_issues_by_time_range(
            start_time=datetime(2024, 1, 15, 0, 0, 0),
            end_time=datetime(2024, 1, 16, 23, 59, 59),
        )

        mock_metrics.start_query.assert_called_once_with("select", "jira_issues")
        record_fn.assert_called_once_with(None)

    def test_find_issues_metrics_on_error(self, mock_engine: MagicMock) -> None:
        """Test find_issues_by_time_range records metrics on error."""
        mock_metrics = MagicMock(spec=DBMetrics)
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = JiraIssuesDAO(mock_engine, metrics=mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Database error")
        conn.execute.side_effect = error

        dao.find_issues_by_time_range(
            start_time=datetime(2024, 1, 15, 0, 0, 0),
            end_time=datetime(2024, 1, 16, 23, 59, 59),
        )

        record_fn.assert_called_once_with(error)


class TestJiraIssuesDAOMetrics:
    """Tests for JiraIssuesDAO metrics instrumentation."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def mock_metrics(self) -> MagicMock:
        """Mock DBMetrics."""
        metrics = MagicMock(spec=DBMetrics)
        metrics.start_query.return_value = MagicMock()
        return metrics

    @pytest.fixture
    def dao_with_metrics(
        self, mock_engine: MagicMock, mock_metrics: MagicMock
    ) -> JiraIssuesDAO:
        """Create DAO instance with mock engine and metrics."""
        return JiraIssuesDAO(mock_engine, metrics=mock_metrics)

    def test_find_issue_by_key_records_metrics_on_success(
        self,
        dao_with_metrics: JiraIssuesDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test find_issue_by_key records metrics on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_row = MagicMock()
        mock_row._mapping = {
            "id": 1,
            "issue_id": "12345",
            "issue_key": "TCMR-123",
            "ticket_type": "tcmr",
            "summary": "Test",
            "status_name": "Open",
            "created_at": datetime(2024, 1, 15, 10, 30, 0),
        }
        result = MagicMock()
        result.fetchone.return_value = mock_row
        conn.execute.return_value = result

        dao_with_metrics.find_issue_by_key("TCMR-123")

        mock_metrics.start_query.assert_called_once_with("select", "jira_issues")
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_find_issue_by_key_records_metrics_on_error(
        self,
        dao_with_metrics: JiraIssuesDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test find_issue_by_key records metrics on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        db_error = Exception("Database error")
        conn.execute.side_effect = db_error

        dao_with_metrics.find_issue_by_key("TCMR-123")

        mock_metrics.start_query.assert_called_once_with("select", "jira_issues")
        mock_metrics.start_query.return_value.assert_called_once_with(db_error)

    def test_get_issues_by_type_records_metrics_on_success(
        self,
        dao_with_metrics: JiraIssuesDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test get_issues_by_type records metrics on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchall.return_value = []
        conn.execute.return_value = result

        dao_with_metrics.get_issues_by_type("tcmr")

        mock_metrics.start_query.assert_called_once_with("select", "jira_issues")
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_get_issues_by_type_records_metrics_on_error(
        self,
        dao_with_metrics: JiraIssuesDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test get_issues_by_type records metrics on error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Database error")
        conn.execute.side_effect = error

        dao_with_metrics.get_issues_by_type("tcmr")

        mock_metrics.start_query.assert_called_once_with("select", "jira_issues")
        mock_metrics.start_query.return_value.assert_called_once_with(error)

    def test_insert_new_issue_records_metrics_on_success(
        self,
        dao_with_metrics: JiraIssuesDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test insert_new_issue records metrics on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.lastrowid = 123
        conn.execute.return_value = result

        issue = JiraIssueRecord(
            issue_id="12345",
            issue_key="TCMR-123",
            ticket_type="tcmr",
            summary="Test",
            status_name="Open",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
        )
        dao_with_metrics.insert_new_issue(issue)

        mock_metrics.start_query.assert_called_once_with("insert", "jira_issues")
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_insert_new_issue_records_metrics_on_error(
        self,
        dao_with_metrics: JiraIssuesDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test insert_new_issue records metrics on error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Insert error")
        conn.execute.side_effect = error

        issue = JiraIssueRecord(
            issue_id="12345",
            issue_key="TCMR-123",
            ticket_type="tcmr",
            summary="Test",
            status_name="Open",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
        )
        dao_with_metrics.insert_new_issue(issue)

        mock_metrics.start_query.assert_called_once_with("insert", "jira_issues")
        mock_metrics.start_query.return_value.assert_called_once_with(error)

    def test_update_issue_records_metrics_on_success(
        self,
        dao_with_metrics: JiraIssuesDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test update_issue records metrics on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value = MagicMock()

        issue = JiraIssueRecord(
            id=1,
            issue_id="12345",
            issue_key="TCMR-123",
            ticket_type="tcmr",
            summary="Updated",
            status_name="Closed",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
        )
        dao_with_metrics.update_issue(issue)

        mock_metrics.start_query.assert_called_once_with("update", "jira_issues")
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_update_issue_records_metrics_on_error(
        self,
        dao_with_metrics: JiraIssuesDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test update_issue records metrics on error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Update error")
        conn.execute.side_effect = error

        issue = JiraIssueRecord(
            id=1,
            issue_id="12345",
            issue_key="TCMR-123",
            ticket_type="tcmr",
            summary="Updated",
            status_name="Closed",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
        )
        dao_with_metrics.update_issue(issue)

        mock_metrics.start_query.assert_called_once_with("update", "jira_issues")
        mock_metrics.start_query.return_value.assert_called_once_with(error)

    def test_upsert_batch_records_metrics_on_success(
        self,
        dao_with_metrics: JiraIssuesDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test _upsert_batch records metrics on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.rowcount = 3
        conn.execute.return_value = result

        issues = [
            JiraIssueRecord(
                issue_id=f"{i}",
                issue_key=f"TCMR-{i}",
                ticket_type="tcmr",
                summary=f"Issue {i}",
                status_name="Open",
                created_at=datetime(2024, 1, 15, 10, 30, 0),
            )
            for i in range(3)
        ]
        dao_with_metrics._upsert_batch(issues)

        mock_metrics.start_query.assert_called_once_with("upsert", "jira_issues")
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_upsert_batch_records_metrics_on_error(
        self,
        dao_with_metrics: JiraIssuesDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test _upsert_batch records metrics on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        db_error = Exception("Database error")
        conn.execute.side_effect = db_error

        issues = [
            JiraIssueRecord(
                issue_id="12345",
                issue_key="TCMR-123",
                ticket_type="tcmr",
                summary="Test",
                status_name="Open",
                created_at=datetime(2024, 1, 15, 10, 30, 0),
            )
        ]
        dao_with_metrics._upsert_batch(issues)

        mock_metrics.start_query.assert_called_once_with("upsert", "jira_issues")
        mock_metrics.start_query.return_value.assert_called_once_with(db_error)

    def test_get_issue_hashes_records_metrics_per_batch(
        self,
        dao_with_metrics: JiraIssuesDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test get_issue_hashes_by_keys records metrics for each batch."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchall.return_value = []
        conn.execute.return_value = result

        dao_with_metrics.get_issue_hashes_by_keys(["TCMR-123", "TCMR-124"])

        mock_metrics.start_query.assert_called_once_with("select", "jira_issues")
        mock_metrics.start_query.return_value.assert_called_once_with(None)


class TestUpdateLLMFields:
    """Tests for JiraIssuesDAO.update_llm_fields."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> JiraIssuesDAO:
        return JiraIssuesDAO(mock_engine)

    def test_success_returns_true(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Successful update returns True."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.rowcount = 1

        result = dao.update_llm_fields(
            issue_key="OPS-123",
            issue_summary="Auth service returning 401s",
            issue_comments_summary="Root cause found",
            summary_hash="abc123",
            comments_hash="def456",
        )
        assert result is WriteOutcome.WRITTEN

    def test_null_llm_fields_returns_true(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Passing None for all LLM fields still succeeds."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.rowcount = 1

        result = dao.update_llm_fields(
            issue_key="OPS-123",
            issue_summary=None,
            issue_comments_summary=None,
            summary_hash=None,
            comments_hash=None,
        )
        assert result is WriteOutcome.WRITTEN

    def test_zero_rowcount_returns_false(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """Returns False when 0 rows updated — base record not yet present."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.rowcount = 0

        result = dao.update_llm_fields(
            issue_key="OPS-123",
            issue_summary="summary",
            issue_comments_summary="comments",
            summary_hash="abc",
            comments_hash="def",
        )
        assert result is WriteOutcome.BASE_NOT_FOUND

    def test_db_error_returns_none(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """DB exception returns ERROR without raising."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB connection lost")

        result = dao.update_llm_fields(
            issue_key="OPS-123",
            issue_summary="summary",
            issue_comments_summary="comments",
            summary_hash="abc",
            comments_hash="def",
        )
        assert result is WriteOutcome.ERROR

    def test_executes_update_on_correct_issue_key(
        self, dao: JiraIssuesDAO, mock_engine: MagicMock
    ) -> None:
        """execute and commit are called on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        dao.update_llm_fields(
            issue_key="OPS-123",
            issue_summary="summary",
            issue_comments_summary="comments",
            summary_hash="abc",
            comments_hash="def",
        )

        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_records_metrics_on_success(self, mock_engine: MagicMock) -> None:
        """DBMetrics.start_query is called and record fn called with None on success."""
        mock_metrics = MagicMock(spec=DBMetrics)
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = JiraIssuesDAO(mock_engine, mock_metrics)

        dao.update_llm_fields(
            issue_key="OPS-123",
            issue_summary="summary",
            issue_comments_summary="comments",
            summary_hash="abc",
            comments_hash="def",
        )

        mock_metrics.start_query.assert_called_once_with("update", "jira_issues")
        record_fn.assert_called_once_with(None)

    def test_records_metrics_on_error(self, mock_engine: MagicMock) -> None:
        """DBMetrics record fn is called with the exception on DB error."""
        mock_metrics = MagicMock(spec=DBMetrics)
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = JiraIssuesDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        db_error = Exception("DB error")
        conn.execute.side_effect = db_error

        dao.update_llm_fields(
            issue_key="OPS-123",
            issue_summary="summary",
            issue_comments_summary="comments",
            summary_hash="abc",
            comments_hash="def",
        )

        mock_metrics.start_query.assert_called_once_with("update", "jira_issues")
        record_fn.assert_called_once_with(db_error)
