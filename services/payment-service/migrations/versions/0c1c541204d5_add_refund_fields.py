"""add refund fields

Revision ID: 0c1c541204d5
Revises: afbcf34047ba
Create Date: 2026-08-17 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0c1c541204d5'
down_revision: Union[str, Sequence[str], None] = 'afbcf34047ba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ALTER TYPE ... ADD VALUE cannot run inside the same transaction as a
    # statement that uses the new value (Postgres restriction) — run it in
    # its own autocommit block, separate from the column addition below.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE paymentstatus ADD VALUE IF NOT EXISTS 'REFUNDED'")

    # Resubmission gate for refund_payment (§22), same shape as
    # stripe_charge_id's own "NULL means not yet submitted" role.
    op.add_column('payments', sa.Column('stripe_refund_id', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Postgres has no ALTER TYPE ... DROP VALUE — removing an enum value
    # requires rebuilding the type, not worth it for a downgrade path this
    # project doesn't actually exercise. Column removal is still safe.
    op.drop_column('payments', 'stripe_refund_id')
