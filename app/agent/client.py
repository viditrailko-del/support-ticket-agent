"""Talking to the LLM. This file knows about Gemini; nothing else in the app does.

The rest of the app depends only on the `LLMClient` protocol, so tests plug in a fake and never call the
real API. Ported from rag_project. One difference: our instructions go in `system_instruction` and the
customer's email goes in a separate user message, so untrusted text is never concatenated into the
instructions. Phase 3 adds function calling.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol

import httpx
from google import genai
from google.genai import errors, types

from app.config import get_settings
from app.logging_setup import log_event

try:  # google-genai 3.x sends requests through httpx2, a separate package: its errors are NOT httpx errors.
    import httpx2
except ImportError:  # pragma: no cover - older SDKs use plain httpx
    httpx2 = httpx

log = logging.getLogger(__name__)

# Network-level failures from either HTTP library. Found by the classification eval (2026-11-17): a
# "Server disconnected" from httpx2 escaped the httpx-only handlers and crashed the run.
TIMEOUT_ERRORS = (httpx.TimeoutException, httpx2.TimeoutException)
TRANSPORT_ERRORS = (httpx.TransportError, httpx2.TransportError)

TRANSIENT_CODES = {408, 429, 500, 502, 503, 504}  # rate limit, server errors, overloaded

# A 429 is either "too many per MINUTE" (retry soon) or "too many per DAY" (this model is done until the
# quota resets). Retrying a daily quota only burns time: go straight to the fallback.
DAILY_QUOTA = re.compile(r"per day|PerDay", re.IGNORECASE)


@dataclass
class LLMResponse:
    text: str | None  # the model's text; for chat(): the raw response content as JSON, stored before use
    model: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None  # billed as output, but not included in output_tokens


# ---------------------------------------------------------------- function calling (provider-neutral types)

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema, built from a Pydantic model (rule 7)


@dataclass(frozen=True)
class FunctionCall:
    name: str
    args: dict[str, Any]
    id: str | None = None


@dataclass(frozen=True)
class UserText:
    text: str


@dataclass(frozen=True)
class ToolResults:
    results: list[tuple[FunctionCall, dict[str, Any]]]  # each call with the result our code produced


@dataclass(frozen=True)
class ModelTurn:
    """A model reply, replayed verbatim in the next request. Opaque on purpose: Gemini 3 attaches thought
    signatures to function-call parts, and they must come back unchanged or the next call is rejected."""

    content: Any


Message = UserText | ToolResults | ModelTurn


@dataclass
class ChatResponse(LLMResponse):
    calls: list[FunctionCall] = field(default_factory=list)
    turn: ModelTurn | None = None


class LLMError(Exception):
    """A failed LLM call. `transient=True` means retrying might work (overload, timeout)."""

    def __init__(self, message: str, *, transient: bool, latency_ms: int = 0):
        super().__init__(message)
        self.transient = transient
        self.latency_ms = latency_ms


class LLMClient(Protocol):
    def generate(self, *, system: str, user: str, schema: dict[str, Any], model: str) -> LLMResponse: ...

    def chat(self, *, system: str, messages: list[Message], tools: list[ToolSpec], model: str) -> ChatResponse:
        """One function-calling turn. The model must call one of `tools` (no free text)."""
        ...


class GeminiClient:
    def __init__(self, api_key: str, *, timeout_s: int = 60, thinking_level: str = "low"):
        # A missing key must not become an HTTP 500: every call fails as a permanent LLMError and the
        # ticket ends `failed`.
        self._client = (
            genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_s * 1000))
            if api_key
            else None
        )
        self._thinking_level = thinking_level

    def generate(self, *, system: str, user: str, schema: dict[str, Any], model: str) -> LLMResponse:
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            temperature=0,
            thinking_config=types.ThinkingConfig(thinking_level=self._thinking_level),
        )
        resp, ms = self._send(model, [types.Content(role="user", parts=[types.Part(text=user)])], config)
        return LLMResponse(text=resp.text, model=model, latency_ms=ms, **_usage(resp))

    def chat(self, *, system: str, messages: list[Message], tools: list[ToolSpec], model: str) -> ChatResponse:
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0,
            thinking_config=types.ThinkingConfig(thinking_level=self._thinking_level),
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.parameters)
                for t in tools
            ])],
            # ANY: every turn must be a tool call (the final answer is the submit_proposal tool).
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY")),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),  # our code runs tools
        )
        resp, ms = self._send(model, [_to_content(m) for m in messages], config)
        content = resp.candidates[0].content if resp.candidates else None
        return ChatResponse(
            text=content.model_dump_json(exclude_none=True) if content else None,
            model=model, latency_ms=ms, **_usage(resp),
            calls=[FunctionCall(name=c.name or "", args=dict(c.args or {}), id=c.id) for c in (resp.function_calls or [])],
            turn=ModelTurn(content) if content else None,
        )

    def _send(self, model: str, contents: list, config: types.GenerateContentConfig) -> tuple[Any, int]:
        """The one place that talks to Gemini: maps every failure to LLMError (never a raw exception)."""
        if self._client is None:
            raise LLMError("GEMINI_API_KEY is not set", transient=False)
        start = time.monotonic()
        try:
            resp = self._client.models.generate_content(model=model, contents=contents, config=config)
        except errors.APIError as e:
            raise LLMError(f"{e.code}: {str(e)[:500]}", transient=is_transient(e.code, str(e)),
                           latency_ms=_ms_since(start)) from e
        except TIMEOUT_ERRORS as e:
            raise LLMError("timeout", transient=True, latency_ms=_ms_since(start)) from e
        except TRANSPORT_ERRORS as e:  # network down, DNS failure, server disconnected: retryable
            raise LLMError(f"network: {type(e).__name__}: {e}"[:500], transient=True,
                           latency_ms=_ms_since(start)) from e
        except Exception as e:  # anything else the SDK throws must still end as a `failed` ticket (rule 9)
            log_event(log, logging.ERROR, "llm_unexpected_error", model=model, error=type(e).__name__, exc_info=True)
            raise LLMError(f"unexpected: {type(e).__name__}: {e}"[:500], transient=False,
                           latency_ms=_ms_since(start)) from e
        return resp, _ms_since(start)


def _usage(resp: Any) -> dict[str, int | None]:
    u = resp.usage_metadata
    return {
        "input_tokens": u.prompt_token_count if u else None,
        "output_tokens": u.candidates_token_count if u else None,
        "thinking_tokens": u.thoughts_token_count if u else None,
    }


def _to_content(m: Message) -> Any:
    if isinstance(m, ModelTurn):
        return m.content
    if isinstance(m, UserText):
        return types.Content(role="user", parts=[types.Part(text=m.text)])
    return types.Content(role="user", parts=[
        types.Part(function_response=types.FunctionResponse(id=call.id, name=call.name, response=result))
        for call, result in m.results
    ])


def is_transient(code: int, message: str) -> bool:
    """Could the SAME model succeed if we retry it soon?"""
    if code == 429 and DAILY_QUOTA.search(message):
        return False
    return code in TRANSIENT_CODES


def _ms_since(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


@lru_cache
def get_llm() -> LLMClient:
    """FastAPI dependency; tests override it with a fake."""
    s = get_settings()
    return GeminiClient(s.gemini_api_key, timeout_s=s.gemini_timeout_s, thinking_level=s.gemini_thinking_level)
