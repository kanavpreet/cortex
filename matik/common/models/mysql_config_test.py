"""Unit tests for MySQLConfig model."""

import pytest
from pydantic import ValidationError

from common.models.mysql_config import MySQLConfig


class TestMySQLConfig:
    """Test suite for MySQLConfig Pydantic model."""

    def test_valid_instantiation_minimal(self) -> None:
        """Test creating MySQLConfig with minimal required fields."""
        conn = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            username="root",
            database="matik",
        )
        assert conn.engine == "mysql"
        assert conn.endpoint == "localhost"
        assert conn.username == "root"
        assert conn.database == "matik"
        assert conn.port == 3306  # Default
        assert conn.password is None  # Default
        assert conn.use_iam is False  # Default

    def test_valid_instantiation_traditional_auth(self) -> None:
        """Test creating MySQLConfig with traditional authentication."""
        conn = MySQLConfig(
            engine="mysql",
            endpoint="db.example.com",
            port=3307,
            username="app_user",
            password="secret123",
            database="production",
            ssl_mode="required",
        )
        assert conn.engine == "mysql"
        assert conn.endpoint == "db.example.com"
        assert conn.port == 3307
        assert conn.username == "app_user"
        assert conn.password == "secret123"
        assert conn.database == "production"
        assert conn.ssl_mode == "required"
        assert conn.use_iam is False

    def test_valid_instantiation_iam_auth(self) -> None:
        """Test creating MySQLConfig with AWS IAM authentication."""
        conn = MySQLConfig(
            engine="mysql",
            endpoint="vpce-123.vpce-svc-456.us-east-1.vpce.amazonaws.com",
            username="iam_user",
            database="matik",
            use_iam=True,
            region="us-east-1",
            canonical_endpoint="matik-dev.cluster-abc.us-east-1.rds.amazonaws.com",
            corpinfra_role_arn="arn:aws:iam::123456789:role/matik-rds-access",
            ssl_mode="required",
        )
        assert conn.use_iam is True
        assert conn.region == "us-east-1"
        assert (
            conn.canonical_endpoint
            == "matik-dev.cluster-abc.us-east-1.rds.amazonaws.com"
        )
        assert conn.corpinfra_role_arn == "arn:aws:iam::123456789:role/matik-rds-access"
        assert conn.password is None

    def test_connection_pool_settings(self) -> None:
        """Test connection pool configuration."""
        conn = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            username="root",
            database="matik",
            max_open_conns=25,
            max_idle_conns=5,
            conn_max_lifetime_minutes=10,
            conn_max_idle_time_minutes=5,
        )
        assert conn.max_open_conns == 25
        assert conn.max_idle_conns == 5
        assert conn.conn_max_lifetime_minutes == 10
        assert conn.conn_max_idle_time_minutes == 5

    def test_required_fields_missing(self) -> None:
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            MySQLConfig(engine="mysql")
        error_str = str(exc_info.value)
        assert "endpoint" in error_str
        assert "username" in error_str
        assert "database" in error_str

    def test_default_values(self) -> None:
        """Test that default values are set correctly."""
        conn = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            username="root",
            database="test",
        )
        assert conn.port == 3306
        assert conn.password is None
        assert conn.use_iam is False
        assert conn.region is None
        assert conn.ssl_mode is None
        assert conn.canonical_endpoint is None
        assert conn.corpinfra_role_arn is None
        assert conn.max_open_conns == 0
        assert conn.max_idle_conns == 2
        assert conn.conn_max_lifetime_minutes == 0
        assert conn.conn_max_idle_time_minutes == 0

    def test_orm_mode(self) -> None:
        """Test that model can be created from dict-like objects."""
        data = {
            "engine": "mysql",
            "endpoint": "db.example.com",
            "port": 3306,
            "username": "user",
            "password": "pass",
            "database": "mydb",
            "use_iam": False,
            "region": None,
            "ssl_mode": "preferred",
            "canonical_endpoint": None,
            "corpinfra_role_arn": None,
            "max_open_conns": 10,
            "max_idle_conns": 3,
            "conn_max_lifetime_minutes": 15,
            "conn_max_idle_time_minutes": 5,
        }
        conn = MySQLConfig.model_validate(data)
        assert conn.engine == "mysql"
        assert conn.password == "pass"
        assert conn.max_open_conns == 10

    def test_ssl_mode_values(self) -> None:
        """Test different SSL mode values."""
        for ssl_mode in ["required", "preferred", "disabled"]:
            conn = MySQLConfig(
                engine="mysql",
                endpoint="localhost",
                username="root",
                database="test",
                ssl_mode=ssl_mode,
            )
            assert conn.ssl_mode == ssl_mode

    def test_different_engines(self) -> None:
        """Test different database engine types."""
        for engine in ["mysql", "postgres", "mariadb"]:
            conn = MySQLConfig(
                engine=engine,
                endpoint="localhost",
                username="root",
                database="test",
            )
            assert conn.engine == engine

    def test_custom_port(self) -> None:
        """Test custom port configuration."""
        conn = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            port=5432,
            username="root",
            database="test",
        )
        assert conn.port == 5432

    def test_json_serialization(self) -> None:
        """Test model serializes to JSON correctly."""
        conn = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            username="root",
            database="test",
            use_iam=True,
            region="us-west-2",
        )
        data = conn.model_dump()
        assert data["engine"] == "mysql"
        assert data["endpoint"] == "localhost"
        assert data["use_iam"] is True
        assert data["region"] == "us-west-2"

    def test_optional_fields_all_none(self) -> None:
        """Test that all optional fields can be None."""
        conn = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            username="root",
            database="test",
            password=None,
            region=None,
            ssl_mode=None,
            canonical_endpoint=None,
            corpinfra_role_arn=None,
        )
        assert conn.password is None
        assert conn.region is None
        assert conn.ssl_mode is None
        assert conn.canonical_endpoint is None
        assert conn.corpinfra_role_arn is None

    def test_full_iam_configuration(self) -> None:
        """Test complete IAM authentication configuration."""
        conn = MySQLConfig(
            engine="mysql",
            endpoint="vpce-endpoint.amazonaws.com",
            port=3306,
            username="matik_service",
            database="matik_production",
            use_iam=True,
            region="us-east-1",
            canonical_endpoint="matik.cluster-xyz.us-east-1.rds.amazonaws.com",
            corpinfra_role_arn="arn:aws:iam::226312150113:role/matik-rds-access-dev",
            ssl_mode="required",
            max_open_conns=20,
            max_idle_conns=5,
            conn_max_lifetime_minutes=10,
            conn_max_idle_time_minutes=5,
        )
        # Verify all fields are set correctly
        assert conn.use_iam is True
        assert conn.region == "us-east-1"
        assert conn.corpinfra_role_arn is not None
        assert "matik-rds-access-dev" in conn.corpinfra_role_arn
        assert conn.conn_max_lifetime_minutes == 10

    def test_no_password_with_iam(self) -> None:
        """Test IAM auth configuration without password."""
        conn = MySQLConfig(
            engine="mysql",
            endpoint="rds.amazonaws.com",
            username="iam_user",
            database="matik",
            use_iam=True,
            region="us-east-1",
        )
        assert conn.password is None
        assert conn.use_iam is True

    def test_password_with_traditional_auth(self) -> None:
        """Test traditional auth with password."""
        conn = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            username="root",
            password="mypassword123",
            database="test",
        )
        assert conn.password == "mypassword123"
        assert conn.use_iam is False


