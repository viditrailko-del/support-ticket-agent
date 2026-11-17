"""shop, policy and ticket tables

Revision ID: 0001
Revises: 
Create Date: 2026-11-17 05:20:49.603098

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('customers',
    sa.Column('id', sa.String(length=10), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('tier', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.Date(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('policy_rules',
    sa.Column('id', sa.String(length=20), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('page', sa.String(length=100), nullable=False),
    sa.Column('page_title', sa.String(length=200), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('orders',
    sa.Column('id', sa.String(length=20), nullable=False),
    sa.Column('customer_id', sa.String(length=10), nullable=False),
    sa.Column('placed_at', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('shipped_at', sa.Date(), nullable=True),
    sa.Column('delivered_at', sa.Date(), nullable=True),
    sa.Column('subtotal_cents', sa.Integer(), nullable=False),
    sa.Column('shipping_cents', sa.Integer(), nullable=False),
    sa.Column('total_paid_cents', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_orders_customer_id'), 'orders', ['customer_id'], unique=False)
    op.create_table('tickets',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('from_email', sa.String(length=320), nullable=False),
    sa.Column('subject', sa.Text(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('customer_id', sa.String(length=10), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('category', sa.String(length=40), nullable=True),
    sa.Column('urgency', sa.String(length=20), nullable=True),
    sa.Column('proposal', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('flags', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), server_default=sa.text("'[]'"), nullable=False),
    sa.Column('decision', sa.String(length=20), nullable=True),
    sa.Column('prompt_version', sa.String(length=20), nullable=True),
    sa.Column('policy_version', sa.String(length=16), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tickets_customer_id'), 'tickets', ['customer_id'], unique=False)
    op.create_index(op.f('ix_tickets_status'), 'tickets', ['status'], unique=False)
    op.create_table('actions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ticket_id', sa.Uuid(), nullable=False),
    sa.Column('type', sa.String(length=20), nullable=False),
    sa.Column('order_id', sa.String(length=20), nullable=True),
    sa.Column('amount_cents', sa.Integer(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=100), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_index(op.f('ix_actions_ticket_id'), 'actions', ['ticket_id'], unique=False)
    op.create_table('agent_steps',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ticket_id', sa.Uuid(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=True),
    sa.Column('raw_output', sa.Text(), nullable=True),
    sa.Column('tool_name', sa.String(length=50), nullable=True),
    sa.Column('tool_args', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('tool_result', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ticket_id', 'seq')
    )
    op.create_index(op.f('ix_agent_steps_ticket_id'), 'agent_steps', ['ticket_id'], unique=False)
    op.create_table('order_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.String(length=20), nullable=False),
    sa.Column('line_no', sa.Integer(), nullable=False),
    sa.Column('sku', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('category', sa.String(length=50), nullable=False),
    sa.Column('qty', sa.Integer(), nullable=False),
    sa.Column('unit_price_cents', sa.Integer(), nullable=False),
    sa.Column('final_sale', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('order_id', 'line_no')
    )
    op.create_index(op.f('ix_order_items_order_id'), 'order_items', ['order_id'], unique=False)
    op.create_table('refunds',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('order_id', sa.String(length=20), nullable=False),
    sa.Column('amount_cents', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(length=40), nullable=False),
    sa.Column('created_at', sa.Date(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_refunds_order_id'), 'refunds', ['order_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_refunds_order_id'), table_name='refunds')
    op.drop_table('refunds')
    op.drop_index(op.f('ix_order_items_order_id'), table_name='order_items')
    op.drop_table('order_items')
    op.drop_index(op.f('ix_agent_steps_ticket_id'), table_name='agent_steps')
    op.drop_table('agent_steps')
    op.drop_index(op.f('ix_actions_ticket_id'), table_name='actions')
    op.drop_table('actions')
    op.drop_index(op.f('ix_tickets_status'), table_name='tickets')
    op.drop_index(op.f('ix_tickets_customer_id'), table_name='tickets')
    op.drop_table('tickets')
    op.drop_index(op.f('ix_orders_customer_id'), table_name='orders')
    op.drop_table('orders')
    op.drop_table('policy_rules')
    op.drop_table('customers')
