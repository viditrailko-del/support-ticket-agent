"""tickets failure_reason

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-17 06:20:19.435624

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tickets', sa.Column('failure_reason', sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column('tickets', 'failure_reason')
