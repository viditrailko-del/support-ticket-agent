"""Run every check and turn the flags into a decision. Pure (rule 6).

Approval policy (internal, rule 13):
  rejected       any `reject` flag: the action is impossible (not the sender's order, nothing left to refund...)
  needs_human    any `review` flag, or the agent escalated
  auto_approved  zero blocking flags, and refund / replacement worth less than the auto limit; reply_only
`info` flags never change the decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.checks.facts import Facts, Flag, Severity
from app.checks.rules import CHECKS, Limits, check_auto_limit, compute_amount
from app.schemas.proposal import ActionType, Proposal


class Decision(StrEnum):
    auto_approved = "auto_approved"
    needs_human = "needs_human"
    rejected = "rejected"
    failed = "failed"


@dataclass(frozen=True)
class CheckResult:
    flags: tuple[Flag, ...]
    decision: Decision
    amount_cents: int | None  # the refund amount computed by code (None for other actions)

    @property
    def flags_json(self) -> list[dict[str, str]]:
        return [f.as_json() for f in self.flags]


def run_checks(p: Proposal, facts: Facts, limits: Limits) -> CheckResult:
    flags = [flag for check in CHECKS for flag in check(p, facts)]
    flags += check_auto_limit(p, facts, limits)
    amount = None
    if p.action is ActionType.refund and facts.order is not None:
        amount = compute_amount(p, facts.order).payable_cents
    return CheckResult(tuple(flags), decide(p, flags), amount)


def decide(p: Proposal, flags: list[Flag]) -> Decision:
    severities = {f.severity for f in flags}
    if Severity.reject in severities:
        return Decision.rejected
    if Severity.review in severities or p.action is ActionType.escalate:
        return Decision.needs_human
    return Decision.auto_approved
