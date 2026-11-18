"""Guardrails: pure checks over (proposal, facts). The agent proposes; these decide what may happen."""

from app.checks.decide import CheckResult, Decision, decide, run_checks
from app.checks.facts import Facts, Flag, LineFacts, OrderFacts, Severity
from app.checks.rules import Limits, compute_amount, injection_signals

__all__ = [
    "CheckResult", "Decision", "Facts", "Flag", "LineFacts", "Limits", "OrderFacts", "Severity",
    "compute_amount", "decide", "injection_signals", "run_checks",
]
