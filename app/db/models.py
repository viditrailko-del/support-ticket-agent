from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class of every table. Alembic autogenerate and the test DB (`create_all`) read its
    metadata. No tables yet: customers, orders, refunds, tickets, agent_steps, actions come in Phase 1."""
