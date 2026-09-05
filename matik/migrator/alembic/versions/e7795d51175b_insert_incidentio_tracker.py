"""insert_incidentio_tracker

Revision ID: e7795d51175b
Revises: e3059a7d02e7
Create Date: 2026-02-10

Insert the single-row incidentio_tracker record used by the sync process.
"""

from alembic import op

# revision identifiers, used by Alembic
revision = "e7795d51175b"
down_revision = "e3059a7d02e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT IGNORE INTO incidentio_tracker (id, timestamp, status, initial_sync_complete, last_updated_at_cursor)
        VALUES (1, CURRENT_TIMESTAMP, 'OK', FALSE, NULL)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM incidentio_tracker WHERE id = 1
        """
    )
