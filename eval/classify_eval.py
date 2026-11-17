"""Small classification eval: answer (real Gemini, once) / score (offline, free, re-runnable).

    python -m eval.classify_eval answer [--limit N] [--ids t01,t02] [--sleep 4.5]   # writes eval/results/classify_<run>.json
    python -m eval.classify_eval answer --resume eval/results/classify_<run>.json    # finish an interrupted run
    python -m eval.classify_eval score eval/results/classify_<run>.json

`answer` runs the exact code the API runs (app.agent.classify.classify_email) with the settings' models
(flash-lite first, main model as fallback), and freezes every attempt: raw output, error, tokens, latency.
`score` compares the frozen outputs with eval/tickets/classification.json. It never calls the model, so
prompt or label work can be re-scored for free. Budget: one call per ticket when nothing goes wrong.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from eval.ticket_review import CLASSIFICATION, TICKETS, load

RESULTS = Path(__file__).parent / "results"


# ---------------------------------------------------------------- answer (costs quota)

def answer(limit: int | None, ids: list[str] | None, sleep_s: float, resume: Path | None = None) -> Path:
    from app.agent.classify import classify_email
    from app.agent.client import get_llm
    from app.agent.structured import MemoryRecorder
    from app.config import get_settings

    settings, llm = get_settings(), get_llm()
    tickets = load(TICKETS)["tickets"]
    if ids:
        tickets = [t for t in tickets if t["id"] in ids]
    tickets = tickets[:limit] if limit else tickets

    if resume:
        # Same run file, only the tickets it doesn't have yet. Refuse if the settings changed: one run = one setup.
        out, run = resume, json.loads(resume.read_text(encoding="utf-8"))
        setup = (settings.classify_prompt_version, [settings.classify_model, settings.classify_fallback_model])
        if (run["prompt_version"], run["models"]) != setup:
            raise SystemExit(f"can't resume {resume}: it used {run['prompt_version']} {run['models']}, now {setup}")
        done = {i["id"] for i in run["items"]}
        tickets = [t for t in tickets if t["id"] not in done]
        print(f"resuming {run['run_id']}: {len(done)} done, {len(tickets)} to go")
    else:
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out = RESULTS / f"classify_{run_id}.json"
        RESULTS.mkdir(parents=True, exist_ok=True)
        run = {
            "run_id": run_id, "kind": "classify", "prompt_version": settings.classify_prompt_version,
            "models": [settings.classify_model, settings.classify_fallback_model],
            "thinking_level": settings.gemini_thinking_level, "items": [],
        }
    for n, t in enumerate(tickets, start=1):
        rec = MemoryRecorder()
        outcome = classify_email(from_email=t["from"], subject=t["subject"], body=t["body"],
                                 llm=llm, settings=settings, recorder=rec)
        run["items"].append({
            "id": t["id"],
            "parsed": outcome.parsed.model_dump(mode="json") if outcome.parsed else None,
            "attempts": [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in dataclasses.asdict(a).items()}
                         for a in rec.attempts],
        })
        out.write_text(json.dumps(run, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")  # frozen as we go
        got = outcome.parsed.category.value if outcome.parsed else "FAILED"
        print(f"[{n}/{len(tickets)}] {t['id']} -> {got} ({len(rec.attempts)} call(s))", flush=True)
        if n < len(tickets):
            time.sleep(sleep_s)  # stay under the per-minute rate limit
    return out


# ---------------------------------------------------------------- score (free)

def score(run: dict, labels: dict, tickets: dict) -> dict:
    """Pure: compare a frozen run with the labels."""
    gold = {lb["id"]: lb for lb in labels["labels"]}
    injection = {t["id"] for t in tickets["tickets"] if t["injection"]}
    rows, attempts = [], []
    for item in run["items"]:
        lb, p = gold[item["id"]], item["parsed"]
        attempts += item["attempts"]
        rows.append({
            "id": item["id"], "injection": item["id"] in injection, "verified": bool(lb.get("verified_hash")),
            "want": (lb["category"], lb["urgency"]),
            "got": (p["category"], p["urgency"]) if p else None,
            "calls": len(item["attempts"]),
        })

    def acc(rs, part):
        ok = [r for r in rs if r["got"] and r["got"][part] == r["want"][part]]
        return round(len(ok) / len(rs), 3) if rs else None

    latencies = [a["latency_ms"] for a in attempts if a.get("latency_ms") is not None]
    costs = [Decimal(a["cost_usd"]) for a in attempts if a.get("cost_usd") is not None]
    inj = [r for r in rows if r["injection"]]
    return {
        "tickets": len(rows),
        "unverified_labels": sum(not r["verified"] for r in rows),
        "failed": sum(r["got"] is None for r in rows),
        "category_accuracy": acc(rows, 0),
        "urgency_accuracy": acc(rows, 1),
        "injection_category_accuracy": acc(inj, 0),
        "calls_per_ticket": round(len(attempts) / len(rows), 2) if rows else None,
        "invalid_outputs": sum(1 for a in attempts if (a.get("error") or "").startswith("validation:")),
        "latency_ms_p50": statistics.median(latencies) if latencies else None,
        "latency_ms_max": max(latencies) if latencies else None,
        "cost_usd_total": str(sum(costs, Decimal(0))) if costs else None,
        "cost_known_for_all_calls": len(costs) == len([a for a in attempts if a.get("raw_output") is not None]),
        "confusions": Counter(f"{r['want'][0]} -> {r['got'][0] if r['got'] else 'FAILED'}"
                              for r in rows if not r["got"] or r["got"][0] != r["want"][0]),
        "misses": [{"id": r["id"], "want": r["want"], "got": r["got"]} for r in rows
                   if not r["got"] or r["got"] != r["want"]],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("answer", help="call Gemini for each ticket and freeze the outputs (uses quota)")
    a.add_argument("--limit", type=int)
    a.add_argument("--ids", help="comma-separated ticket ids")
    a.add_argument("--sleep", type=float, default=4.5, help="seconds between tickets (rate limit)")
    a.add_argument("--resume", type=Path, help="finish this frozen run instead of starting a new one")
    s = sub.add_parser("score", help="score a frozen run against the labels (free)")
    s.add_argument("run", type=Path)
    args = ap.parse_args()

    if args.cmd == "answer":
        out = answer(args.limit, args.ids.split(",") if args.ids else None, args.sleep, args.resume)
        print(f"frozen: {out}\nscore it: python -m eval.classify_eval score {out}")
        return 0
    result = score(json.loads(args.run.read_text(encoding="utf-8")), load(CLASSIFICATION), load(TICKETS))
    misses = result.pop("misses")
    print(json.dumps(result, indent=2, default=dict))
    for m in misses:
        print(f"  miss {m['id']}: want {m['want']}, got {m['got']}")
    if result["unverified_labels"]:
        print(f"WARNING: {result['unverified_labels']} label(s) not human-verified yet", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
