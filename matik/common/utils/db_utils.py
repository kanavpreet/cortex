"""Database connection utilities for building SQLAlchemy connection URLs."""

import ssl
import threading
import time
import urllib.parse
from typing import Any

import boto3
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from common.models.mysql_config import MySQLConfig
from common.utils import log_utils
from common.utils.iam_utils import get_session

logger = log_utils.get_logger(__name__)

# =============================================================================
# Internal Classes
# =============================================================================


class _TokenProvider:
    """
    Thread-safe IAM token provider with automatic refresh.

    Internal class used by create_long_lived_engine().
    """

    def __init__(self, mysql_config: MySQLConfig):
        self._config = mysql_config
        self._token: str | None = None
        self._expiry: float = 0
        self._lock = threading.Lock()
        self._session: boto3.Session | None = None
        # Convert config minutes to seconds
        self._token_lifetime = (mysql_config.token_expiry_minutes or 15) * 60
        self._grace_period = (mysql_config.token_expiry_grace_minutes or 1) * 60

    def _get_session(self) -> boto3.Session:
        """Get or create boto3 session with role assumption if configured."""
        if self._session is None:
            logger.info(
                "creating boto3 session",
                role_arn=self._config.corpinfra_role_arn,
                region=self._config.region,
            )
            self._session = get_session(
                role_arn=self._config.corpinfra_role_arn,
                region=self._config.region,
                session_name="matik-rds-access",
            )
            logger.info("boto3 session created successfully")
        return self._session

    def _refresh_token(self) -> str:
        """Generate a new IAM authentication token."""
        logger.info("refreshing IAM auth token")
        session = self._get_session()
        token_endpoint = self._config.canonical_endpoint or self._config.endpoint

        logger.info(
            "generating RDS auth token",
            endpoint=token_endpoint,
            port=self._config.port,
            username=self._config.username,
            region=self._config.region,
        )
        token = generate_rds_auth_token(
            session=session,
            hostname=token_endpoint,
            port=self._config.port,
            username=self._config.username,
            region=self._config.region,
        )
        logger.info("RDS auth token generated successfully")

        self._token = token
        self._expiry = time.time() + self._token_lifetime
        return token

    def _needs_refresh(self) -> bool:
        """Check if token needs refresh (expired or within grace period)."""
        if self._token is None:
            return True
        return time.time() >= (self._expiry - self._grace_period)

    def get_token(self) -> str:
        """Get a valid IAM token, refreshing if needed. Thread-safe."""
        if not self._needs_refresh():
            return self._token  # type: ignore[return-value]

        with self._lock:
            if self._needs_refresh():
                return self._refresh_token()
            return self._token  # type: ignore[return-value]


# =============================================================================
# Internal Functions
# =============================================================================


def _get_ssl_connect_args(mysql_config: MySQLConfig) -> dict[str, Any]:
    """
    Get SSL connect_args for PyMySQL when IAM auth is enabled.

    RDS IAM authentication requires TLS. This returns the connect_args
    dict to pass to SQLAlchemy's create_engine().

    When connecting via VPC endpoint, the canonical_endpoint is used for
    certificate hostname verification instead of the VPC endpoint hostname.

    Args:
        mysql_config: Database configuration

    Returns:
        Dict with SSL settings for connect_args, or empty dict if not needed
    """
    if not mysql_config.use_iam:
        logger.debug("IAM auth disabled, skipping SSL config")
        return {}

    # RDS IAM auth requires TLS
    # Use the RDS CA bundle downloaded in Dockerfile
    rds_ca_bundle = "/usr/local/share/ca-certificates/rds-combined-ca-bundle.crt"

    # When using VPC endpoint, disable hostname checking since the VPC endpoint
    # hostname won't match the RDS certificate. Certificate verification is
    # still enabled against the RDS CA bundle.
    if mysql_config.canonical_endpoint:
        ssl_context = ssl.create_default_context(cafile=rds_ca_bundle)
        ssl_context.check_hostname = False  # VPC endpoint hostname won't match cert
        ssl_context.verify_mode = ssl.CERT_REQUIRED  # Still verify the certificate

        logger.info(
            "SSL config for VPC endpoint (hostname check disabled)",
            ca_path=rds_ca_bundle,
        )
        return {"ssl": ssl_context}

    # Standard connection (not via VPC endpoint)
    ssl_config: dict[str, Any] = {
        "ssl": {
            "ca": rds_ca_bundle,
            "check_hostname": True,
        }
    }
    logger.info("SSL config for IAM auth", ssl_config=ssl_config, ca_path=rds_ca_bundle)
    return ssl_config


