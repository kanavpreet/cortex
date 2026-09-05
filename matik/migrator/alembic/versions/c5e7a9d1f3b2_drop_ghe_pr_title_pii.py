"""drop_ghe_pr_title_pii

Drop the ``title`` column from ``ghe_pull_requests``. The PR title was found to
contain PII, so the verbatim value is removed from the catalog. The title is
still folded into the summarization input (see build_ghe_pr_content), which is
intentionally left unchanged per the scoped decision.

Revision ID: c5e7a9d1f3b2
Revises: b8d2f3a1c4e6
Create Date: 2026-06-30 00:00:00.000000+00:00

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision = "c5e7a9d1f3b2"
down_revision = "b8d2f3a1c4e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("ghe_pull_requests", "title")


def downgrade() -> None:
    # Best-effort restore of the column shape only; the dropped PII values are
    # not (and must not be) recovered. server_default="" satisfies NOT NULL for
    # any existing rows.
    op.add_column(
        "ghe_pull_requests",
        sa.Column(
            "title",
            sa.String(length=500),
            nullable=False,
            server_default="",
        ),
    )
