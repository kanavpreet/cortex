"""Tests for GHE PR DAO."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs

from common.daos.base_dao import WriteOutcome
from common.daos.ghe_pr_dao import GHEPRDAO, GHEPullRequestWithRepo, PRHashInfo
from common.models.ghe_pr import GHEPullRequest


def _make_pr(
    pull_request_id: int,
    *,
    merged: bool = False,
    state: str = "open",
    locked: bool = False,
    created_at: datetime | None = None,
    org_id: int = 1,
    org_login: str = "Airbnb-ITX",
    repo_id: int = 100,
    repo_name: str = "my-repo",
    target_branch_name: str = "main",
) -> GHEPullRequest:
    """Build a GHEPullRequest with all required (non-null) fields set."""
    return GHEPullRequest(
        pull_request_id=pull_request_id,
        pull_request_number=pull_request_id,
        org_id=org_id,
        org_login=org_login,
        repo_id=repo_id,
        repo_name=repo_name,
        merged=merged,
        state=state,
        locked=locked,
        created_at=created_at or datetime(2024, 1, 15, 10, 30, 0),
        target_branch_name=target_branch_name,
    )


def _make_pr_mapping(
    pull_request_id: int,
    *,
    org_id: int = 1,
    org_login: str = "Airbnb-ITX",
    repo_id: int = 100,
    repo_name: str = "my-repo",
    merged: bool = True,
    state: str = "closed",
    locked: bool = False,
    closed_at: datetime | None = datetime(2024, 1, 16, 12, 0, 0),
    merged_at: datetime | None = datetime(2024, 1, 16, 12, 0, 0),
    pull_request_summary: str | None = None,
    jira_tcmr_key: str | None = None,
    environment: str | None = None,
    description_hash: str | None = None,
    services: list[str] | None = None,
) -> dict[str, object]:
    """Build a row._mapping dict containing all REQUIRED GHEPullRequest fields.

    Intentionally excludes the removed `id` and `repository_id` columns.
    """
    return {
        "pull_request_id": pull_request_id,
        "pull_request_number": pull_request_id,
        "org_id": org_id,
        "org_login": org_login,
        "repo_id": repo_id,
        "repo_name": repo_name,
        "merged": merged,
        "state": state,
        "locked": locked,
        "created_at": datetime(2024, 1, 15, 10, 30, 0),
        "closed_at": closed_at,
        "merged_at": merged_at,
        "target_branch_name": "main",
        "pull_request_summary": pull_request_summary,
        "jira_tcmr_key": jira_tcmr_key,
        "environment": environment,
        "deleted_at": None,
        "description_hash": description_hash,
        "last_updated_at": None,
        "services": services,
    }


class TestGHEPRDAO:
    """Test suite for GHEPRDAO class."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRDAO:
        """Create DAO instance with mock engine."""
        return GHEPRDAO(mock_engine)

    def test_constructor(self, mock_engine: MagicMock) -> None:
        """Test DAO constructor stores engine."""
        dao = GHEPRDAO(mock_engine)
        assert dao._engine is mock_engine


