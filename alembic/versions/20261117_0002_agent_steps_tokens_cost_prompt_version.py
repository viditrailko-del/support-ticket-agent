"""agent_steps tokens, cost, prompt version

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-17 05:32:05.811208

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agent_steps', sa.Column('prompt_version', sa.String(length=40), nullable=True))
    op.add_column('agent_steps', sa.Column('input_tokens', sa.Integer(), nullable=True))
    op.add_column('agent_steps', sa.Column('output_tokens', sa.Integer(), nullable=True))
    op.add_column('agent_steps', sa.Column('thinking_tokens', sa.Integer(), nullable=True))
    op.add_column('agent_steps', sa.Column('cost_usd', sa.Numeric(precision=10, scale=6), nullable=True))


def downgrade() -> None:
    op.drop_column('agent_steps', 'cost_usd')
    op.drop_column('agent_steps', 'thinking_tokens')
    op.drop_column('agent_steps', 'output_tokens')
    op.drop_column('agent_steps', 'input_tokens')
    op.drop_column('agent_steps', 'prompt_version')
