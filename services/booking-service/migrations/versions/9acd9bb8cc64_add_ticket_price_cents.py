"""add ticket price_cents

Revision ID: 9acd9bb8cc64
Revises: 2ab7ccc49f4d
Create Date: 2026-08-17 09:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9acd9bb8cc64'
down_revision: Union[str, Sequence[str], None] = '2ab7ccc49f4d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Organizer-set per section at provisioning time (decisions-log §9/§16
    # amendments, 2026-08-17). A temporary server_default backfills any
    # already-provisioned local-dev rows; application inserts always supply
    # the real value, so the default is dropped immediately after.
    op.add_column('tickets', sa.Column('price_cents', sa.Integer(), nullable=False, server_default='0'))
    op.alter_column('tickets', 'price_cents', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('tickets', 'price_cents')
