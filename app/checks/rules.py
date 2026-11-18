"""The guardrail checks. Pure functions: (proposal, facts[, limits]) -> flags. No DB, no network (rule 6).

The model's numbers are never used: the refund amount is recomputed from the order lines it named, and its
own `amount_cents` is only compared. Every threshold here is internal (rule 13): none of it is in the prompt.

Day windows mirror the policy pages ("up to and including N days"):
  refund   delivered <= 30 days (VIP 60) | not shipped yet (cancellation) | lost (shipped >= 14 days, not delivered)
  replace  delivered <= 14 days (damage) | <= 30 for a wrong item | <= 365 if every line is electronics
           (warranty) | lost
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.checks.facts import Facts, Flag, OrderFacts, Severity
from app.schemas.proposal import ActionType, Proposal

R, V, I = Severity.reject, Severity.review, Severity.info

REFUND_WINDOW_DAYS = 30
VIP_REFUND_WINDOW_DAYS = 60
DAMAGE_WINDOW_DAYS = 14
WRONG_ITEM_WINDOW_DAYS = 30
WARRANTY_DAYS = 365
LOST_AFTER_DAYS = 14

# Which actions make sense for each classified category. Anything else is a reason to look.
ALLOWED_ACTIONS: dict[str, frozenset[ActionType]] = {
    "refund_request": frozenset({ActionType.refund, ActionType.reply_only, ActionType.escalate}),
    "damaged_or_defective": frozenset(ActionType),
    "wrong_item": frozenset(ActionType),
    "delivery_issue": frozenset(ActionType),
    "cancellation": frozenset({ActionType.refund, ActionType.reply_only, ActionType.escalate}),
    "account": frozenset({ActionType.reply_only, ActionType.escalate}),
    "other": frozenset({ActionType.reply_only, ActionType.escalate}),
}
# Final-sale items can still be refunded when they arrived damaged or were the wrong item (RET-1).
FINAL_SALE_EXCEPTIONS = frozenset({"damaged_or_defective", "wrong_item"})


@dataclass(frozen=True)
class Limits:
    auto_limit_cents: int  # refunds / replacements at or above this never run without a human


@dataclass(frozen=True)
class Amount:
    """What a refund would pay, computed in code."""

    items_cents: int  # sum of qty * unit price over the named lines that exist
    shipping_cents: int  # the order's shipping fee if include_shipping, else 0
    payable_cents: int  # min(items + shipping, refundable left): REF-3


def _moves_goods(p: Proposal) -> bool:
    return p.action in (ActionType.refund, ActionType.replace)


def compute_amount(p: Proposal, order: OrderFacts) -> Amount:
    items = 0
    for it in p.items:
        line = order.line(it.line_no)
        if line is not None:
            items += min(it.qty, line.qty) * line.unit_price_cents
    shipping = order.shipping_cents if p.include_shipping else 0
    return Amount(items, shipping, max(0, min(items + shipping, order.refundable_left_cents)))


# ---------------------------------------------------------------- one function per check


def check_order(p: Proposal, f: Facts) -> list[Flag]:
    """Rule 1: a refund or replacement needs an existing order that belongs to the sender."""
    if not _moves_goods(p):
        return []
    if f.sender_customer_id is None:
        return [Flag("sender_not_customer", R, "the sender address does not belong to a customer")]
    if f.order is None:
        return [Flag("order_not_found", R, f"order {p.order_id} does not exist")]
    if f.order.customer_id != f.sender_customer_id:
        return [Flag("order_not_senders", R, f"order {f.order.order_id} belongs to another customer")]
    return []


def check_items(p: Proposal, f: Facts) -> list[Flag]:
    """Every named line exists on the order, with no more units than were ordered."""
    if not _moves_goods(p) or f.order is None:
        return []
    flags = []
    for it in p.items:
        line = f.order.line(it.line_no)
        if line is None:
            flags.append(Flag("unknown_line", R, f"line {it.line_no} is not on order {f.order.order_id}"))
        elif it.qty > line.qty:
            flags.append(Flag("qty_above_ordered", R, f"line {it.line_no}: {it.qty} units, {line.qty} ordered"))
    return flags


def check_amount(p: Proposal, f: Facts) -> list[Flag]:
    """Rule 2: the refund is recomputed; the model's figure is only compared with it."""
    if p.action is not ActionType.refund or f.order is None:
        return []
    a = compute_amount(p, f.order)
    flags = []
    left = f.order.refundable_left_cents
    if left <= 0:
        flags.append(Flag("nothing_left_to_refund", R, f"order {f.order.order_id} is already fully refunded"))
    elif a.items_cents + a.shipping_cents > left:
        flags.append(Flag("amount_capped", I, f"{a.items_cents + a.shipping_cents} capped at {left} "
                          "(paid minus already refunded)"))
    if a.payable_cents == 0 and left > 0:
        flags.append(Flag("zero_amount", R, "the named lines add up to nothing"))
    if p.amount_cents != a.payable_cents:
        flags.append(Flag("amount_mismatch", V, f"model wrote {p.amount_cents}, code computed {a.payable_cents}"))
    return flags


