"""rename ghe_pull_requests.jira_tcmr_id to jira_tcmr_key

Repurpose the unused ``jira_tcmr_id`` column (an always-NULL numeric FK to
``jira_issues.id``) as ``jira_tcmr_key`` — a VARCHAR holding the JIRA TCMR issue
key (e.g. ``TCMR-12345``) parsed from PR content. The column has never been
populated, so the rename + type change needs no data backfill.

Revision ID: b4f2c1a9d3e7
Revises: c5e7a9d1f3b2
Create Date: 2026-07-01 00:00:00.000000+00:00

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision = "b4f2c1a9d3e7"
down_revision = "c5e7a9d1f3b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "ghe_pull_requests",
        "jira_tcmr_id",
        new_column_name="jira_tcmr_key",
        existing_type=sa.BigInteger(),
        type_=sa.String(length=255),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "ghe_pull_requests",
        "jira_tcmr_key",
        new_column_name="jira_tcmr_id",
        existing_type=sa.String(length=255),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )
