"""Alembic environment configuration."""

import os

import boto3
from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Engine

from common.config import load_config
from common.models import metadata
from common.models.mysql_config import MySQLConfig
from common.utils import log_utils
from common.utils.db_utils import create_short_lived_engine
from common.utils.env_utils import is_local_environment
from common.utils.iam_utils import assume_role

logger = log_utils.get_logger(__name__)

# Alembic Config object
alembic_config = context.config

# Note: Logging is configured in main.py via log_utils.configure()
# Do NOT call fileConfig() here as it would override our structured logging setup

# SQLAlchemy MetaData object (for autogenerate support)
target_metadata = metadata


def get_mysql_config() -> tuple[MySQLConfig, bool]:
    """
    Load database config from Matik config file.

    Returns:
        Tuple of (MySQLConfig, is_local) where is_local indicates local environment.
    """
    config_path = "/app/config/matik-migrator-config.yml"
    try:
        matik_config = load_config(config_path)
        if matik_config.mysql is None:
            raise ValueError("MySQL configuration not found in config file")
        is_local = is_local_environment(matik_config.common)
        return MySQLConfig.model_validate(matik_config.mysql), is_local
    except Exception as e:
        # check if env vars are set
        print(
            f"Warning: Failed to load DB config from {config_path}: {e}. "
            "Falling back to environment variables."
        )

        # Load config from env var path if default fails (assume local)
        return MySQLConfig(
            engine=os.environ.get("MYSQL_ENGINE", "mysql"),
            endpoint=os.environ.get("MYSQL_HOST", "localhost"),
            username=os.environ.get("MYSQL_USER", ""),
            password=os.environ.get("MYSQL_PASSWORD", ""),
            database=os.environ.get("MYSQL_DATABASE", "database"),
        ), True


def get_db_session(mysql_config: MySQLConfig, is_local: bool) -> boto3.Session | None:
    """
    Get boto3 session with chained role assumption if configured.

    - If is_local: returns None (no IAM auth needed)
    - If airbnbprod_role_arn and corpinfra_role_arn: chains AIRBNB-PROD → CORPINFRA-PROD
    - If only corpinfra_role_arn: assumes CORPINFRA-PROD directly
    - If neither: returns None (db_utils will use default credentials)
    """
    if is_local or not mysql_config.use_iam:
        return None

    if mysql_config.airbnbprod_role_arn and mysql_config.corpinfra_role_arn:
        airbnb_session = assume_role(
            mysql_config.airbnbprod_role_arn, region=mysql_config.region
        )
        return assume_role(
            mysql_config.corpinfra_role_arn,
            region=mysql_config.region,
            session=airbnb_session,
        )
    elif mysql_config.corpinfra_role_arn:
        return assume_role(mysql_config.corpinfra_role_arn, region=mysql_config.region)
    return None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    # Offline mode generates SQL without connecting - use engine to get URL
    mysql_config, is_local = get_mysql_config()
    session = get_db_session(mysql_config, is_local)
    engine = create_short_lived_engine(
        mysql_config, session=session, poolclass=pool.NullPool
    )

    try:
        context.configure(
            url=str(engine.url),
            target_metadata=target_metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
        )

        with context.begin_transaction():
            context.run_migrations()
    finally:
        engine.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    mysql_config, is_local = get_mysql_config()
    logger.info(
        "connecting to database",
        endpoint=mysql_config.endpoint,
        database=mysql_config.database,
        username=mysql_config.username,
        use_iam=mysql_config.use_iam,
        has_password=bool(mysql_config.password),
    )
    session = get_db_session(mysql_config, is_local)
    connectable: Engine = create_short_lived_engine(
        mysql_config, session=session, poolclass=pool.NullPool
    )

    try:
        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)

            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
