"""Unit tests for database utility functions."""

from unittest.mock import MagicMock, patch

import pytest

from common.models.mysql_config import MySQLConfig
from common.utils.db_utils import (
    _get_short_lived_connection_url,
    _get_ssl_connect_args,
    _TokenProvider,
    build_connection_url,
    create_long_lived_engine,
    create_short_lived_engine,
    generate_rds_auth_token,
)


class TestBuildConnectionUrl:
    """Test suite for build_connection_url function."""

    def test_basic_url_without_iam(self) -> None:
        """Test building URL with username/password auth."""
        config = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            port=3306,
            username="testuser",
            password="testpass",
            database="testdb",
            use_iam=False,
        )
        url = build_connection_url(config)
        assert url.startswith("mysql+pymysql://testuser:testpass@localhost:3306/testdb")
        assert "charset=utf8mb4" in url

    def test_url_with_special_characters_in_password(self) -> None:
        """Test that special characters in password are URL-encoded."""
        config = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            port=3306,
            username="user",
            password="p@ss/word#123",
            database="testdb",
            use_iam=False,
        )
        url = build_connection_url(config)
        # Special characters should be URL-encoded
        assert "p%40ss%2Fword%23123" in url

    def test_url_with_iam_token(self) -> None:
        """Test building URL with IAM token."""
        config = MySQLConfig(
            engine="mysql",
            endpoint="db.example.com",
            port=3306,
            username="iam_user",
            database="testdb",
            use_iam=True,
            region="us-east-1",
        )
        token = "iam-auth-token-12345"
        url = build_connection_url(config, iam_token=token)
        assert "iam-auth-token-12345" in url
        assert "iam_user" in url

    def test_iam_without_token_raises_error(self) -> None:
        """Test that IAM auth without token raises ValueError."""
        config = MySQLConfig(
            engine="mysql",
            endpoint="db.example.com",
            port=3306,
            username="iam_user",
            database="testdb",
            use_iam=True,
        )
        with pytest.raises(ValueError, match="IAM authentication enabled"):
            build_connection_url(config)

    def test_url_with_empty_password(self) -> None:
        """Test building URL with empty password."""
        config = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            port=3306,
            username="user",
            password="",
            database="testdb",
            use_iam=False,
        )
        url = build_connection_url(config)
        assert "user:@localhost" in url

    def test_url_with_none_password(self) -> None:
        """Test building URL with None password."""
        config = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            port=3306,
            username="user",
            password=None,
            database="testdb",
            use_iam=False,
        )
        url = build_connection_url(config)
        assert "user:@localhost" in url


class TestGenerateRdsAuthToken:
    """Test suite for generate_rds_auth_token function."""

    def test_generate_token(self) -> None:
        """Test generating RDS auth token."""
        mock_session = MagicMock()
        mock_rds_client = MagicMock()
        mock_session.client.return_value = mock_rds_client
        mock_rds_client.generate_db_auth_token.return_value = "generated-token-xyz"

        token = generate_rds_auth_token(
            session=mock_session,
            hostname="db.example.com",
            port=3306,
            username="dbuser",
            region="us-east-1",
        )

        mock_session.client.assert_called_once_with("rds", region_name="us-east-1")
        mock_rds_client.generate_db_auth_token.assert_called_once_with(
            DBHostname="db.example.com",
            Port=3306,
            DBUsername="dbuser",
            Region="us-east-1",
        )
        assert token == "generated-token-xyz"


