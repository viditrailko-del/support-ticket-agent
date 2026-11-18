"""Ticket intake: store the email exactly as received, match the sender to a customer, classify it.

The customer match uses the sender address only (rule 1). Nothing in the subject or body, and nothing the
model writes later, can change which customer's data the tools see.

Status flow: received -> classified -> proposed -> auto_approved | needs_human | rejected (app/guardrails.py),
auto_approved -> executed (app/execution). Any step before the checks can end `failed` with a
`failure_reason`, never an HTTP 500 (rule 9). Human review is in app/reviews.py.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.agent.classify import classify_email
from app.agent.client import FunctionCall, LLMClient
from app.agent.loop import TicketInput, run_agent
from app.agent.structured import Attempt
from app.config import Settings
from app.db.models import AgentStep, Customer, Ticket
from app.execution import execute_ticket
from app.guardrails import guard_ticket
from app.logging_setup import log_event
from app.policies import load_policies
from app.tools import ToolContext, run_tool

log = logging.getLogger(__name__)


class StepRecorder:
    """Writes each LLM call as an agent_steps row and commits at once: a crash later still leaves the raw
    model output in the database (rule 5)."""

    def __init__(self, db: Session, ticket: Ticket, kind: str) -> None:
        self.db, self.ticket, self.kind = db, ticket, kind
        self._rows: dict[int, AgentStep] = {}

    def _next_seq(self) -> int:
        return (self.db.scalar(select(func.max(AgentStep.seq)).where(AgentStep.ticket_id == self.ticket.id)) or 0) + 1

    def record(self, attempt: Attempt) -> None:
        row = AgentStep(
            ticket_id=self.ticket.id, seq=self._next_seq(), kind=self.kind, model=attempt.model,
            prompt_version=attempt.prompt_version, raw_output=attempt.raw_output, error=attempt.error,
            latency_ms=attempt.latency_ms, input_tokens=attempt.input_tokens, output_tokens=attempt.output_tokens,
            thinking_tokens=attempt.thinking_tokens, cost_usd=attempt.cost_usd,
        )
        self.db.add(row)
        self.db.commit()
        self._rows[id(attempt)] = row

    def update(self, attempt: Attempt) -> None:
        self._rows[id(attempt)].error = attempt.error  # only the error changes; raw_output is never rewritten
        self.db.commit()

    def record_tool(self, call: FunctionCall, result: dict, latency_ms: int) -> None:
        """A tool call exactly as the model wrote it, and what our code returned, committed before the model
        sees the result."""
        self.db.add(AgentStep(ticket_id=self.ticket.id, seq=self._next_seq(), kind="tool", tool_name=call.name,
                              tool_args=call.args, tool_result=result, latency_ms=latency_ms))
        self.db.commit()


def match_customer(db: Session, from_email: str) -> str | None:
    return db.scalar(select(Customer.id).where(Customer.email == func.lower(from_email.strip())))


def create_ticket(
    db: Session, *, from_email: str, subject: str, body: str, received_at: datetime | None = None
) -> Ticket:
    ticket = Ticket(
        from_email=from_email, subject=subject, body=body,
        received_at=received_at or datetime.now(UTC),
        customer_id=match_customer(db, from_email),
        status="received",
    )
    db.add(ticket)
    db.commit()  # the email is on record before any slow or failing work
    log_event(log, logging.INFO, "ticket_received", ticket_id=ticket.id, customer_matched=ticket.customer_id is not None)
    return ticket


def classify_ticket(
    db: Session, ticket: Ticket, llm: LLMClient, settings: Settings, sleep: Callable[[float], None] = time.sleep
) -> Ticket:
    outcome = classify_email(
        from_email=ticket.from_email, subject=ticket.subject, body=ticket.body,
        llm=llm, settings=settings, recorder=StepRecorder(db, ticket, "classify"), sleep=sleep,
    )
    if outcome.parsed is None:
        ticket.status, ticket.failure_reason = "failed", "classification_failed"
    else:
        ticket.category = outcome.parsed.category.value
        ticket.urgency = outcome.parsed.urgency.value
        ticket.status = "classified"
    db.commit()
    log_event(log, logging.INFO, "ticket_classified", ticket_id=ticket.id, status=ticket.status,
              category=ticket.category, urgency=ticket.urgency, llm_calls=len(outcome.attempts))
    return ticket


def propose_for_ticket(
    db: Session, ticket: Ticket, llm: LLMClient, settings: Settings, sleep: Callable[[float], None] = time.sleep
) -> Ticket:
    """Run the agent on a classified ticket. The proposal is stored; nothing is executed."""
    if ticket.status != "classified":
        return ticket
    policies = load_policies(db)
    ticket.prompt_version, ticket.policy_version = settings.agent_prompt_version, policies.version
    db.commit()  # what the agent was given is on record before it runs
    ctx = ToolContext.for_ticket(db, ticket)
    outcome = run_agent(
        TicketInput(from_email=ticket.from_email, subject=ticket.subject, body=ticket.body,
                    received_on=ctx.today, category=ticket.category, urgency=ticket.urgency),
        policies_text=policies.text,
        run_tool=lambda name, args: run_tool(ctx, name, args),
        llm=llm, settings=settings, recorder=StepRecorder(db, ticket, "model"), sleep=sleep,
    )
    if outcome.proposal is None:
        ticket.status, ticket.failure_reason = "failed", outcome.failure_reason
    else:
        ticket.proposal = outcome.proposal.model_dump(mode="json")
        ticket.status = "proposed"
    db.commit()
    log_event(log, logging.INFO, "ticket_proposed", ticket_id=ticket.id, status=ticket.status,
              action=outcome.proposal.action.value if outcome.proposal else None,
              failure_reason=ticket.failure_reason, model_calls=len(outcome.attempts), tool_calls=outcome.tool_calls)
    return ticket


def receive_ticket(
    db: Session, *, from_email: str, subject: str, body: str, received_at: datetime | None,
    llm: LLMClient, settings: Settings,
) -> Ticket:
    """POST /tickets: store, classify, run the agent, run the checks, and execute only if auto-approved."""
    started = time.monotonic()
    ticket = create_ticket(db, from_email=from_email, subject=subject, body=body, received_at=received_at)
    classify_ticket(db, ticket, llm, settings)
    propose_for_ticket(db, ticket, llm, settings)
    guard_ticket(db, ticket, settings)
    if ticket.status == "auto_approved":
        execute_ticket(db, ticket, settings, approved_by="auto")
    done = get_ticket(db, ticket.id)
    log_ticket_finished(done, latency_ms=int((time.monotonic() - started) * 1000))  # type: ignore[arg-type]
    return done  # type: ignore[return-value]


def log_ticket_finished(t: Ticket, *, latency_ms: int) -> None:
    """One summary event per ticket: everything a dashboard needs (outcome, flags, calls, tokens, cost,
    latency), and nothing the customer wrote. The email stays in the DB, out of the log stream."""
    model_steps = [s for s in t.steps if s.kind != "tool"]
    costs = [s.cost_usd for s in model_steps if s.cost_usd is not None]
    log_event(
        log, logging.INFO, "ticket_finished", ticket_id=t.id, status=t.status, decision=t.decision,
        failure_reason=t.failure_reason, category=t.category, urgency=t.urgency,
        action=(t.proposal or {}).get("action"), amount_cents=t.amount_cents,
        flags=[f["code"] for f in t.flags or []], executed=any(a.status == "executed" for a in t.actions),
        model_calls=len(model_steps), tool_calls=len(t.steps) - len(model_steps),
        llm_errors=sum(1 for s in model_steps if s.error),
        input_tokens=sum(s.input_tokens or 0 for s in model_steps),
        output_tokens=sum(s.output_tokens or 0 for s in model_steps),
        cost_usd=sum(costs, Decimal(0)) if len(costs) == sum(s.raw_output is not None for s in model_steps) else None,
        models=sorted({s.model for s in model_steps if s.model}),
        prompt_version=t.prompt_version, policy_version=t.policy_version, latency_ms=latency_ms,
    )


def get_ticket(db: Session, ticket_id: uuid.UUID) -> Ticket | None:
    return db.scalar(
        select(Ticket)
        .where(Ticket.id == ticket_id)
        .options(selectinload(Ticket.steps), selectinload(Ticket.actions), selectinload(Ticket.reviews))
        .execution_options(populate_existing=True)
    )