class TestGetPRHashesByRepoID:
    """Tests for get_pr_hashes_by_repo_id method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRDAO:
        """Create DAO instance with mock engine."""
        return GHEPRDAO(mock_engine)

    def test_get_pr_hashes_success(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test get_pr_hashes returns hash map when PRs found."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        # Create mock rows
        mock_row1 = MagicMock()
        mock_row1.pull_request_id = 101
        mock_row1.repo_id = 100
        mock_row1.description_hash = "abc123"
        mock_row1.pull_request_summary = "Summary 1"
        mock_row1.services = ["service-a"]

        mock_row2 = MagicMock()
        mock_row2.pull_request_id = 102
        mock_row2.repo_id = 100
        mock_row2.description_hash = "def456"
        mock_row2.pull_request_summary = "Summary 2"
        mock_row2.services = None

        result = MagicMock()
        result.fetchall.return_value = [mock_row1, mock_row2]
        conn.execute.return_value = result

        hash_map = dao.get_pr_hashes_by_repo_id(org_id=1, repo_id=100)

        assert hash_map is not None
        assert len(hash_map) == 2
        assert "101" in hash_map
        assert "102" in hash_map
        assert hash_map["101"].description_hash == "abc123"
        assert hash_map["101"].repository_id == 100
        assert hash_map["101"].services == ["service-a"]
        assert hash_map["102"].pull_request_summary == "Summary 2"
        assert hash_map["102"].services is None

    def test_get_pr_hashes_empty(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test get_pr_hashes returns empty dict when no PRs found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchall.return_value = []
        conn.execute.return_value = result

        hash_map = dao.get_pr_hashes_by_repo_id(org_id=1, repo_id=999)

        assert hash_map is not None
        assert len(hash_map) == 0

    def test_get_pr_hashes_database_error(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_pr_hashes returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database connection error")

        hash_map = dao.get_pr_hashes_by_repo_id(org_id=1, repo_id=100)

        assert hash_map is None


class TestFindPRsByPullRequestIds:
    """Tests for find_prs_by_pull_request_ids method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRDAO:
        return GHEPRDAO(mock_engine)

    def _make_mock_row(self, pull_request_id: int) -> MagicMock:
        mock_row = MagicMock()
        mock_row._mapping = _make_pr_mapping(pull_request_id)
        return mock_row

    def test_returns_matching_prs(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test returns list of PRs for matching pull_request_ids."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [
            self._make_mock_row(101),
            self._make_mock_row(102),
        ]

        result = dao.find_prs_by_pull_request_ids([101, 102])

        assert len(result) == 2
        assert result[0].pull_request_id == 101
        assert result[1].pull_request_id == 102

    def test_returns_empty_list_for_empty_input(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Test returns empty list without hitting the database for empty input."""
        result = dao.find_prs_by_pull_request_ids([])

        assert result == []
        mock_engine.connect.assert_not_called()

    def test_returns_empty_list_when_none_found(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Test returns empty list when no PRs match."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []

        result = dao.find_prs_by_pull_request_ids([99999])

        assert result == []

    def test_returns_empty_list_on_database_error(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Test returns empty list on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        result = dao.find_prs_by_pull_request_ids([101, 102])

        assert result == []


class TestFindGhePR:
    """Tests for find_ghe_pr method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRDAO:
        """Create DAO instance with mock engine."""
        return GHEPRDAO(mock_engine)

    def test_find_success(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test find returns PR when found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_row = MagicMock()
        mock_row._mapping = _make_pr_mapping(
            101,
            pull_request_summary="Summary",
            jira_tcmr_key="TCMR-123",
            environment="production",
            description_hash="abc123",
        )
        result = MagicMock()
        result.fetchone.return_value = mock_row
        conn.execute.return_value = result

        pr = dao.find_ghe_pr(pull_request_id=101)

        assert pr is not None
        assert pr.pull_request_id == 101
        assert pr.merged is True
        assert pr.state == "closed"
        assert pr.jira_tcmr_key == "TCMR-123"

    def test_find_not_found(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test find returns None when PR not found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchone.return_value = None
        conn.execute.return_value = result

        pr = dao.find_ghe_pr(pull_request_id=999)

        assert pr is None

    def test_find_database_error(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test find returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database connection error")

        with capture_logs() as cap_logs:
            pr = dao.find_ghe_pr(pull_request_id=101)

        assert pr is None
        error_logs = [log for log in cap_logs if log.get("event") == "error finding PR"]
        assert len(error_logs) == 1
        assert error_logs[0]["exc_info"] is True


class TestInsertNewPR:
    """Tests for insert_new_pr method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRDAO:
        """Create DAO instance with mock engine."""
        return GHEPRDAO(mock_engine)

    def test_insert_success(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test successful insert returns the pull_request_id."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        conn.execute.return_value = result

        pr = _make_pr(101)
        pr_id = dao.insert_new_pr(pr)

        assert pr_id == 101
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_insert_database_error(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test insert returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Duplicate key error")

        pr = _make_pr(101)
        pr_id = dao.insert_new_pr(pr)

        assert pr_id is None


class TestUpdatePR:
    """Tests for update_pr method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRDAO:
        """Create DAO instance with mock engine."""
        return GHEPRDAO(mock_engine)

    def test_update_success(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test successful update returns PR ID."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        conn.execute.return_value = result

        pr = _make_pr(101, merged=True, state="closed")
        pr_id = dao.update_pr(pr)

        assert pr_id == 101
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_update_database_error(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test update returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Update error")

        pr = _make_pr(101, merged=True, state="closed")
        pr_id = dao.update_pr(pr)

        assert pr_id is None


class TestDeleteGHEPR:
    """Tests for delete_ghe_pr method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRDAO:
        """Create DAO instance with mock engine."""
        return GHEPRDAO(mock_engine)

    def test_delete_success(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test successful soft delete returns True."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        conn.execute.return_value = result

        success = dao.delete_ghe_pr(pull_request_id=101)

        assert success is True
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_delete_database_error(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test delete returns False on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Delete error")

        success = dao.delete_ghe_pr(pull_request_id=101)

        assert success is False


class TestInsertOrUpdateGHEPR:
    """Tests for insert_or_update_ghe_pr method (upsert)."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRDAO:
        """Create DAO instance with mock engine."""
        return GHEPRDAO(mock_engine)

    def test_insert_when_not_exists(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Test upsert inserts when PR doesn't exist."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        # First call: find returns None (not found)
        find_result = MagicMock()
        find_result.fetchone.return_value = None

        # Second call: insert succeeds
        insert_result = MagicMock()

        conn.execute.side_effect = [find_result, insert_result]

        pr = _make_pr(101)
        pr_id = dao.insert_or_update_ghe_pr(pr)

        assert pr_id == 101
        assert conn.execute.call_count == 2

    def test_update_when_exists(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test upsert updates when PR exists."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        # First call: find returns existing PR
        mock_row = MagicMock()
        mock_row._mapping = _make_pr_mapping(
            101,
            merged=False,
            state="open",
            closed_at=None,
            merged_at=None,
        )
        find_result = MagicMock()
        find_result.fetchone.return_value = mock_row

        # Second call: update succeeds
        update_result = MagicMock()

        conn.execute.side_effect = [find_result, update_result]

        pr = _make_pr(101, merged=True, state="closed")
        pr_id = dao.insert_or_update_ghe_pr(pr)

        assert pr_id == 101
        assert conn.execute.call_count == 2


class TestUpsertGHEPRsBatch:
    """Tests for upsert_ghe_prs_batch method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRDAO:
        """Create DAO instance with mock engine."""
        return GHEPRDAO(mock_engine)

    def test_batch_empty_list(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test batch upsert with empty list returns 0."""
        result = dao.upsert_ghe_prs_batch([])

        assert result == 0

    def test_batch_single_chunk(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test batch upsert with less than BATCH_SIZE PRs."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.rowcount = 5
        conn.execute.return_value = result

        prs = [_make_pr(i) for i in range(5)]
        affected = dao.upsert_ghe_prs_batch(prs)

        assert affected == 5
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_batch_update_clause_is_behavior_preserving(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Oracle: the spec-derived ON DUPLICATE KEY UPDATE clause matches the
        former hand-written ``_UPSERT_COLUMNS`` — LLM + immutable identity/creation
        columns are never overwritten, and ``deleted_at`` IS reset (revive)."""
        from sqlalchemy.dialects import mysql

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.rowcount = 1

        dao.upsert_ghe_prs_batch([_make_pr(101)])

        compiled = str(
            conn.execute.call_args[0][0].compile(
                dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        update_part = compiled.split("ON DUPLICATE KEY UPDATE", 1)[1]
        # Mutable base columns (incl. deleted_at revive) are refreshed on conflict.
        for col in ("state", "merged", "services", "deleted_at", "last_updated_at"):
            assert f"{col}=" in update_part.replace(" ", "")
        # PK + immutable identity keys + creation timestamp + LLM columns are NOT.
        for col in (
            "pull_request_id",
            "org_id",
            "repo_id",
            "created_at",
            "pull_request_summary",
            "description_hash",
        ):
            assert f"{col}=" not in update_part.replace(" ", "")

    def test_batch_multiple_chunks(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test batch upsert with more than BATCH_SIZE PRs."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result1 = MagicMock()
        result1.rowcount = 500
        result2 = MagicMock()
        result2.rowcount = 100
        conn.execute.side_effect = [result1, result2]

        prs = [_make_pr(i) for i in range(600)]
        affected = dao.upsert_ghe_prs_batch(prs)

        assert affected == 600
        assert conn.execute.call_count == 2
        assert conn.commit.call_count == 2

    def test_batch_error_in_chunk(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test batch upsert returns None when a chunk fails."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        prs = [_make_pr(i) for i in range(5)]
        affected = dao.upsert_ghe_prs_batch(prs)

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
    def dao(self, mock_engine: MagicMock) -> GHEPRDAO:
        """Create DAO instance with mock engine."""
        return GHEPRDAO(mock_engine)

    def test_upsert_batch_success(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test _upsert_batch returns rowcount on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.rowcount = 3
        conn.execute.return_value = result

        prs = [_make_pr(i) for i in range(3)]
        affected = dao._upsert_batch(prs)

        assert affected == 3

    def test_upsert_batch_empty(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test _upsert_batch returns 0 for empty list."""
        affected = dao._upsert_batch([])

        assert affected == 0

    def test_upsert_batch_database_error(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Test _upsert_batch returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        prs = [_make_pr(1)]
        affected = dao._upsert_batch(prs)

        assert affected is None


class TestPRHashInfo:
    """Tests for PRHashInfo dataclass."""

    def test_pr_hash_info_creation(self) -> None:
        """Test PRHashInfo dataclass can be created."""
        info = PRHashInfo(
            pull_request_id=101,
            repository_id=100,
            description_hash="abc123",
            pull_request_summary="Summary",
        )

        assert info.pull_request_id == 101
        assert info.repository_id == 100
        assert info.description_hash == "abc123"
        assert info.pull_request_summary == "Summary"

    def test_pr_hash_info_with_none_values(self) -> None:
        """Test PRHashInfo dataclass with None values."""
        info = PRHashInfo(
            pull_request_id=101,
            repository_id=100,
            description_hash=None,
            pull_request_summary=None,
        )

        assert info.pull_request_id == 101
        assert info.repository_id == 100
        assert info.description_hash is None
        assert info.pull_request_summary is None


class TestMetricsInstrumentation:
    """Tests for metrics instrumentation in DAO methods."""

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
        """Mock DBMetrics with start_query returning a mock recorder."""
        metrics = MagicMock()
        record_fn = MagicMock()
        metrics.start_query.return_value = record_fn
        return metrics

    @pytest.fixture
    def dao_with_metrics(
        self, mock_engine: MagicMock, mock_metrics: MagicMock
    ) -> GHEPRDAO:
        """Create DAO instance with mock engine and metrics."""
        return GHEPRDAO(mock_engine, mock_metrics)

    def test_get_pr_hashes_records_metrics_on_success(
        self,
        dao_with_metrics: GHEPRDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test get_pr_hashes_by_repo_id records metrics on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchall.return_value = []
        conn.execute.return_value = result

        dao_with_metrics.get_pr_hashes_by_repo_id(org_id=1, repo_id=100)

        mock_metrics.start_query.assert_called_once_with("select", "ghe_pull_requests")
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_get_pr_hashes_records_metrics_on_error(
        self,
        dao_with_metrics: GHEPRDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test get_pr_hashes_by_repo_id records metrics on error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Database error")
        conn.execute.side_effect = error

        dao_with_metrics.get_pr_hashes_by_repo_id(org_id=1, repo_id=100)

        mock_metrics.start_query.return_value.assert_called_once_with(error)

    def test_find_records_metrics_on_success(
        self,
        dao_with_metrics: GHEPRDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test find_ghe_pr records metrics on successful query."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_row = MagicMock()
        mock_row._mapping = _make_pr_mapping(
            101,
            pull_request_summary="Summary",
            jira_tcmr_key="TCMR-123",
            environment="production",
            description_hash="abc123",
        )
        result = MagicMock()
        result.fetchone.return_value = mock_row
        conn.execute.return_value = result

        dao_with_metrics.find_ghe_pr(pull_request_id=101)

        mock_metrics.start_query.assert_called_once_with("select", "ghe_pull_requests")
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_find_records_metrics_on_not_found(
        self,
        dao_with_metrics: GHEPRDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test find_ghe_pr records metrics when PR not found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchone.return_value = None
        conn.execute.return_value = result

        dao_with_metrics.find_ghe_pr(pull_request_id=999)

        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_find_records_metrics_on_error(
        self,
        dao_with_metrics: GHEPRDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test find_ghe_pr records metrics with error on exception."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Database error")
        conn.execute.side_effect = error

        dao_with_metrics.find_ghe_pr(pull_request_id=101)

        mock_metrics.start_query.return_value.assert_called_once_with(error)

    def test_insert_records_metrics_on_success(
        self,
        dao_with_metrics: GHEPRDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test insert_new_pr records metrics on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        conn.execute.return_value = result

        pr = _make_pr(101)
        dao_with_metrics.insert_new_pr(pr)

        mock_metrics.start_query.assert_called_once_with("insert", "ghe_pull_requests")
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_insert_records_metrics_on_error(
        self,
        dao_with_metrics: GHEPRDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test insert_new_pr records metrics with error on exception."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Insert error")
        conn.execute.side_effect = error

        pr = _make_pr(101)
        dao_with_metrics.insert_new_pr(pr)

        mock_metrics.start_query.return_value.assert_called_once_with(error)

    def test_update_records_metrics_on_success(
        self,
        dao_with_metrics: GHEPRDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test update_pr records metrics on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        conn.execute.return_value = result

        pr = _make_pr(101, merged=True, state="closed")
        dao_with_metrics.update_pr(pr)

        mock_metrics.start_query.assert_called_once_with("update", "ghe_pull_requests")
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_update_records_metrics_on_error(
        self,
        dao_with_metrics: GHEPRDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test update_pr records metrics with error on exception."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Update error")
        conn.execute.side_effect = error

        pr = _make_pr(101, merged=True, state="closed")
        dao_with_metrics.update_pr(pr)

        mock_metrics.start_query.return_value.assert_called_once_with(error)

    def test_delete_records_metrics_on_success(
        self,
        dao_with_metrics: GHEPRDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test delete_ghe_pr records metrics on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        conn.execute.return_value = result

        dao_with_metrics.delete_ghe_pr(pull_request_id=101)

        mock_metrics.start_query.assert_called_once_with("update", "ghe_pull_requests")
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_delete_records_metrics_on_error(
        self,
        dao_with_metrics: GHEPRDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test delete_ghe_pr records metrics with error on exception."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Delete error")
        conn.execute.side_effect = error

        dao_with_metrics.delete_ghe_pr(pull_request_id=101)

        mock_metrics.start_query.return_value.assert_called_once_with(error)

    def test_upsert_batch_records_metrics_on_success(
        self,
        dao_with_metrics: GHEPRDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test _upsert_batch records metrics on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.rowcount = 3
        conn.execute.return_value = result

        prs = [_make_pr(i) for i in range(3)]
        dao_with_metrics._upsert_batch(prs)

        mock_metrics.start_query.assert_called_once_with("upsert", "ghe_pull_requests")
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_upsert_batch_records_metrics_on_error(
        self,
        dao_with_metrics: GHEPRDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test _upsert_batch records metrics with error on exception."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Database error")
        conn.execute.side_effect = error

        prs = [_make_pr(1)]
        dao_with_metrics._upsert_batch(prs)

        mock_metrics.start_query.return_value.assert_called_once_with(error)


class TestGetServicesByPRIdentifiers:
    """Tests for get_services_by_pr_identifiers method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRDAO:
        """Create DAO instance with mock engine."""
        return GHEPRDAO(mock_engine)

    def test_empty_lookups(self, dao: GHEPRDAO) -> None:
        """Test with empty lookups returns empty dict."""
        result = dao.get_services_by_pr_identifiers([])
        assert result == {}

    def test_successful_lookup(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test successful lookup returns services map."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        mock_row = MagicMock()
        mock_row.org_login = "Airbnb-ITX"
        mock_row.repo_name = "infra_indexer"
        mock_row.pull_request_number = 19
        mock_row.services = ["service-a", "service-b"]

        result_mock = MagicMock()
        result_mock.fetchall.return_value = [mock_row]
        conn.execute.return_value = result_mock

        lookups = [("Airbnb-ITX", "infra_indexer", 19)]
        result = dao.get_services_by_pr_identifiers(lookups)

        assert len(result) == 1
        assert result[("Airbnb-ITX", "infra_indexer", 19)] == [
            "service-a",
            "service-b",
        ]

    def test_services_as_json_string(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Test that JSON string services are parsed correctly."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        mock_row = MagicMock()
        mock_row.org_login = "Org"
        mock_row.repo_name = "repo"
        mock_row.pull_request_number = 1
        mock_row.services = '["svc-a", "svc-b"]'

        result_mock = MagicMock()
        result_mock.fetchall.return_value = [mock_row]
        conn.execute.return_value = result_mock

        result = dao.get_services_by_pr_identifiers([("Org", "repo", 1)])

        assert result[("Org", "repo", 1)] == ["svc-a", "svc-b"]

    def test_no_results_found(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test that empty results return empty dict."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        result_mock = MagicMock()
        result_mock.fetchall.return_value = []
        conn.execute.return_value = result_mock

        result = dao.get_services_by_pr_identifiers([("Org", "repo", 1)])
        assert result == {}

    def test_database_error_returns_empty(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Test that database error returns empty dict."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB error")

        result = dao.get_services_by_pr_identifiers([("Org", "repo", 1)])
        assert result == {}

    def test_multiple_lookups(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test batch lookup with multiple PR identifiers."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        mock_row1 = MagicMock()
        mock_row1.org_login = "Org1"
        mock_row1.repo_name = "repo1"
        mock_row1.pull_request_number = 1
        mock_row1.services = ["svc-a"]

        mock_row2 = MagicMock()
        mock_row2.org_login = "Org2"
        mock_row2.repo_name = "repo2"
        mock_row2.pull_request_number = 2
        mock_row2.services = ["svc-b", "svc-c"]

        result_mock = MagicMock()
        result_mock.fetchall.return_value = [mock_row1, mock_row2]
        conn.execute.return_value = result_mock

        lookups = [("Org1", "repo1", 1), ("Org2", "repo2", 2)]
        result = dao.get_services_by_pr_identifiers(lookups)

        assert len(result) == 2
        assert result[("Org1", "repo1", 1)] == ["svc-a"]
        assert result[("Org2", "repo2", 2)] == ["svc-b", "svc-c"]

    def test_metrics_recorded_on_success(self, mock_engine: MagicMock) -> None:
        """Test get_services_by_pr_identifiers records metrics on success."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = GHEPRDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        result_mock = MagicMock()
        result_mock.fetchall.return_value = []
        conn.execute.return_value = result_mock

        dao.get_services_by_pr_identifiers([("Org", "repo", 1)])

        mock_metrics.start_query.assert_called_once_with("select", "ghe_pull_requests")
        record_fn.assert_called_once_with(None)

    def test_metrics_recorded_on_error(self, mock_engine: MagicMock) -> None:
        """Test get_services_by_pr_identifiers records metrics on error."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = GHEPRDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("DB error")
        conn.execute.side_effect = error

        dao.get_services_by_pr_identifiers([("Org", "repo", 1)])

        record_fn.assert_called_once_with(error)


class TestFindPRsByTimeRange:
    """Tests for find_prs_by_time_range method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRDAO:
        """Create DAO instance with mock engine."""
        return GHEPRDAO(mock_engine)

    def test_find_prs_success(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test find_prs_by_time_range returns PRs within range."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_row = MagicMock()
        mock_row._mapping = _make_pr_mapping(
            101,
            pull_request_summary="Summary",
            description_hash="abc123",
        )
        result = MagicMock()
        result.fetchall.return_value = [mock_row]
        conn.execute.return_value = result

        prs = dao.find_prs_by_time_range(
            start_time=datetime(2024, 1, 15, 0, 0, 0),
            end_time=datetime(2024, 1, 16, 23, 59, 59),
        )

        assert prs is not None
        assert len(prs) == 1
        assert prs[0].pull_request_id == 101

    def test_find_prs_empty(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Test find_prs_by_time_range returns empty list when none found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchall.return_value = []
        conn.execute.return_value = result

        prs = dao.find_prs_by_time_range(
            start_time=datetime(2024, 1, 15, 0, 0, 0),
            end_time=datetime(2024, 1, 16, 23, 59, 59),
        )

        assert prs is not None
        assert len(prs) == 0

    def test_find_prs_with_services_filter(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Test find_prs_by_time_range with services filter."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchall.return_value = []
        conn.execute.return_value = result

        prs = dao.find_prs_by_time_range(
            start_time=datetime(2024, 1, 15, 0, 0, 0),
            end_time=datetime(2024, 1, 16, 23, 59, 59),
            services=["service-checkout"],
        )

        assert prs is not None
        conn.execute.assert_called_once()

    def test_find_prs_with_merged_at_time_field(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Test find_prs_by_time_range with merged_at time field."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchall.return_value = []
        conn.execute.return_value = result

        prs = dao.find_prs_by_time_range(
            start_time=datetime(2024, 1, 15, 0, 0, 0),
            end_time=datetime(2024, 1, 16, 23, 59, 59),
            time_field="merged_at",
        )

        assert prs is not None

    def test_find_prs_invalid_time_field(self, dao: GHEPRDAO) -> None:
        """Test find_prs_by_time_range raises ValueError for invalid time_field."""
        with pytest.raises(ValueError, match="Invalid time_field"):
            dao.find_prs_by_time_range(
                start_time=datetime(2024, 1, 15, 0, 0, 0),
                end_time=datetime(2024, 1, 16, 23, 59, 59),
                time_field="invalid_field",
            )

    def test_find_prs_database_error(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Test find_prs_by_time_range returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        prs = dao.find_prs_by_time_range(
            start_time=datetime(2024, 1, 15, 0, 0, 0),
            end_time=datetime(2024, 1, 16, 23, 59, 59),
        )

        assert prs is None

    def test_find_prs_metrics_on_success(self, mock_engine: MagicMock) -> None:
        """Test find_prs_by_time_range records metrics on success."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = GHEPRDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchall.return_value = []
        conn.execute.return_value = result

        dao.find_prs_by_time_range(
            start_time=datetime(2024, 1, 15, 0, 0, 0),
            end_time=datetime(2024, 1, 16, 23, 59, 59),
        )

        mock_metrics.start_query.assert_called_once_with("select", "ghe_pull_requests")
        record_fn.assert_called_once_with(None)

    def test_find_prs_metrics_on_error(self, mock_engine: MagicMock) -> None:
        """Test find_prs_by_time_range records metrics on error."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = GHEPRDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Database error")
        conn.execute.side_effect = error

        dao.find_prs_by_time_range(
            start_time=datetime(2024, 1, 15, 0, 0, 0),
            end_time=datetime(2024, 1, 16, 23, 59, 59),
        )

        record_fn.assert_called_once_with(error)


class TestFindPRsWithRepoByPullRequestIds:
    """Tests for find_prs_with_repo_by_pull_request_ids method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRDAO:
        return GHEPRDAO(mock_engine)

    def _make_mock_row(
        self,
        pull_request_id: int,
        org: str = "Airbnb-ITX",
        repo_name: str = "my-repo",
    ) -> MagicMock:
        mock_row = MagicMock()
        mock_row._mapping = _make_pr_mapping(
            pull_request_id,
            org_login=org,
            repo_name=repo_name,
        )
        mock_row.org_login = org
        mock_row.repo_name = repo_name
        return mock_row

    def test_returns_empty_list_for_empty_input(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Test returns empty list without hitting the database for empty input."""
        result = dao.find_prs_with_repo_by_pull_request_ids([])

        assert result == []
        mock_engine.connect.assert_not_called()

    def test_returns_prs_with_repo_info(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Test returns GHEPullRequestWithRepo objects with org and repo_name."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [
            self._make_mock_row(101, org="Airbnb-ITX", repo_name="my-repo"),
            self._make_mock_row(102, org="Airbnb-ITX", repo_name="other-repo"),
        ]

        result = dao.find_prs_with_repo_by_pull_request_ids([101, 102])

        assert len(result) == 2
        assert all(isinstance(r, GHEPullRequestWithRepo) for r in result)
        assert result[0].pr.pull_request_id == 101
        assert result[0].org == "Airbnb-ITX"
        assert result[0].repo_name == "my-repo"
        assert result[1].pr.pull_request_id == 102
        assert result[1].org == "Airbnb-ITX"
        assert result[1].repo_name == "other-repo"

    def test_returns_empty_list_when_none_found(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Test returns empty list when no PRs match."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []

        result = dao.find_prs_with_repo_by_pull_request_ids([99999])

        assert result == []

    def test_returns_empty_list_on_database_error(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Test returns empty list on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        result = dao.find_prs_with_repo_by_pull_request_ids([101, 102])

        assert result == []

    def test_metrics_recorded_on_success(self, mock_engine: MagicMock) -> None:
        """Test find_prs_with_repo_by_pull_request_ids records metrics on success."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = GHEPRDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []

        dao.find_prs_with_repo_by_pull_request_ids([101])

        mock_metrics.start_query.assert_called_once_with("select", "ghe_pull_requests")
        record_fn.assert_called_once_with(None)

    def test_metrics_recorded_on_error(self, mock_engine: MagicMock) -> None:
        """Test find_prs_with_repo_by_pull_request_ids records metrics on error."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = GHEPRDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Database error")
        conn.execute.side_effect = error

        dao.find_prs_with_repo_by_pull_request_ids([101])

        record_fn.assert_called_once_with(error)


class TestGetPRHashesByIds:
    """Tests for get_pr_hashes_by_ids — fetches hashes for specific PRs by GitHub API IDs.

    Looks up PRs by their globally-unique ``pull_request_id`` and builds the
    result key from the inline ``org_id``/``repo_id`` columns on each row.
    """

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRDAO:
        """Create DAO instance with mock engine."""
        return GHEPRDAO(mock_engine)

    def _make_mock_row(
        self,
        org_id: int,
        repo_id: int,
        pull_request_id: int,
        description_hash: str | None = "hash_abc",
        pull_request_summary: str | None = "PR summary",
        services: list[str] | None = None,
    ) -> MagicMock:
        """Build a mock DB result row matching the SELECT columns in get_pr_hashes_by_ids."""
        row = MagicMock()
        row.org_id = org_id
        row.repo_id = repo_id
        row.pull_request_id = pull_request_id
        row.description_hash = description_hash
        row.pull_request_summary = pull_request_summary
        row.services = services
        return row

    def test_empty_entity_ids_returns_empty_dict(self, dao: GHEPRDAO) -> None:
        """Empty input returns {} without hitting the DB."""
        result = dao.get_pr_hashes_by_ids([])
        assert result == {}

    def test_returns_hash_info_keyed_by_github_api_ids(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Results are keyed by 'org_id:repo_id:pr_id' using GitHub API IDs."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [
            self._make_mock_row(10, 20, 30, description_hash="dh_abc"),
        ]

        entity_ids = [{"org_id": 10, "repository_id": 20, "pull_request_id": 30}]
        result = dao.get_pr_hashes_by_ids(entity_ids)

        assert result is not None
        assert "10:20:30" in result
        info = result["10:20:30"]
        assert info.description_hash == "dh_abc"
        assert info.pull_request_id == 30
        assert info.repository_id == 20

    def test_multiple_prs_returned_correctly(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Multiple PRs across different repos all appear in the result."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [
            self._make_mock_row(10, 20, 30, description_hash="h1"),
            self._make_mock_row(10, 21, 31, description_hash="h2"),
        ]

        entity_ids = [
            {"org_id": 10, "repository_id": 20, "pull_request_id": 30},
            {"org_id": 10, "repository_id": 21, "pull_request_id": 31},
        ]
        result = dao.get_pr_hashes_by_ids(entity_ids)

        assert result is not None
        assert len(result) == 2
        assert result["10:20:30"].description_hash == "h1"
        assert result["10:21:31"].description_hash == "h2"

    def test_pr_not_in_db_absent_from_result(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """PRs that don't exist in DB (or are soft-deleted) are simply absent from result."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []  # nothing found

        result = dao.get_pr_hashes_by_ids(
            [{"org_id": 10, "repository_id": 20, "pull_request_id": 99}]
        )

        assert result == {}

    def test_null_description_hash_preserved(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """NULL description_hash in DB is returned as None (not filtered out)."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [
            self._make_mock_row(10, 20, 30, description_hash=None),
        ]

        result = dao.get_pr_hashes_by_ids(
            [{"org_id": 10, "repository_id": 20, "pull_request_id": 30}]
        )

        assert result is not None
        assert result["10:20:30"].description_hash is None

    def test_database_error_returns_none(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Returns None on database error (signals failure to the caller)."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB connection lost")

        result = dao.get_pr_hashes_by_ids(
            [{"org_id": 10, "repository_id": 20, "pull_request_id": 30}]
        )

        assert result is None

    def test_metrics_recorded_on_success(self, mock_engine: MagicMock) -> None:
        """DBMetrics.start_query is called and record(None) is invoked on success."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = GHEPRDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []

        dao.get_pr_hashes_by_ids(
            [{"org_id": 10, "repository_id": 20, "pull_request_id": 30}]
        )

        mock_metrics.start_query.assert_called_once_with("select", "ghe_pull_requests")
        record_fn.assert_called_once_with(None)

    def test_metrics_recorded_on_error(self, mock_engine: MagicMock) -> None:
        """record(exception) is called when a database error occurs."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = GHEPRDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("DB error")
        conn.execute.side_effect = error

        dao.get_pr_hashes_by_ids(
            [{"org_id": 10, "repository_id": 20, "pull_request_id": 30}]
        )

        mock_metrics.start_query.assert_called_once_with("select", "ghe_pull_requests")
        record_fn.assert_called_once_with(error)


class TestUpdateLLMFields:
    """Tests for GHEPRDAO.update_llm_fields."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRDAO:
        return GHEPRDAO(mock_engine)

    def test_success_returns_true(self, dao: GHEPRDAO, mock_engine: MagicMock) -> None:
        """Successful update returns True."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.rowcount = 1

        result = dao.update_llm_fields(
            pull_request_id=101,
            pull_request_summary="PR implements feature X",
            description_hash="abc123",
        )
        assert result is WriteOutcome.WRITTEN

    def test_null_llm_fields_returns_true(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Passing None for all LLM fields still succeeds."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.rowcount = 1

        result = dao.update_llm_fields(
            pull_request_id=101,
            pull_request_summary=None,
            description_hash=None,
        )
        assert result is WriteOutcome.WRITTEN

    def test_zero_rowcount_returns_base_not_found(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """Returns BASE_NOT_FOUND when 0 rows updated (unguarded: no entered_at)."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.rowcount = 0

        result = dao.update_llm_fields(
            pull_request_id=101,
            pull_request_summary="summary",
            description_hash="abc",
        )
        assert result is WriteOutcome.BASE_NOT_FOUND

    def test_db_error_returns_error(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """DB exception returns ERROR without raising."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB connection lost")

        result = dao.update_llm_fields(
            pull_request_id=101,
            pull_request_summary="summary",
            description_hash="abc",
        )
        assert result is WriteOutcome.ERROR

    def test_metrics_recorded_on_success(self, mock_engine: MagicMock) -> None:
        """DBMetrics.start_query is called and record(None) is invoked on success."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = GHEPRDAO(mock_engine, mock_metrics)

        dao.update_llm_fields(
            pull_request_id=101,
            pull_request_summary="summary",
            description_hash="abc",
        )

        mock_metrics.start_query.assert_called_once_with("update", "ghe_pull_requests")
        record_fn.assert_called_once_with(None)

    def test_metrics_recorded_on_error(self, mock_engine: MagicMock) -> None:
        """record(exception) is called when a database error occurs."""
        mock_metrics = MagicMock()
        record_fn = MagicMock()
        mock_metrics.start_query.return_value = record_fn
        dao = GHEPRDAO(mock_engine, mock_metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("DB error")
        conn.execute.side_effect = error

        dao.update_llm_fields(
            pull_request_id=101,
            pull_request_summary="summary",
            description_hash="abc",
        )

        mock_metrics.start_query.assert_called_once_with("update", "ghe_pull_requests")
        record_fn.assert_called_once_with(error)

    def test_where_clause_filters_by_pull_request_id(
        self, dao: GHEPRDAO, mock_engine: MagicMock
    ) -> None:
        """The UPDATE statement targets the PR solely by its primary key.

        The denormalized table makes pull_request_id the primary key, so the
        WHERE clause filters on it directly — no join through a repositories
        table.
        """
        from sqlalchemy.dialects import mysql

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.rowcount = 1

        dao.update_llm_fields(
            pull_request_id=101,
            pull_request_summary="summary",
            description_hash="abc",
        )

        compiled_sql = str(
            conn.execute.call_args[0][0].compile(
                dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        assert "ghe_pull_requests" in compiled_sql
        assert "pull_request_id" in compiled_sql
        assert "101" in compiled_sql
        assert "ghe_repositories" not in compiled_sql