class TestGetShortLivedConnectionUrl:
    """Test suite for _get_short_lived_connection_url function."""

    def test_without_iam(self) -> None:
        """Test getting connection URL without IAM."""
        config = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            port=3306,
            username="user",
            password="pass",
            database="testdb",
            use_iam=False,
        )
        url = _get_short_lived_connection_url(config)
        assert "mysql+pymysql://user:pass@localhost:3306/testdb" in url

    @patch("common.utils.db_utils.get_session")
    @patch("common.utils.db_utils.generate_rds_auth_token")
    def test_with_iam(
        self, mock_generate_token: MagicMock, mock_get_session: MagicMock
    ) -> None:
        """Test getting connection URL with IAM authentication."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        mock_generate_token.return_value = "iam-token-abc"

        config = MySQLConfig(
            engine="mysql",
            endpoint="vpce.amazonaws.com",
            port=3306,
            username="iam_user",
            database="testdb",
            use_iam=True,
            region="us-east-1",
            canonical_endpoint="db.cluster.us-east-1.rds.amazonaws.com",
            corpinfra_role_arn="arn:aws:iam::123456789:role/rds-access",
        )

        url = _get_short_lived_connection_url(config)

        mock_get_session.assert_called_once_with(
            role_arn="arn:aws:iam::123456789:role/rds-access",
            region="us-east-1",
            session_name="matik-rds-access",
        )
        mock_generate_token.assert_called_once_with(
            session=mock_session,
            hostname="db.cluster.us-east-1.rds.amazonaws.com",
            port=3306,
            username="iam_user",
            region="us-east-1",
        )
        assert "iam-token-abc" in url

    @patch("common.utils.db_utils.get_session")
    @patch("common.utils.db_utils.generate_rds_auth_token")
    def test_with_iam_and_provided_session(
        self, mock_generate_token: MagicMock, mock_get_session: MagicMock
    ) -> None:
        """Test getting connection URL with IAM when session is provided."""
        provided_session = MagicMock()
        mock_generate_token.return_value = "iam-token-provided"

        config = MySQLConfig(
            engine="mysql",
            endpoint="db.example.com",
            port=3306,
            username="iam_user",
            database="testdb",
            use_iam=True,
            region="us-east-1",
        )

        url = _get_short_lived_connection_url(config, session=provided_session)

        # Should NOT call get_session when session is provided
        mock_get_session.assert_not_called()

        # Should use provided session for token generation
        mock_generate_token.assert_called_once_with(
            session=provided_session,
            hostname="db.example.com",
            port=3306,
            username="iam_user",
            region="us-east-1",
        )
        assert "iam-token-provided" in url


class TestTokenProvider:
    """Test suite for _TokenProvider class."""

    @patch("common.utils.db_utils.get_session")
    @patch("common.utils.db_utils.generate_rds_auth_token")
    def test_get_token_initial(
        self, mock_generate_token: MagicMock, mock_get_session: MagicMock
    ) -> None:
        """Test getting initial token."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        mock_generate_token.return_value = "initial-token"

        config = MySQLConfig(
            engine="mysql",
            endpoint="db.example.com",
            port=3306,
            username="user",
            database="testdb",
            use_iam=True,
            region="us-east-1",
        )
        provider = _TokenProvider(config)

        token = provider.get_token()

        assert token == "initial-token"
        mock_generate_token.assert_called_once()

    @patch("common.utils.db_utils.get_session")
    @patch("common.utils.db_utils.generate_rds_auth_token")
    def test_get_token_cached(
        self, mock_generate_token: MagicMock, mock_get_session: MagicMock
    ) -> None:
        """Test that token is cached and not regenerated immediately."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        mock_generate_token.return_value = "cached-token"

        config = MySQLConfig(
            engine="mysql",
            endpoint="db.example.com",
            port=3306,
            username="user",
            database="testdb",
            use_iam=True,
            region="us-east-1",
            token_expiry_minutes=15,
            token_expiry_grace_minutes=1,
        )
        provider = _TokenProvider(config)

        # Get token twice
        token1 = provider.get_token()
        token2 = provider.get_token()

        assert token1 == token2
        # Should only generate once due to caching
        assert mock_generate_token.call_count == 1

    @patch("common.utils.db_utils.time.time")
    @patch("common.utils.db_utils.get_session")
    @patch("common.utils.db_utils.generate_rds_auth_token")
    def test_get_token_refresh_on_expiry(
        self,
        mock_generate_token: MagicMock,
        mock_get_session: MagicMock,
        mock_time: MagicMock,
    ) -> None:
        """Test that token is refreshed when expired."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        mock_generate_token.side_effect = ["token-1", "token-2"]

        config = MySQLConfig(
            engine="mysql",
            endpoint="db.example.com",
            port=3306,
            username="user",
            database="testdb",
            use_iam=True,
            region="us-east-1",
            token_expiry_minutes=15,
            token_expiry_grace_minutes=1,
        )
        provider = _TokenProvider(config)

        # First call at time 0
        mock_time.return_value = 0
        token1 = provider.get_token()

        # Second call after token expired (15 min - 1 min grace = 14 min = 840 sec)
        mock_time.return_value = 900  # 15 minutes later
        token2 = provider.get_token()

        assert token1 == "token-1"
        assert token2 == "token-2"
        assert mock_generate_token.call_count == 2

    def test_uses_canonical_endpoint_for_token(self) -> None:
        """Test that canonical_endpoint is used for token generation if set."""
        with (
            patch("common.utils.db_utils.get_session") as mock_get_session,
            patch(
                "common.utils.db_utils.generate_rds_auth_token"
            ) as mock_generate_token,
        ):
            mock_session = MagicMock()
            mock_get_session.return_value = mock_session
            mock_generate_token.return_value = "token"

            config = MySQLConfig(
                engine="mysql",
                endpoint="vpce.amazonaws.com",
                port=3306,
                username="user",
                database="testdb",
                use_iam=True,
                region="us-east-1",
                canonical_endpoint="actual-db.rds.amazonaws.com",
            )
            provider = _TokenProvider(config)
            provider.get_token()

            # Should use canonical_endpoint, not endpoint
            call_kwargs = mock_generate_token.call_args[1]
            assert call_kwargs["hostname"] == "actual-db.rds.amazonaws.com"


