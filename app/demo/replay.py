"""Replay mode for the public demo: the real pipeline, with the model's answers pre-recorded.

A scenario (app/demo/scenarios/<name>.json, built by `python -m demo.build_replay`) holds one labeled
ticket's email and what flash-lite actually answered in a frozen eval run: the classification output and every
agent turn (raw output, as stored). Replaying it runs exactly what POST /tickets runs (intake, classification,
the agent loop with the REAL sender-scoped tools on the DB, the current guardrails, execution); only the
`LLMClient` is a `RecordedLLM` instead of Gemini. So a replay costs no quota and is always available, and
what the visitor sees is the code deciding, not a canned answer.

`RecordedLLM` refuses to improvise: before each agent turn it compares the tool results our code just
produced with the ones the model saw when it was recorded. Any difference (shop data changed, the order was
refunded by another ticket) raises a non-transient LLMError, so the ticket ends `failed` (llm_unavailable,
with "replay diverged" in the step's error) instead of showing a model answer to a question it never saw.

Replayed steps are recorded under the model name `replay:<recorded model>`: no price is known for that name,
so their cost is None, and the live-mode quota (counted on real model names) never counts them.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from app.agent.client import ChatResponse, FunctionCall, LLMError, LLMResponse, Message, ModelTurn, ToolResults
from app.agent.loop import SUBMIT
from app.config import Settings
from app.db.models import AgentStep, Refund, Ticket
from app.tickets import receive_ticket

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
REPLAY_PREFIX = "replay:"


@dataclass(frozen=True)
class Scenario:
    name: str
    data: dict[str, Any]

    @property
    def email(self) -> dict[str, Any]:
        return self.data["email"]

    def summary(self) -> dict[str, Any]:
        """What the demo page lists: no recorded outputs, no expected result."""
        return {k: self.data[k] for k in ("name", "group", "title", "blurb", "label_id", "email")}


@lru_cache
def scenarios() -> dict[str, Scenario]:
    out = {}
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out[data["name"]] = Scenario(name=data["name"], data=data)
    return out


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


class RecordedLLM:
    """An LLMClient that answers with a scenario's recorded outputs, in order."""

    def __init__(self, recorded: dict[str, Any]) -> None:
        self._classify: Iterator[dict] = iter(recorded["classify"])
        self._agent: Iterator[dict] = iter(recorded["agent"])
        self._tool_calls: list[dict] = list(recorded["tool_calls"])
        self._tools_seen = 0

    def generate(self, *, system: str, user: str, schema: dict[str, Any], model: str) -> LLMResponse:
        rec = next(self._classify, None)
        if rec is None:
            raise LLMError("replay: no recorded classification left", transient=False)
        return LLMResponse(text=rec["raw_output"], model=model, latency_ms=rec["latency_ms"] or 0,
                           input_tokens=rec["input_tokens"], output_tokens=rec["output_tokens"],
                           thinking_tokens=rec["thinking_tokens"])

    def chat(self, *, system: str, messages: list[Message], tools: list, model: str) -> ChatResponse:
        self._check_tool_results(messages[-1])
        rec = next(self._agent, None)
        if rec is None:
            raise LLMError("replay: no recorded agent turn left", transient=False)
        content = json.loads(rec["raw_output"])
        calls = [
            FunctionCall(name=fc.get("name", ""), args=fc.get("args") or {}, id=fc.get("id"))
            for part in content.get("parts", []) if (fc := part.get("function_call"))
        ]
        return ChatResponse(text=rec["raw_output"], model=model, latency_ms=rec["latency_ms"] or 0,
                            input_tokens=rec["input_tokens"], output_tokens=rec["output_tokens"],
                            thinking_tokens=rec["thinking_tokens"], calls=calls, turn=ModelTurn(content))

    def _check_tool_results(self, last: Message) -> None:
        """The results our tools just returned must be the ones the recorded model read."""
        if not isinstance(last, ToolResults):
            return
        for call, result in last.results:
            if call.name == SUBMIT:  # our own "submit alone" / "fix your proposal" notes, not a tool call
                continue
            if self._tools_seen >= len(self._tool_calls):
                raise LLMError(f"replay diverged: unrecorded tool call {call.name}", transient=False)
            want = self._tool_calls[self._tools_seen]
            self._tools_seen += 1
            got = {"name": call.name, "args": call.args, "result": result}
            if _canon(got) != _canon({k: want[k] for k in ("name", "args", "result")}):
                raise LLMError(f"replay diverged: {call.name} returned something the recorded model never saw",
                               transient=False)


def replay_settings(settings: Settings, recorded: dict[str, Any]) -> Settings:
    """The app's settings with the recorded models and prompt versions: steps are labeled `replay:<model>`,
    there is no fallback model, and no retry waits."""
    return settings.model_copy(update={
        "classify_model": REPLAY_PREFIX + recorded["classify_model"], "classify_fallback_model": "",
        "classify_prompt_version": recorded["classify_prompt_version"],
        "agent_model": REPLAY_PREFIX + recorded["agent_model"], "agent_fallback_model": "",
        "agent_prompt_version": recorded["agent_prompt_version"],
        "llm_retry_backoff_s": 0,
    })


# One replay of a scenario at a time (the demo runs as one process): a replay deletes the scenario's earlier
# replays, which must not happen under a replay that is still running.
_locks: dict[str, threading.Lock] = {name: threading.Lock() for name in scenarios()}


def clear_earlier_replays(db: Session, scenario: Scenario) -> int:
    """Delete this scenario's earlier replay tickets and the refunds they executed, so the shop is back to the
    state the recording was made in. Live tickets (no replay steps) are never touched."""
    e = scenario.email
    replayed = exists().where(AgentStep.ticket_id == Ticket.id, AgentStep.model.like(f"{REPLAY_PREFIX}%"))
    ids = list(db.scalars(select(Ticket.id).where(
        Ticket.from_email == e["from_email"], Ticket.subject == e["subject"], Ticket.body == e["body"], replayed)))
    if ids:
        db.execute(delete(Refund).where(Refund.id.in_([refund_id(i) for i in ids])))
        db.execute(delete(Ticket).where(Ticket.id.in_(ids)))  # steps, actions, reviews cascade
        db.commit()
    return len(ids)


def refund_id(ticket_id: uuid.UUID) -> str:
    return f"RF-T{ticket_id.hex[:12].upper()}"  # the id app/execution gives a ticket's refund


def replay(db: Session, scenario: Scenario, settings: Settings) -> Ticket:
    rec = scenario.data["recorded"]
    with _locks.setdefault(scenario.name, threading.Lock()):
        clear_earlier_replays(db, scenario)
        e = scenario.email
        return receive_ticket(
            db, from_email=e["from_email"], subject=e["subject"], body=e["body"],
            received_at=datetime.fromisoformat(e["received_at"]), attachments=e.get("attachments") or [],
            llm=RecordedLLM(rec), settings=replay_settings(settings, rec),
        )
