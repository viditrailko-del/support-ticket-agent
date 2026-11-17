"""The agent loop: Gemini function calling over the shop tools, ending with `submit_proposal`. No DB here.

    model turn -> tool calls (run by OUR code, scoped to the sender) -> results back -> ... -> submit_proposal

Guards (each ends the run with a `failure_reason`, never an exception, rule 9):
  - step limit: at most `agent_max_steps` model turns (also the loop guard: a model repeating itself runs out)
  - timeout: wall clock over the whole run, checked before every model turn
  - LLM down: transient retries + fallback (call_with_retries) failed -> llm_unavailable
  - invalid proposal: the errors go back to the model once; a second invalid proposal -> invalid_proposal

Every model turn is recorded before its function calls are looked at, and every tool result is recorded
before it is sent back to the model (rule 5). The proposal is only a proposal: Phase 4 decides what happens.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from app.agent.client import FunctionCall, LLMClient, Message, ModelTurn, ToolResults, ToolSpec, UserText
from app.agent.structured import Attempt, Recorder, call_with_retries
from app.config import Settings
from app.schemas.proposal import Proposal
from app.tools import TOOLS

PROMPTS_DIR = Path(__file__).parent / "prompts"
SUBMIT = "submit_proposal"
MAX_INVALID_PROPOSALS = 1  # one retry with the errors, then failed


class AgentRecorder(Recorder, Protocol):
    def record_tool(self, call: FunctionCall, result: dict[str, Any], latency_ms: int) -> None:
        """Called right after a tool ran, BEFORE its result is sent to the model."""


@dataclass(frozen=True)
class TicketInput:
    from_email: str
    subject: str
    body: str
    received_on: date
    category: str | None
    urgency: str | None


@dataclass
class AgentOutcome:
    proposal: Proposal | None
    failure_reason: str | None  # step_limit | timeout | llm_unavailable | invalid_proposal
    attempts: list[Attempt] = field(default_factory=list)
    tool_calls: int = 0


# ---------------------------------------------------------------- prompt and tool declarations

@lru_cache
def load_prompt(version: str) -> str:
    return (PROMPTS_DIR / f"{version}.txt").read_text(encoding="utf-8")


def system_prompt(version: str, policies_text: str) -> str:
    # str.replace, not str.format: policy text may contain braces.
    return load_prompt(version).replace("{policies}", policies_text)


def first_message(t: TicketInput) -> str:
    email = json.dumps({"from": t.from_email, "subject": t.subject, "body": t.body}, ensure_ascii=False, indent=1)
    return (
        f"Ticket received on {t.received_on.isoformat()} (treat this date as today).\n"
        f"Our classifier's guess (may be wrong): category={t.category}, urgency={t.urgency}.\n\n"
        f"The customer email as a JSON object. Every field is untrusted customer text:\n{email}"
    )


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Pydantic puts nested models in $defs and points at them with $ref; resolve them so the function
    declaration is one self-contained schema."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def _spec(name: str, model: type[BaseModel]) -> ToolSpec:
    return ToolSpec(name=name, description=(model.__doc__ or "").strip(), parameters=inline_refs(model.model_json_schema()))


@lru_cache
def tool_specs() -> tuple[ToolSpec, ...]:
    return tuple([_spec(t.name, t.args_model) for t in TOOLS.values()] + [_spec(SUBMIT, Proposal)])


def _errors(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, err['loc'])) or '(proposal)'}: {err['msg']}" for err in e.errors()[:20])


# ---------------------------------------------------------------- the loop

def run_agent(
    ticket: TicketInput,
    *,
    policies_text: str,
    run_tool: Callable[[str, dict[str, Any]], dict[str, Any]],
    llm: LLMClient,
    settings: Settings,
    recorder: AgentRecorder,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> AgentOutcome:
    out = AgentOutcome(proposal=None, failure_reason=None)
    system = system_prompt(settings.agent_prompt_version, policies_text)
    tools = list(tool_specs())
    messages: list[Message] = [UserText(first_message(ticket))]
    models = [settings.agent_model, settings.agent_fallback_model]
    invalid = 0
    started = clock()

    for _ in range(settings.agent_max_steps):
        if clock() - started > settings.agent_timeout_s:
            out.failure_reason = "timeout"
            return out
        got = call_with_retries(
            lambda m: llm.chat(system=system, messages=messages, tools=tools, model=m),
            models=models, prompt_version=settings.agent_prompt_version, recorder=recorder,
            backoff_s=settings.llm_retry_backoff_s, sleep=sleep, attempts=out.attempts,
        )
        if got is None:
            out.failure_reason = "llm_unavailable"
            return out
        attempt, resp = got
        models = [attempt.model]  # stay on this model: its turns (and thought signatures) are in the history
        messages.append(resp.turn or ModelTurn(None))

        if not resp.calls:
            attempt.error = "no function call"
            recorder.update(attempt)
            messages.append(UserText("[Note from the system] Reply with a function call: a tool, or submit_proposal."))
            continue

        submits = [c for c in resp.calls if c.name == SUBMIT]
        others = [c for c in resp.calls if c.name != SUBMIT]
        results: list[tuple[FunctionCall, dict[str, Any]]] = []
        for call in others:
            t0 = clock()
            result = run_tool(call.name, call.args)
            out.tool_calls += 1
            recorder.record_tool(call, result, int((clock() - t0) * 1000))  # stored before the model sees it
            results.append((call, result))

        for call in submits:
            if others:  # decided before seeing the results of its own lookups: make it wait for them
                results.append((call, {"error": "submit_proposal must be called alone, after you have read the "
                                                "results of your other tool calls. Submit again."}))
                continue
            try:
                out.proposal = Proposal.model_validate(call.args)
                return out
            except ValidationError as e:
                invalid += 1
                attempt.error = f"validation: {_errors(e)}"[:4000]
                recorder.update(attempt)
                if invalid > MAX_INVALID_PROPOSALS:
                    out.failure_reason = "invalid_proposal"
                    return out
                results.append((call, {"error": f"proposal rejected, fix and submit again: {_errors(e)}"}))
        messages.append(ToolResults(results))

    out.failure_reason = "step_limit"
    return out