class TestGetSslConnectArgs:
    """Test suite for _get_ssl_connect_args function."""

    def test_returns_ssl_config_for_iam_auth(self) -> None:
        """Test that SSL config is returned when IAM auth is enabled."""
        config = MySQLConfig(
            engine="mysql",
            endpoint="db.example.com",
            port=3306,
            username="iam_user",
            database="testdb",
            use_iam=True,
            region="us-east-1",
        )
        ssl_args = _get_ssl_connect_args(config)

        assert "ssl" in ssl_args
        assert (
            ssl_args["ssl"]["ca"]
            == "/usr/local/share/ca-certificates/rds-combined-ca-bundle.crt"
        )
        assert ssl_args["ssl"]["check_hostname"] is True

    def test_returns_empty_dict_for_non_iam_auth(self) -> None:
        """Test that empty dict is returned when IAM auth is disabled."""
        config = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            port=3306,
            username="user",
            password="pass",
            database="testdb",
            use_iam=False,
        )
        ssl_args = _get_ssl_connect_args(config)

        assert ssl_args == {}

    @patch("common.utils.db_utils.ssl")
    def test_returns_ssl_context_with_canonical_endpoint(
        self, mock_ssl: MagicMock
    ) -> None:
        """Test that SSL context with hostname check disabled for VPC endpoint."""
        mock_context = MagicMock()
        mock_ssl.create_default_context.return_value = mock_context
        mock_ssl.CERT_REQUIRED = 2  # ssl.CERT_REQUIRED value

        config = MySQLConfig(
            engine="mysql",
            endpoint="vpce.amazonaws.com",
            port=3306,
            username="iam_user",
            database="testdb",
            use_iam=True,
            region="us-east-1",
            canonical_endpoint="actual-db.rds.amazonaws.com",
        )
        ssl_args = _get_ssl_connect_args(config)

        # When canonical_endpoint is set, hostname check is disabled for VPC endpoint
        assert "ssl" in ssl_args
        assert ssl_args["ssl"] == mock_context
        assert mock_context.check_hostname is False
        assert mock_context.verify_mode == 2  # ssl.CERT_REQUIRED
        mock_ssl.create_default_context.assert_called_once_with(
            cafile="/usr/local/share/ca-certificates/rds-combined-ca-bundle.crt"
        )


