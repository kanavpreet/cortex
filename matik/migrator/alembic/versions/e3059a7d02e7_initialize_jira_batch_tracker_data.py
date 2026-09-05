"""initialize_jira_batch_tracker_data

Revision ID: e3059a7d02e7
Revises: 616f7b7e3e44
Create Date: 2026-02-10

Initialize jira_batch_tracker table with starting values for each ticket type.
NULL status indicates this is the first batch to process.
"""

from alembic import op

# revision identifiers, used by Alembic
revision = "e3059a7d02e7"
down_revision = "616f7b7e3e44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT IGNORE INTO jira_batch_tracker (
            ticket_type, batch_start, batch_end, window_days, status, updated_at
        )
        VALUES
            ('tcmr', '2026-01-01 00:00:00', '2026-01-15 00:00:00', 14, NULL, NOW()),
            ('operational', '2026-01-01 00:00:00', '2026-01-15 00:00:00', 14, NULL, NOW())
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM jira_batch_tracker
        WHERE ticket_type IN ('tcmr', 'operational')
        AND batch_start = '2026-01-01 00:00:00'
        AND batch_end = '2026-01-15 00:00:00'
        """
    )
