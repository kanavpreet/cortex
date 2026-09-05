"""add_incident_channel_summary_columns

Add ``incident_channel_summary`` (LLM-generated) and
``incident_channel_summary_hash`` (change-detection hash) columns to
``incidentio_incidents``.

These back the OpsBot on-demand incident-channel-summary feed: OpsBot posts a
raw Slack-channel summary via a new generic Chronicler webhook. The raw text
is never persisted — it only ever exists in the SQS enrichment-request
message, mirroring how Incident.io's own raw ``summary``/``resolution_statement``
fields are handled — and is re-summarized by the Enricher directly into
``incident_channel_summary``. Named plainly (no ``_llm`` suffix), matching the
existing ``root_cause_summary``/``description_summary`` convention where the
LLM output itself owns the plain column name. Named after the data, not the
producing tool, so the columns survive if OpsBot is replaced. The regular
Incident.io ingestion path never writes these columns (see
``exclude_columns`` in ``common/datasources/incidentio.py``).

See https://jira.airbnb.biz/browse/ITE-797334.

Revision ID: d1e5f8a2b6c9
Revises: b4f2c1a9d3e7
Create Date: 2026-07-16 00:00:00.000000+00:00

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision = "d1e5f8a2b6c9"
down_revision = "b4f2c1a9d3e7"
branch_labels = None
depends_on = None

_TABLE = "incidentio_incidents"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("incident_channel_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column("incident_channel_summary_hash", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, "incident_channel_summary_hash")
    op.drop_column(_TABLE, "incident_channel_summary")
