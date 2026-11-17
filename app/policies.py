"""Policy rules for the agent's prompt.

All customer-facing rules go into the prompt (decision in CLAUDE.md: no search tool). The rendered text
is hashed into `policy_version`, recorded on every run, so eval runs stay comparable when a page changes.
Only customer-visible rules may live in policy_rules: anything in the prompt can leak into a reply (rule 13).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PolicyRule


@dataclass(frozen=True)
class PolicySet:
    rules: tuple[PolicyRule, ...]
    text: str  # what the prompt contains
    version: str  # sha256 of text, 16 hex chars

    def rule(self, rule_id: str) -> PolicyRule | None:
        return next((r for r in self.rules if r.id == rule_id), None)


def render(rules: list[PolicyRule] | tuple[PolicyRule, ...]) -> str:
    """One rule per line: `[REF-1] Refunds > Refund window: <text>`. Pure."""
    return "\n".join(f"[{r.id}] {r.page_title} > {r.title}: {r.text}" for r in rules)


def version_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_policies(db: Session) -> PolicySet:
    rules = tuple(db.scalars(select(PolicyRule).order_by(PolicyRule.position)))
    text = render(rules)
    return PolicySet(rules=rules, text=text, version=version_of(text))
