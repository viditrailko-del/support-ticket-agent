"""Run the agent on a few labeled tickets with real Gemini and the dev DB's tools. A smoke test, not the eval
(that is Phase 5): it prints each step and the proposal next to the label, and freezes the run.

    python -m eval.agent_smoke t01 t49 t56          # uses AGENT_MODEL (set it to the lite model to save quota)

Needs the dev DB migrated and seeded (`alembic upgrade head`, `python -m data.seed`). Writes nothing to the DB.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal

from app.agent.classify import classify_email
from app.agent.client import get_llm
from app.agent.loop import TicketInput, run_agent
from app.agent.structured import MemoryRecorder
from app.config import get_settings
from app.db.session import SessionLocal
from app.policies import load_policies
from app.tickets import match_customer
from app.tools import ToolContext, run_tool
from eval.classify_eval import RESULTS
from eval.ticket_review import load


def main(ids: list[str]) -> int:
    settings, llm = get_settings(), get_llm()
    tickets = {t["id"]: t for t in load()["tickets"]}
    run = {"run_id": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"), "agent_model": settings.agent_model,
           "prompt_version": settings.agent_prompt_version, "items": []}
    with SessionLocal() as db:
        policies = load_policies(db)
        for tid in ids:
            t = tickets[tid]
            today = datetime.fromisoformat(t["received_at"]).date()
            ctx = ToolContext(db=db, customer_id=match_customer(db, t["from"]), today=today)
            cls_rec, rec = MemoryRecorder(), MemoryRecorder()
            cls = classify_email(from_email=t["from"], subject=t["subject"], body=t["body"], llm=llm,
                                 settings=settings, recorder=cls_rec).parsed
            out = run_agent(
                TicketInput(t["from"], t["subject"], t["body"], today,
                            cls.category.value if cls else None, cls.urgency.value if cls else None),
                policies_text=policies.text, run_tool=lambda n, a: run_tool(ctx, n, a), llm=llm,
                settings=settings, recorder=rec,
            )
            p = out.proposal
            exp = t["expected"]
            print(f"\n=== {tid} {t['scenario']}  label: {exp['action']} {exp['order_id']} {exp['amount_cents']} {exp['rule_ids']}")
            for c in rec.tool_calls:
                print(f"  tool {c['name']}({json.dumps(c['args'])}) -> found={c['result'].get('found')}"
                      f"{' error=' + c['result']['error'] if 'error' in c['result'] else ''}")
            for a in rec.attempts:
                if a.error:
                    print(f"  model error: {a.error[:200]}")
            if p:
                print(f"  PROPOSAL {p.action.value} {p.order_id} items={[(i.line_no, i.qty) for i in p.items]} "
                      f"ship={p.include_shipping} amount={p.amount_cents}")
                print(f"  cites {[(c.rule_id, c.quote[:60]) for c in p.citations]}")
                print(f"  reply: {p.reply_draft[:220]!r}")
            else:
                print(f"  FAILED: {out.failure_reason}")
            print(f"  model calls {len(rec.attempts)}, tool calls {out.tool_calls}, "
                  f"latency {sum(a.latency_ms or 0 for a in rec.attempts)} ms, "
                  f"cost ${sum((a.cost_usd or Decimal(0)) for a in rec.attempts)}")
            run["items"].append({
                "id": tid, "classification": cls.model_dump(mode="json") if cls else None,
                "proposal": p.model_dump(mode="json") if p else None, "failure_reason": out.failure_reason,
                "attempts": [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in dataclasses.asdict(a).items()}
                             for a in rec.attempts],
                "tool_calls": rec.tool_calls,
            })
    RESULTS.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS / f"agent_smoke_{run['run_id']}.json"
    out_path.write_text(json.dumps(run, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(f"\nfrozen: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
