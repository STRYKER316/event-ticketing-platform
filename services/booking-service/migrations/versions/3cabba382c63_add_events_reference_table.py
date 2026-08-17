"""add events reference table

Revision ID: 3cabba382c63
Revises: 9acd9bb8cc64
Create Date: 2026-08-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '3cabba382c63'
down_revision: Union[str, Sequence[str], None] = '9acd9bb8cc64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Minimal reference row written by ProvisioningConsumer from the same
    # Kafka message that already provisions Ticket rows (decisions-log §22
    # amendment #2) — the only thing booking_db needs event start_time for
    # is enforcing the cancellation cutoff (§22).
    op.create_table(
        'events',
        sa.Column('event_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('event_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('events')