class TestCreateShortLivedEngine:
    """Test suite for create_short_lived_engine function."""

    @patch("common.utils.db_utils.create_engine")
    def test_without_iam(self, mock_create_engine: MagicMock) -> None:
        """Test creating engine without IAM auth."""
        config = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            port=3306,
            username="user",
            password="pass",
            database="testdb",
            use_iam=False,
        )
        create_short_lived_engine(config)

        mock_create_engine.assert_called_once()
        call_kwargs = mock_create_engine.call_args[1]
        # Non-IAM still pins the session to UTC, but has no SSL config
        assert call_kwargs["connect_args"] == {
            "init_command": "SET time_zone = '+00:00'"
        }
        # Stale-connection guard is enabled by default
        assert call_kwargs["pool_pre_ping"] is True

    @patch("common.utils.db_utils.create_engine")
    @patch("common.utils.db_utils.get_session")
    @patch("common.utils.db_utils.generate_rds_auth_token")
    def test_with_iam_includes_ssl_connect_args(
        self,
        mock_generate_token: MagicMock,
        mock_get_session: MagicMock,
        mock_create_engine: MagicMock,
    ) -> None:
        """Test creating engine with IAM auth includes SSL connect_args."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        mock_generate_token.return_value = "iam-token"

        config = MySQLConfig(
            engine="mysql",
            endpoint="db.example.com",
            port=3306,
            username="iam_user",
            database="testdb",
            use_iam=True,
            region="us-east-1",
        )
        create_short_lived_engine(config)

        mock_create_engine.assert_called_once()
        call_kwargs = mock_create_engine.call_args[1]
        # Should have SSL connect_args for IAM auth
        assert "connect_args" in call_kwargs
        assert "ssl" in call_kwargs["connect_args"]
        assert (
            call_kwargs["connect_args"]["ssl"]["ca"]
            == "/usr/local/share/ca-certificates/rds-combined-ca-bundle.crt"
        )
        assert call_kwargs["connect_args"]["ssl"]["check_hostname"] is True
        # ...and still pins the session to UTC
        assert call_kwargs["connect_args"]["init_command"] == "SET time_zone = '+00:00'"

    @patch("common.utils.db_utils.create_engine")
    @patch("common.utils.db_utils.get_session")
    @patch("common.utils.db_utils.generate_rds_auth_token")
    def test_with_provided_session(
        self,
        mock_generate_token: MagicMock,
        mock_get_session: MagicMock,
        mock_create_engine: MagicMock,
    ) -> None:
        """Test creating engine with provided session skips get_session."""
        provided_session = MagicMock()
        mock_generate_token.return_value = "iam-token"

        config = MySQLConfig(
            engine="mysql",
            endpoint="db.example.com",
            port=3306,
            username="iam_user",
            database="testdb",
            use_iam=True,
            region="us-east-1",
        )
        create_short_lived_engine(config, session=provided_session)

        # Should NOT call get_session when session is provided
        mock_get_session.assert_not_called()

        # Should use provided session for token generation
        mock_generate_token.assert_called_once()
        call_kwargs = mock_generate_token.call_args[1]
        assert call_kwargs["session"] == provided_session

        mock_create_engine.assert_called_once()


class TestCreateLongLivedEngine:
    """Test suite for create_long_lived_engine pool defaults."""

    @patch("common.utils.db_utils.create_engine")
    def test_enables_stale_connection_guards_by_default(
        self, mock_create_engine: MagicMock
    ) -> None:
        """pool_pre_ping and a 10-minute pool_recycle default are applied."""
        config = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            port=3306,
            username="user",
            password="pass",
            database="testdb",
            use_iam=False,
        )
        create_long_lived_engine(config)

        mock_create_engine.assert_called_once()
        call_kwargs = mock_create_engine.call_args[1]
        assert call_kwargs["pool_pre_ping"] is True
        assert call_kwargs["pool_recycle"] == 600

    @patch("common.utils.db_utils.create_engine")
    def test_config_pool_recycle_overrides_default(
        self, mock_create_engine: MagicMock
    ) -> None:
        """A configured conn_max_lifetime_minutes wins over the default."""
        config = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            port=3306,
            username="user",
            password="pass",
            database="testdb",
            use_iam=False,
            conn_max_lifetime_minutes=5,
        )
        create_long_lived_engine(config)

        call_kwargs = mock_create_engine.call_args[1]
        assert call_kwargs["pool_recycle"] == 300  # 5 minutes, from config
        assert call_kwargs["pool_pre_ping"] is True

    @patch("common.utils.db_utils.create_engine")
    def test_explicit_engine_kwargs_override_defaults(
        self, mock_create_engine: MagicMock
    ) -> None:
        """Callers can still override the pool defaults via engine_kwargs."""
        config = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            port=3306,
            username="user",
            password="pass",
            database="testdb",
            use_iam=False,
        )
        create_long_lived_engine(config, pool_pre_ping=False, pool_recycle=42)

        call_kwargs = mock_create_engine.call_args[1]
        assert call_kwargs["pool_pre_ping"] is False
        assert call_kwargs["pool_recycle"] == 42
