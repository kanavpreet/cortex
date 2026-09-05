"""collapse_ghe_into_single_pull_requests_table

Collapse the GHE org/repo/PR schema into a single denormalized
``ghe_pull_requests`` table keyed on the globally-unique GitHub
``pull_request_id``. The ``ghe_organizations`` and ``ghe_repositories`` tables
are removed and their identity/slug columns (org_id, org_login, repo_id,
repo_name) are stored inline on the PR row, with a unique constraint on
(org_id, repo_id, pull_request_number).

Existing GHE PR rows are NOT backfilled — the historian re-populates them on its
next crawl — so this drops and recreates ``ghe_pull_requests`` rather than
altering it in place (the primary key changes from the surrogate ``id`` to
``pull_request_id``). ``ghe_pr_tracker`` is unchanged.

Revision ID: e2850d8010ac
Revises: e7795d51175b
Create Date: 2026-06-10 08:50:47.220744+00:00

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision = "e2850d8010ac"
down_revision = "e7795d51175b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old GHE tables (surrogate-id PKs, FK-by-surrogate-id). DROP TABLE
    # removes their indexes/constraints implicitly, and IF EXISTS keeps this
    # robust against partial DB states. No FK constraints exist between these
    # tables, so drop order is unconstrained.
    op.execute("DROP TABLE IF EXISTS `ghe_pull_requests`")
    op.execute("DROP TABLE IF EXISTS `ghe_repositories`")
    op.execute("DROP TABLE IF EXISTS `ghe_organizations`")

    # Recreate ghe_pull_requests as a single denormalized table.
    op.create_table(
        "ghe_pull_requests",
        sa.Column("pull_request_id", sa.BigInteger(), nullable=False),
        sa.Column("pull_request_number", sa.BigInteger(), nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("org_login", sa.String(length=255), nullable=False),
        sa.Column("repo_id", sa.BigInteger(), nullable=False),
        sa.Column("repo_name", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("merged", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(length=50), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("merged_at", sa.DateTime(), nullable=True),
        sa.Column("target_branch_name", sa.String(length=255), nullable=False),
        sa.Column("pull_request_summary", sa.Text(), nullable=True),
        sa.Column("jira_tcmr_id", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("environment", sa.Text(), nullable=True),
        sa.Column("description_hash", sa.String(length=64), nullable=True),
        sa.Column("last_updated_at", sa.DateTime(), nullable=True),
        sa.Column("services", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("pull_request_id"),
        sa.UniqueConstraint(
            "org_id",
            "repo_id",
            "pull_request_number",
            name="uq_ghe_pr_org_repo_number",
        ),
    )
    op.create_index(
        op.f("ix_ghe_pull_requests_repo_id"),
        "ghe_pull_requests",
        ["repo_id"],
        unique=False,
    )
    op.create_index(
        "ix_ghe_pr_org_repo_number",
        "ghe_pull_requests",
        ["org_login", "repo_name", "pull_request_number"],
        unique=False,
    )


def downgrade() -> None:
    # Reverse the collapse: drop the single table and recreate the original
    # ghe_organizations / ghe_repositories / ghe_pull_requests schema exactly as
    # the initial migration defined it. DROP TABLE IF EXISTS removes the table's
    # indexes implicitly and tolerates a partial DB state.
    op.execute("DROP TABLE IF EXISTS `ghe_pull_requests`")

    op.create_table(
        "ghe_organizations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("org", sa.String(length=255), nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org"),
        sa.UniqueConstraint("org_id"),
    )
    op.create_table(
        "ghe_repositories",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("repo_id", sa.BigInteger(), nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("private", sa.Boolean(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_id", "org_id", name="uq_ghe_repo_id_org_id"),
    )
    op.create_index(
        op.f("ix_ghe_repositories_org_id"),
        "ghe_repositories",
        ["org_id"],
        unique=False,
    )
    op.create_table(
        "ghe_pull_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pull_request_id", sa.BigInteger(), nullable=False),
        sa.Column("pull_request_number", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("merged", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(length=50), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("merged_at", sa.DateTime(), nullable=True),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("target_branch_name", sa.String(length=255), nullable=False),
        sa.Column("pull_request_summary", sa.Text(), nullable=True),
        sa.Column("jira_tcmr_id", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("environment", sa.Text(), nullable=True),
        sa.Column("description_hash", sa.String(length=64), nullable=True),
        sa.Column("last_updated_at", sa.DateTime(), nullable=True),
        sa.Column("services", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pull_request_id", "repository_id", name="uq_ghe_pr_repo"),
    )
    op.create_index(
        op.f("ix_ghe_pull_requests_repository_id"),
        "ghe_pull_requests",
        ["repository_id"],
        unique=False,
    )