# Every connection is pinned to UTC so server-evaluated timestamp expressions —
# notably the DB-managed ``row_created_at`` / ``row_updated_at`` audit columns
# whose DEFAULT is ``CURRENT_TIMESTAMP`` — are written in UTC, matching the
# naive-UTC convention used for all application-managed timestamps. DATETIME
# columns store literal values regardless of the session zone, so existing
# reads/writes are unaffected.
_UTC_SESSION_INIT_COMMAND = "SET time_zone = '+00:00'"


def _get_connect_args(mysql_config: MySQLConfig) -> dict[str, Any]:
    """
    Build PyMySQL connect_args.

    Always pins the session to UTC; additionally adds SSL settings when IAM auth
    is enabled (RDS IAM requires TLS).

    Args:
        mysql_config: Database configuration

    Returns:
        Dict of connect_args for SQLAlchemy's create_engine().
    """
    connect_args: dict[str, Any] = {"init_command": _UTC_SESSION_INIT_COMMAND}
    connect_args.update(_get_ssl_connect_args(mysql_config))
    return connect_args


def generate_rds_auth_token(
    session: boto3.Session,
    hostname: str,
    port: int,
    username: str,
    region: str | None = None,
) -> str:
    """Generate AWS RDS IAM authentication token (valid for 15 minutes)."""
    rds_client = session.client("rds", region_name=region)
    token: str = rds_client.generate_db_auth_token(
        DBHostname=hostname,
        Port=port,
        DBUsername=username,
        Region=region,
    )
    return token


def build_connection_url(
    mysql_config: MySQLConfig, iam_token: str | None = None
) -> str:
    """
    Build SQLAlchemy connection URL for MySQL database.

    Args:
        mysql_config: Database connection configuration
        iam_token: IAM authentication token (required if use_iam is True)

    Returns:
        SQLAlchemy connection URL string

    Raises:
        ValueError: If IAM auth is enabled but no token provided
    """
    if mysql_config.use_iam:
        if not iam_token:
            raise ValueError("IAM authentication enabled but no token provided")
        password = iam_token
    else:
        password = mysql_config.password or ""

    username_encoded = urllib.parse.quote_plus(mysql_config.username)
    password_encoded = urllib.parse.quote_plus(password)

    url = (
        f"mysql+pymysql://{username_encoded}:{password_encoded}"
        f"@{mysql_config.endpoint}:{mysql_config.port}/{mysql_config.database}"
        f"?charset=utf8mb4"
    )

    return url


def _get_short_lived_connection_url(
    mysql_config: MySQLConfig, session: boto3.Session | None = None
) -> str:
    """
    Build connection URL with IAM token if needed.

    Internal helper for create_short_lived_engine().

    Args:
        mysql_config: Database configuration
        session: Optional boto3 session to use. If None and use_iam is True,
                 assumes corpinfra_role_arn.
    """
    if mysql_config.use_iam:
        if session is None:
            session = get_session(
                role_arn=mysql_config.corpinfra_role_arn,
                region=mysql_config.region,
                session_name="matik-rds-access",
            )
        token_endpoint = mysql_config.canonical_endpoint or mysql_config.endpoint
        token = generate_rds_auth_token(
            session=session,
            hostname=token_endpoint,
            port=mysql_config.port,
            username=mysql_config.username,
            region=mysql_config.region,
        )
        return build_connection_url(mysql_config, iam_token=token)
    else:
        return build_connection_url(mysql_config)


# =============================================================================
# Main Functions
# =============================================================================