class TestToSqlalchemyPoolKwargs:
    """Test suite for to_sqlalchemy_pool_kwargs method."""

    def test_empty_kwargs_with_defaults(self) -> None:
        """Test returns empty dict when all pool settings are default/zero."""
        conn = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            username="root",
            database="test",
        )
        kwargs = conn.to_sqlalchemy_pool_kwargs()
        # Only max_idle_conns has non-zero default (2)
        assert kwargs == {"pool_size": 2}

    def test_pool_size_from_max_idle_conns(self) -> None:
        """Test pool_size is set from max_idle_conns."""
        conn = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            username="root",
            database="test",
            max_idle_conns=10,
        )
        kwargs = conn.to_sqlalchemy_pool_kwargs()
        assert kwargs["pool_size"] == 10

    def test_max_overflow_calculated(self) -> None:
        """Test max_overflow is calculated from max_open_conns - pool_size."""
        conn = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            username="root",
            database="test",
            max_idle_conns=5,
            max_open_conns=20,
        )
        kwargs = conn.to_sqlalchemy_pool_kwargs()
        assert kwargs["pool_size"] == 5
        assert kwargs["max_overflow"] == 15  # 20 - 5

    def test_max_overflow_with_default_pool_size(self) -> None:
        """Test max_overflow uses default pool_size (5) when not specified."""
        conn = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            username="root",
            database="test",
            max_idle_conns=0,  # Override default
            max_open_conns=10,
        )
        kwargs = conn.to_sqlalchemy_pool_kwargs()
        assert kwargs["max_overflow"] == 5  # 10 - 5 (default)

    def test_pool_recycle_from_lifetime(self) -> None:
        """Test pool_recycle is set from conn_max_lifetime_minutes in seconds."""
        conn = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            username="root",
            database="test",
            conn_max_lifetime_minutes=10,
        )
        kwargs = conn.to_sqlalchemy_pool_kwargs()
        assert kwargs["pool_recycle"] == 600  # 10 * 60

    def test_pool_timeout_from_idle_time(self) -> None:
        """Test pool_timeout is set from conn_max_idle_time_minutes in seconds."""
        conn = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            username="root",
            database="test",
            conn_max_idle_time_minutes=5,
        )
        kwargs = conn.to_sqlalchemy_pool_kwargs()
        assert kwargs["pool_timeout"] == 300  # 5 * 60

    def test_all_pool_settings(self) -> None:
        """Test all pool settings together."""
        conn = MySQLConfig(
            engine="mysql",
            endpoint="localhost",
            username="root",
            database="test",
            max_idle_conns=5,
            max_open_conns=25,
            conn_max_lifetime_minutes=15,
            conn_max_idle_time_minutes=3,
        )
        kwargs = conn.to_sqlalchemy_pool_kwargs()
        assert kwargs["pool_size"] == 5
        assert kwargs["max_overflow"] == 20  # 25 - 5
        assert kwargs["pool_recycle"] == 900  # 15 * 60
        assert kwargs["pool_timeout"] == 180  # 3 * 60
