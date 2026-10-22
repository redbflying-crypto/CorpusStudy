"""The Anthropic API call behind answer generation.

One request per answer (plus at most one repair). Structured output fixes the reply's shape
(`{"answer": ..., "interpretive": ...}`); the system prompt is cached; a request the model
declines on safety grounds is retried server-side on Anthropic's recommended fallback model
(`fallbacks: "default"`). Thinking is adaptive (always on for this model); `effort` sets
its depth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from corpusstudy.config import get_settings
from corpusstudy.generation.prompts import ANSWER_SCHEMA, SYSTEM

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMUnavailable(RuntimeError):
    """No API key is configured."""


@dataclass
class Reply:
    answer: str | None  # None when the model declined or the reply was unusable
    interpretive: bool = False
    stop_reason: str | None = None
    content: list[Any] = field(default_factory=list)  # raw blocks, to replay in a repair turn
    model: str | None = None

    @property
    def declined(self) -> bool:
        return self.stop_reason == "refusal"


class LLM:
    def __init__(self, client: Any = None, *, model: str | None = None, effort: str | None = None):
        settings = get_settings()
        if client is None:
            if not settings.has_api_key:
                raise LLMUnavailable("ANTHROPIC_API_KEY is not set (see .env.example)")
            import anthropic

            assert settings.anthropic_api_key is not None
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())
        self.client = client
        self.model = model or settings.llm_model
        self.effort = effort or settings.llm_effort
        self.max_tokens = settings.llm_max_tokens

    def ask(self, messages: list[dict[str, Any]]) -> Reply:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
            },
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        reply = Reply(None, stop_reason=response.stop_reason, content=list(response.content),
                      model=getattr(response, "model", None))  # fmt: skip
        if response.stop_reason != "end_turn":  # refusal, max_tokens, ...
            return reply
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        try:
            data = json.loads(text)
            reply.answer = str(data["answer"]).strip() or None
            reply.interpretive = bool(data.get("interpretive", False))
        except (ValueError, KeyError, TypeError):
            reply.answer = None
        return reply