def create_short_lived_engine(
    mysql_config: MySQLConfig,
    session: boto3.Session | None = None,
    **engine_kwargs: Any,
) -> Engine:
    """
    Create SQLAlchemy engine for short-lived tasks (< 15 minutes).

    Generates a single IAM token and creates an engine with proper SSL/TLS
    configuration. Use this for:
    - Database migrations
    - One-off scripts
    - CLI tools
    - Any task that completes within 15 minutes

    For long-running services, use create_long_lived_engine() instead.

    Args:
        mysql_config: Database connection configuration
        session: Optional boto3 session for IAM auth. If None and use_iam is True,
                 assumes corpinfra_role_arn from mysql_config.
        **engine_kwargs: Optional overrides for SQLAlchemy engine arguments

    Returns:
        SQLAlchemy Engine configured for the database
    """
    url = _get_short_lived_connection_url(mysql_config, session=session)

    # Validate pooled connections at checkout so a stale connection never
    # surfaces as a "2013 Lost connection" error. Callers may override.
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    kwargs.update(engine_kwargs)

    # Pin the session to UTC (for DB-managed timestamp defaults) and add SSL
    # connect_args for IAM auth (RDS IAM requires TLS). Any caller-provided
    # connect_args win.
    connect_args = _get_connect_args(mysql_config)
    connect_args.update(kwargs.get("connect_args", {}))
    kwargs["connect_args"] = connect_args

    return create_engine(url, **kwargs)


def create_long_lived_engine(  # pragma: no cover - requires live DB/SQLAlchemy integration test
    mysql_config: MySQLConfig, **engine_kwargs: Any
) -> Engine:
    """
    Create SQLAlchemy engine for long-running services.

    Automatically refreshes IAM tokens before they expire (15 min lifetime).
    Use this for:
    - Any process that runs longer than 15 minutes

    For short-lived tasks, use get_short_lived_connection_url() instead.

    Pool settings are read from mysql_config by default:
        - max_idle_conns -> pool_size
        - max_open_conns -> pool_size + max_overflow
        - conn_max_lifetime_minutes -> pool_recycle

    Args:
        mysql_config: Database connection configuration
        **engine_kwargs: Optional overrides for SQLAlchemy engine arguments

    Returns:
        SQLAlchemy Engine configured for the database
    """
    logger.info(
        "creating long-lived database engine",
        endpoint=mysql_config.endpoint,
        database=mysql_config.database,
        use_iam=mysql_config.use_iam,
    )

    pool_kwargs = mysql_config.to_sqlalchemy_pool_kwargs()
    # Guard against pymysql "2013 Lost connection" errors from stale pooled
    # connections (RDS/network idle timeout, failover): pool_pre_ping validates
    # and transparently replaces a dead connection at checkout, and pool_recycle
    # retires connections after 10 min so stale ones are never handed out.
    # setdefault keeps explicit per-config overrides (e.g. a configured
    # conn_max_lifetime_minutes -> pool_recycle) winning.
    pool_kwargs.setdefault("pool_pre_ping", True)
    pool_kwargs.setdefault("pool_recycle", 600)
    pool_kwargs.update(engine_kwargs)
    logger.debug("pool kwargs", pool_kwargs=pool_kwargs)

    # Pin the session to UTC (for DB-managed timestamp defaults) and add SSL
    # connect_args for IAM auth (RDS IAM requires TLS). Any caller-provided
    # connect_args win.
    connect_args = _get_connect_args(mysql_config)
    connect_args.update(pool_kwargs.get("connect_args", {}))
    pool_kwargs["connect_args"] = connect_args

    if not mysql_config.use_iam:
        logger.info("creating engine without IAM auth")
        url = build_connection_url(mysql_config)
        return create_engine(url, **pool_kwargs)

    # IAM auth - need token refresh on connection checkout
    logger.info("creating engine with IAM auth")
    provider = _TokenProvider(mysql_config)
    initial_token = provider.get_token()
    logger.info("initial IAM token obtained")
    url = build_connection_url(mysql_config, iam_token=initial_token)
    logger.info("creating SQLAlchemy engine")
    engine = create_engine(url, **pool_kwargs)
    logger.info("SQLAlchemy engine created")

    @event.listens_for(engine, "checkout")
    def _refresh_token_on_checkout(
        dbapi_conn: Any, _connection_record: Any, _connection_proxy: Any
    ) -> None:
        """Refresh IAM token before each connection checkout from pool."""
        token = provider.get_token()
        if hasattr(dbapi_conn, "_auth_plugin_map"):
            dbapi_conn.password = token

    return engine
