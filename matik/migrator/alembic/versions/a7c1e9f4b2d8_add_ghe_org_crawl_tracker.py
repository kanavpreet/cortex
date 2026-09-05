"""add_ghe_org_crawl_tracker

Add the ``ghe_org_crawl_tracker`` table used to skip dormant repositories. Each
row records the start time of the last fully-successful crawl for a GitHub
organization (keyed by ``org_id``); the historian only re-processes repos pushed
on or after that time on the next run.

Revision ID: a7c1e9f4b2d8
Revises: e2850d8010ac
Create Date: 2026-06-19 00:00:00.000000+00:00

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision = "a7c1e9f4b2d8"
down_revision = "e2850d8010ac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ghe_org_crawl_tracker",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("last_crawled_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", name="uq_ghe_org_crawl_tracker_org_id"),
    )
    op.create_index(
        op.f("ix_ghe_org_crawl_tracker_last_crawled_at"),
        "ghe_org_crawl_tracker",
        ["last_crawled_at"],
        unique=False,
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS `ghe_org_crawl_tracker`")
