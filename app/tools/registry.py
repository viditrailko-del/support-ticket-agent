"""Tool registry and the one entry point the agent loop uses: run_tool.

run_tool never raises for anything the model can cause (unknown tool, bad arguments): it returns an error
result that is stored and shown to the model like any other result (rule 9).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.db.models import Ticket
from app.schemas.tools import GetCustomerArgs, GetOrderArgs, ListRefundsArgs
from app.tools.shop import get_customer, get_order, list_refunds


@dataclass(frozen=True)
class ToolContext:
    db: Session
    customer_id: str | None  # the ticket's sender, matched when the ticket was stored. None = not a customer.
    today: date  # the ticket's received date: every "days since" is relative to it

    @classmethod
    def for_ticket(cls, db: Session, ticket: Ticket) -> ToolContext:
        return cls(db=db, customer_id=ticket.customer_id, today=ticket.received_at.date())


@dataclass(frozen=True)
class Tool:
    name: str
    args_model: type[BaseModel]
    fn: Callable[[ToolContext, Any], BaseModel]

    @property
    def description(self) -> str:
        return (self.args_model.__doc__ or "").strip()


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool("get_customer", GetCustomerArgs, get_customer),
        Tool("get_order", GetOrderArgs, get_order),
        Tool("list_refunds", ListRefundsArgs, list_refunds),
    )
}


def run_tool(ctx: ToolContext, name: str, raw_args: dict[str, Any] | None) -> dict[str, Any]:
    """Validate the model's arguments, run the tool, return a JSON-ready result (or {"error": ...})."""
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"unknown tool {name!r}; available: {', '.join(TOOLS)}"}
    try:
        args = tool.args_model.model_validate(raw_args or {})
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc'])) or 'args'}: {err['msg']}" for err in e.errors())
        return {"error": f"invalid arguments for {name}: {problems}"}
    return tool.fn(ctx, args).model_dump(mode="json")
