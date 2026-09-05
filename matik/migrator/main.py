"""Migrator service entry point"""

import argparse
import os
import sys

from alembic import command
from alembic.config import Config

from common.config import load_config
from common.utils import log_utils

logger = log_utils.get_logger(__name__)


def get_alembic_config() -> Config:
    """Create and configure Alembic config."""
    migrator_dir = os.path.dirname(os.path.abspath(__file__))
    alembic_ini = os.path.join(migrator_dir, "alembic.ini")
    alembic_cfg = Config(alembic_ini)

    # Prevent Alembic from reconfiguring logging
    alembic_cfg.attributes["configure_logger"] = False

    # Set script location relative to migrator directory
    alembic_cfg.set_main_option(
        "script_location", os.path.join(migrator_dir, "alembic")
    )

    return alembic_cfg


def setup_logging(level: str, environment: str) -> None:
    """Configure logging for the migrator."""
    log_utils.configure(level=level, environment=environment)


def run_upgrade(alembic_cfg: Config, revision: str) -> None:
    """Run upgrade migrations."""
    logger.info("upgrading database to revision", revision=revision)
    command.upgrade(alembic_cfg, revision)
    logger.info("upgrade completed successfully")


def run_downgrade(alembic_cfg: Config, revision: str) -> None:
    """Run downgrade migrations."""
    logger.info("downgrading database to revision", revision=revision)
    command.downgrade(alembic_cfg, revision)
    logger.info("downgrade completed successfully")


def show_current(alembic_cfg: Config) -> None:
    """Show current revision."""
    command.current(alembic_cfg, verbose=True)


def show_history(alembic_cfg: Config) -> None:
    """Show migration history."""
    command.history(alembic_cfg, verbose=True)


def run_stamp(alembic_cfg: Config, revision: str) -> None:
    """Stamp the database with a revision without running migrations."""
    logger.info("stamping database with revision", revision=revision)
    command.stamp(alembic_cfg, revision)
    logger.info("stamp completed successfully")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Database migration tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m migrator                  # Upgrade to head (default)
  python -m migrator upgrade          # Upgrade to head
  python -m migrator upgrade abc123   # Upgrade to specific revision
  python -m migrator downgrade -1     # Downgrade by 1 revision
  python -m migrator downgrade base   # Downgrade to base (empty)
  python -m migrator current          # Show current revision
  python -m migrator history          # Show migration history
  python -m migrator stamp head       # Mark DB at head without running migrations
  python -m migrator stamp abc123     # Mark DB at specific revision
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Migration command")

    # upgrade command
    upgrade_parser = subparsers.add_parser("upgrade", help="Upgrade database schema")
    upgrade_parser.add_argument(
        "revision",
        nargs="?",
        default="head",
        help="Target revision (default: head)",
    )

    # downgrade command
    downgrade_parser = subparsers.add_parser(
        "downgrade", help="Downgrade database schema"
    )
    downgrade_parser.add_argument(
        "revision",
        help="Target revision (e.g., -1, -2, base, or revision id)",
    )

    # current command
    subparsers.add_parser("current", help="Show current revision")

    # history command
    subparsers.add_parser("history", help="Show migration history")

    # stamp command
    stamp_parser = subparsers.add_parser(
        "stamp", help="Stamp database with revision (no migrations run)"
    )
    stamp_parser.add_argument(
        "revision",
        help="Revision to stamp (e.g., head, base, or revision id)",
    )

    return parser.parse_args()


def main() -> None:
    """Run database migrations."""
    args = parse_args()

    # Load configuration
    config = load_config("matik-migrator-config.yml")

    # Setup Logging
    setup_logging(config.common.log_level, config.common.environment)

    alembic_cfg = get_alembic_config()

    try:
        if args.command is None or args.command == "upgrade":
            revision = getattr(args, "revision", "head")
            run_upgrade(alembic_cfg, revision)
        elif args.command == "downgrade":
            run_downgrade(alembic_cfg, args.revision)
        elif args.command == "current":
            show_current(alembic_cfg)
        elif args.command == "history":
            show_history(alembic_cfg)
        elif args.command == "stamp":
            run_stamp(alembic_cfg, args.revision)
    except Exception:
        logger.exception("migration failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
