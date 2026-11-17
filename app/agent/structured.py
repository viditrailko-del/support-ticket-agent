"""One structured-output LLM request, with the "the model may be wrong, slow or down" logic, and no DB.

call_structured():
  - transient errors (overload, timeout, per-minute rate limit): retry with backoff, then the fallback model
  - permanent errors (bad request, daily quota, missing key): straight to the fallback model
  - output that doesn't fit the Pydantic schema: retry ONCE, telling the model what was wrong
  - nothing worked: `parsed is None` (callers turn that into a `failed` ticket, never an exception)

Every call is reported to a `Recorder` right after it returns, BEFORE its output is parsed (rule 5): the API
stores agent_steps rows, the eval keeps them in memory, so the eval measures exactly the code the API runs.
Shared by classification (Phase 2) and the agent's final proposal (Phase 3). Ported from rag_project.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.agent.client import LLMClient, LLMError, LLMResponse
from app.agent.pricing import cost_usd
from app.logging_setup import log_event

log = logging.getLogger(__name__)

MAX_TRANSIENT_RETRIES = 2  # per model, so up to 3 calls each
MAX_VALIDATION_RETRIES = 1

RETRY_FEEDBACK = """

[Note from the system, not from the customer] Your previous reply was rejected because it did not match
the required JSON schema. Problems:
{errors}
Reply again for the same email, fixing these problems."""

T = TypeVar("T", bound=BaseModel)


@dataclass
class Attempt:
    """One LLM call and what came of it (one agent_steps row in the API)."""

    model: str
    prompt_version: str
    raw_output: str | None = None
    error: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    cost_usd: Decimal | None = None


class Recorder(Protocol):
    def record(self, attempt: Attempt) -> None:
        """Called right after each LLM call, BEFORE its output is parsed."""

    def update(self, attempt: Attempt) -> None:
        """Called when a recorded attempt changes (its output failed validation)."""


class MemoryRecorder:
    """Keeps attempts in memory (eval harness, unit tests)."""

    def __init__(self) -> None:
        self.attempts: list[Attempt] = []
        self.tool_calls: list[dict] = []  # agent runs: {name, args, result, latency_ms} in call order

    def record(self, attempt: Attempt) -> None:
        self.attempts.append(attempt)

    def update(self, attempt: Attempt) -> None:
        pass

    def record_tool(self, call, result: dict, latency_ms: int) -> None:
        self.tool_calls.append({"name": call.name, "args": call.args, "result": result, "latency_ms": latency_ms})


@dataclass
class StructuredOutcome(Generic[T]):
    parsed: T | None
    attempts: list[Attempt] = field(default_factory=list)


def call_structured(
    *,
    output: type[T],
    system: str,
    user: str,
    models: Sequence[str],
    prompt_version: str,
    llm: LLMClient,
    recorder: Recorder,
    backoff_s: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> StructuredOutcome[T]:
    outcome: StructuredOutcome[T] = StructuredOutcome(parsed=None)
    schema = output.model_json_schema()
    prompt_user = user
    for _ in range(1 + MAX_VALIDATION_RETRIES):
        def invoke(model: str, u: str = prompt_user) -> LLMResponse:
            return llm.generate(system=system, user=u, schema=schema, model=model)

        got = call_with_retries(invoke, models=models, prompt_version=prompt_version, recorder=recorder,
                                backoff_s=backoff_s, sleep=sleep, attempts=outcome.attempts)
        if got is None:
            return outcome  # every model/attempt failed at the API level
        attempt, _ = got
        try:
            outcome.parsed = output.model_validate_json(attempt.raw_output or "")
            return outcome
        except ValidationError as e:
            attempt.error = f"validation: {e}"[:4000]
            recorder.update(attempt)
            log_event(log, logging.WARNING, "llm_output_invalid", model=attempt.model,
                      prompt_version=prompt_version, errors=e.error_count())
            prompt_user = user + RETRY_FEEDBACK.format(errors=_short_errors(e))
    return outcome


R = TypeVar("R", bound=LLMResponse)


def call_with_retries(
    invoke: Callable[[str], R],
    *,
    models: Sequence[str],
    prompt_version: str,
    recorder: Recorder,
    backoff_s: float,
    sleep: Callable[[float], None],
    attempts: list[Attempt],
) -> tuple[Attempt, R] | None:
    """Call `invoke(model)` with transient retries, then the fallback model. Each call is recorded right after
    it returns, before anyone looks at its output. None = every model failed."""
    for model in [m for m in models if m]:
        for attempt_no in range(1 + MAX_TRANSIENT_RETRIES):
            attempt = Attempt(model=model, prompt_version=prompt_version)
            attempts.append(attempt)
            try:
                resp = invoke(model)
            except LLMError as e:
                attempt.error = str(e)[:4000]
                attempt.latency_ms = e.latency_ms
                recorder.record(attempt)
                log_event(log, logging.WARNING, "llm_call_failed", model=model, transient=e.transient,
                          attempt=attempt_no + 1, latency_ms=e.latency_ms, error=str(e)[:200])
                if not e.transient:
                    break  # same model won't fix it: try the fallback
                if attempt_no < MAX_TRANSIENT_RETRIES:
                    sleep(backoff_s * (2**attempt_no))
                continue

            attempt.raw_output = resp.text
            attempt.latency_ms = resp.latency_ms
            attempt.input_tokens = resp.input_tokens
            attempt.output_tokens = resp.output_tokens
            attempt.thinking_tokens = resp.thinking_tokens
            attempt.cost_usd = cost_usd(model, resp.input_tokens, resp.output_tokens, resp.thinking_tokens)
            recorder.record(attempt)  # raw output is saved before we even try to parse it
            return attempt, resp
    return None


def _short_errors(e: ValidationError) -> str:
    return "\n".join(
        f"- {'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}" for err in e.errors()[:20]
    )
