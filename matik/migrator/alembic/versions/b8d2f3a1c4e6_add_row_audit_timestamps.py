"""add_row_audit_timestamps

Add DB-managed ``row_created_at`` / ``row_updated_at`` audit columns to every
table that lacks true row-level write tracking. These record when a row was
written and last updated in our DB (to help with debugging), and are distinct
from any upstream/domain ``created_at``/``updated_at``/``last_updated_at``
columns sourced from external systems. Values are populated entirely by MySQL
via ``CURRENT_TIMESTAMP`` / ``ON UPDATE CURRENT_TIMESTAMP`` defaults, so no
application code sets them.

See https://git.musta.ch/airbnb/matik/issues/374.

Revision ID: b8d2f3a1c4e6
Revises: a7c1e9f4b2d8
Create Date: 2026-06-30 00:00:00.000000+00:00

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision = "b8d2f3a1c4e6"
down_revision = "a7c1e9f4b2d8"
branch_labels = None
depends_on = None

# Tables that lack genuine row-level audit timestamps. ghe_pr_tracker and
# ghe_org_crawl_tracker are intentionally excluded — they already maintain
# row-level created_at/updated_at populated by their DAOs.
_TABLES = (
    "incidentio_incidents",
    "incidentio_tracker",
    "greenroom_entities",
    "jira_issues",
    "jira_batch_tracker",
    "ghe_pull_requests",
    "reliability_correlation_groups",
    "reliability_correlations",
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "row_created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "row_updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            ),
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "row_updated_at")
        op.drop_column(table, "row_created_at")
