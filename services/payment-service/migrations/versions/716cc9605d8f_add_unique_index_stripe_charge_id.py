"""add unique index on stripe_charge_id

Revision ID: 716cc9605d8f
Revises: 0c1c541204d5
Create Date: 2026-08-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '716cc9605d8f'
down_revision: Union[str, Sequence[str], None] = '0c1c541204d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # get_by_stripe_charge_id assumes at most one row (scalar_one_or_none())
    # but nothing enforced that — Postgres unique indexes treat NULL as
    # distinct, so not-yet-submitted rows (stripe_charge_id IS NULL) are
    # unaffected.
    op.create_index(op.f('ix_payments_stripe_charge_id'), 'payments', ['stripe_charge_id'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_payments_stripe_charge_id'), table_name='payments')
