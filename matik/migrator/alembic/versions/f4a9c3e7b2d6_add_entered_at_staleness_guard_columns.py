"""add_entered_at_staleness_guard_columns

Add one ``*_entered_at`` timestamp column per write-group to the tables with
demonstrated base+enrichment race risk: ``incidentio_incidents`` (three
write-groups: ``incidentio`` base, ``incidentio`` enrichment,
``incident_channel_summary`` enrichment), ``ghe_pull_requests`` (base +
enrichment), and ``jira_issues`` (base + enrichment).

These back the DLQ retry staleness guard: each column records when the
*winning* message for that write-group entered Matik, so a DLQ-redriven
message carrying stale content can be detected and skipped instead of
clobbering a row that fresher data already updated. Distinct from
``row_updated_at`` (DB-managed, reflects when MySQL last touched the row, not
which message won) added in ``b8d2f3a1c4e6``. Nullable and app-populated (no
server default) — a NULL value means "no guard applied yet" and falls back to
an unconditional write for backward compatibility with messages that predate
this column.

See ADR 024-dlq-retry-staleness-guard.md.

Revision ID: f4a9c3e7b2d6
Revises: d1e5f8a2b6c9
Create Date: 2026-07-22 00:00:00.000000+00:00

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision = "f4a9c3e7b2d6"
down_revision = "d1e5f8a2b6c9"
branch_labels = None
depends_on = None

# table -> ordered list of entered_at columns to add for that table's write-groups
_TABLE_COLUMNS = (
    (
        "incidentio_incidents",
        (
            "incidentio_base_entered_at",
            "incidentio_enrichment_entered_at",
            "incident_channel_summary_entered_at",
        ),
    ),
    (
        "ghe_pull_requests",
        (
            "ghe_pr_base_entered_at",
            "ghe_pr_enrichment_entered_at",
        ),
    ),
    (
        "jira_issues",
        (
            "jira_base_entered_at",
            "jira_enrichment_entered_at",
        ),
    ),
)


def upgrade() -> None:
    for table, columns in _TABLE_COLUMNS:
        for column in columns:
            op.add_column(table, sa.Column(column, sa.DateTime(), nullable=True))


def downgrade() -> None:
    for table, columns in reversed(_TABLE_COLUMNS):
        for column in reversed(columns):
            op.drop_column(table, column)