def check_window(p: Proposal, f: Facts) -> list[Flag]:
    """The policy windows, from the order's dates (computed relative to the ticket's received date)."""
    if not _moves_goods(p) or f.order is None:
        return []
    o = f.order
    if o.status == "processing":
        if p.action is ActionType.replace:
            return [Flag("replace_not_shipped", V, "replacing an order that has not shipped yet")]
        return []  # cancellation before shipping (CAN-1)
    if o.days_since_delivered is None:  # shipped, not delivered
        if (o.days_since_shipped or 0) >= LOST_AFTER_DAYS:
            return []  # lost (SHP-3)
        return [Flag("in_transit", V, f"shipped {o.days_since_shipped} days ago, still in transit")]
    d = o.days_since_delivered
    if p.action is ActionType.refund:
        window = VIP_REFUND_WINDOW_DAYS if f.sender_tier == "vip" else REFUND_WINDOW_DAYS
        if d > window:
            return [Flag("outside_refund_window", V, f"delivered {d} days ago, refund window {window}")]
        return []
    lines = [o.line(it.line_no) for it in p.items]
    if d <= DAMAGE_WINDOW_DAYS:
        return []
    if f.category == "wrong_item" and d <= WRONG_ITEM_WINDOW_DAYS:
        return []
    if lines and all(ln is not None and ln.category == "electronics" for ln in lines) and d <= WARRANTY_DAYS:
        return []
    return [Flag("outside_replace_window", V, f"delivered {d} days ago; no damage, wrong-item or warranty "
                 "window covers this replacement")]


def check_shipping(p: Proposal, f: Facts) -> list[Flag]:
    """REF-2: the shipping fee comes back only for a cancellation before shipping or a lost order."""
    if p.action is not ActionType.refund or not p.include_shipping or f.order is None:
        return []
    o = f.order
    lost = o.days_since_delivered is None and (o.days_since_shipped or 0) >= LOST_AFTER_DAYS
    if o.status == "processing" or lost:
        return []
    return [Flag("shipping_not_refundable", V, "shipping fee included, but the order was not cancelled before "
                 "shipping or lost")]


def check_final_sale(p: Proposal, f: Facts) -> list[Flag]:
    """RET-1: final-sale lines are not refunded, unless damaged or the wrong item."""
    if p.action is not ActionType.refund or f.order is None or f.category in FINAL_SALE_EXCEPTIONS:
        return []
    final = [it.line_no for it in p.items if (ln := f.order.line(it.line_no)) is not None and ln.final_sale]
    if final and f.order.status == "delivered":
        return [Flag("final_sale_item", V, f"final-sale lines refunded: {final}")]
    return []


