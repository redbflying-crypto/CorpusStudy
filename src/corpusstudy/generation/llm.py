"""The Anthropic API call behind answer generation.

One request per answer (plus at most one repair). Structured output fixes the reply's shape
(`{"answer": ..., "interpretive": ...}`); the system prompt is cached; a request the model
declines on safety grounds is retried server-side on Anthropic's recommended fallback model
(`fallbacks: "default"`). Thinking is adaptive (always on for this model); `effort` sets
its depth. Every call's token usage is added to `usage`, with a cost estimate from PRICES.
`classify` is the optional model-assisted routing step (CORPUS_LLM_ROUTER); `draft` writes a
concept proposal for review (`okf propose`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from corpusstudy.config import get_settings
from corpusstudy.generation.prompts import (
    ANSWER_SCHEMA,
    CLASSIFY,
    CLASSIFY_SCHEMA,
    JUDGE,
    JUDGE_SCHEMA,
    SYSTEM,
    evidence_block,
)
from corpusstudy.schemas import Usage

FALLBACK_BETA = "server-side-fallback-2026-07-01"

# US dollars per million tokens: input, output, cache read, cache write (5-minute).
# Fallback models are priced too (cache reads 0.1x input, cache writes 1.25x input).
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-opus-5": (5.00, 25.00, 0.50, 6.25),
    "claude-opus-4-8": (5.00, 25.00, 0.50, 6.25),
}


class LLMUnavailable(RuntimeError):
    """No API key is configured."""


@dataclass
class Reply:
    answer: str | None  # None when the model declined or the reply was unusable
    interpretive: bool = False
    stop_reason: str | None = None
    content: list[Any] = field(default_factory=list)  # raw blocks, to replay in a repair turn
    model: str | None = None
    data: dict[str, Any] = field(default_factory=dict)  # the parsed JSON reply

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
        self.usage = Usage()

    def ask(self, messages: list[dict[str, Any]]) -> Reply:
        reply = self._call(SYSTEM, messages, ANSWER_SCHEMA, self.effort, self.max_tokens)
        if reply.data:
            reply.answer = str(reply.data.get("answer", "")).strip() or None
            reply.interpretive = bool(reply.data.get("interpretive", False))
        return reply

    def classify(self, question: str) -> dict[str, Any]:
        """The model's route for an open question: {"route": ..., "books": [...]}."""
        messages = [{"role": "user", "content": question}]
        return self._call(CLASSIFY, messages, CLASSIFY_SCHEMA, "low", 1024).data

    def judge(self, evidence: list[Any], answer: str) -> dict[str, Any]:
        """Evaluation only: is every claim in the answer supported by its evidence?"""
        content = f"{evidence_block(evidence)}\n<answer>\n{answer}\n</answer>"
        messages = [{"role": "user", "content": content}]
        return self._call(JUDGE, messages, JUDGE_SCHEMA, "medium", 4096).data

    def draft(self, system: str, messages: list[dict[str, Any]], schema: dict[str, Any]) -> Reply:
        """Knowledge maintenance (Phase 9): a structured draft under its own instructions."""
        return self._call(system, messages, schema, self.effort, self.max_tokens)

    def _call(self, system: str, messages: list[dict[str, Any]], schema: dict[str, Any],
              effort: str, max_tokens: int) -> Reply:  # fmt: skip
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        model = getattr(response, "model", None)
        self._count(getattr(response, "usage", None), model or self.model)
        reply = Reply(None, stop_reason=response.stop_reason, content=list(response.content),
                      model=model)  # fmt: skip
        if response.stop_reason != "end_turn":  # refusal, max_tokens, ...
            return reply
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        try:
            data = json.loads(text)
            reply.data = data if isinstance(data, dict) else {}
        except ValueError:
            reply.data = {}
        return reply

    def _count(self, usage: Any, model: str) -> None:
        u = self.usage
        u.calls += 1
        if usage is None:
            return
        tokens = {
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        }
        for key, n in tokens.items():
            setattr(u, key, getattr(u, key) + n)
        price = PRICES.get(model)
        if price is None or (u.cost_usd is None and u.calls > 1):
            u.cost_usd = None
            return
        cost = (tokens["input_tokens"] * price[0] + tokens["output_tokens"] * price[1]
                + tokens["cache_read_tokens"] * price[2]
                + tokens["cache_write_tokens"] * price[3]) / 1e6  # fmt: skip
        u.cost_usd = round((u.cost_usd or 0.0) + cost, 6)

    def take_usage(self) -> Usage:
        """The usage counted since the last call to take_usage (per answer)."""
        usage, self.usage = self.usage, Usage()
        return usage