def check_category(p: Proposal, f: Facts) -> list[Flag]:
    allowed = ALLOWED_ACTIONS.get(f.category or "")
    if allowed is None:
        return [Flag("unknown_category", V, f"category {f.category!r}")]
    if p.action not in allowed:
        return [Flag("action_category_mismatch", V, f"{p.action.value} for a {f.category} ticket")]
    return []


_WS = re.compile(r"\s+")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def _norm(s: str) -> str:
    """Whitespace and curly quotes don't count as a misquote; every word must match."""
    return _WS.sub(" ", s.translate(_QUOTES)).strip().lower()


def check_citations(p: Proposal, f: Facts) -> list[Flag]:
    """Every cited rule exists and its quote is an exact substring of the rule text."""
    flags = []
    for c in p.citations:
        text = f.rules.get(c.rule_id)
        if text is None:
            flags.append(Flag("unknown_rule", V, f"cited {c.rule_id}, which is not a policy rule"))
        elif _norm(c.quote) not in _norm(text):
            flags.append(Flag("quote_not_in_rule", V, f"{c.rule_id}: quote is not in the rule text"))
    return flags


# Signals that an email tries to steer the system instead of asking for something. Internal (rule 13).
INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(rx, re.IGNORECASE | re.MULTILINE)) for name, rx in (
        ("ignore_instructions", r"\b(ignore|disregard|forget)\b.{0,40}\b(instructions?|rules?|prompt|polic(y|ies))\b"),
        ("role_claim", r"\b(admin(istrator)?\s+mode|(verified|store|system)\s+admin(istrator)?|i\s+am\s+an?\s+admin)"),
        ("ai_address", r"\b(note|message|instructions?)\s+(to|for)\s+the\s+(ai|assistant|bot|model)\b"
                       r"|\[\s*(assistant|system)\s+instructions?"),
        ("system_marker", r"(^|\n|;)\s*(system|assistant)\s*:"),
        ("control_field", r"\b(refund_approved|skip_review|auto_approved|approved\s*=\s*true|decision\s*=)"),
        ("fake_support_reply", r"^\s*>.*\b(support|customer service|support team)\b.*\bwrote\b"),
        ("fake_tool_output", r"\"(tool|result|function_call|total_paid_cents|refund_approved)\"\s*:"),
        ("approval_claim", r"\b(management|manager|supervisor|support)\s+(has\s+)?(waived|approved|authori[sz]ed)\b"
                           r"|\bi\s+authori[sz]e\b|\bchecks?\s+(are|is)\s+not\s+needed\b"),
    )
)
_RULE_ID = re.compile(r"\b([A-Z]{3})-(\d{1,3})\b")


def injection_signals(subject: str, body: str, rule_ids: set[str] | frozenset[str]) -> list[str]:
    text = f"{subject}\n{body}"
    found = [name for name, rx in INJECTION_PATTERNS if rx.search(text)]
    # A policy id that looks like ours but isn't one ("per your policy REF-9").
    if any(m.group(0) not in rule_ids and m.group(1) in {r.split("-")[0] for r in rule_ids}
           for m in _RULE_ID.finditer(text)):
        found.append("fake_rule_id")
    return found


def check_injection(p: Proposal, f: Facts) -> list[Flag]:
    """Rule 4: an email that tries to give orders never gets an automatic action, whatever the agent did."""
    signals = injection_signals(f.subject, f.body, frozenset(f.rules))
    if not signals:
        return []
    return [Flag("injection_signal", V, f"email contains instruction-like text: {', '.join(signals)}")]


def check_auto_limit(p: Proposal, f: Facts, limits: Limits) -> list[Flag]:
    """Money and goods at or above the internal limit always go to a human."""
    if not _moves_goods(p) or f.order is None:
        return []
    a = compute_amount(p, f.order)
    value = a.payable_cents if p.action is ActionType.refund else a.items_cents
    if value >= limits.auto_limit_cents:
        return [Flag("above_auto_limit", V, f"{p.action.value} worth {value} cents needs a human")]
    return []


CHECKS = (check_order, check_items, check_amount, check_window, check_shipping, check_final_sale,
          check_category, check_citations, check_injection)
