"""Claude via the official Anthropic SDK, using structured outputs."""

from __future__ import annotations

import json
from typing import Any

from ..base import LLMError, LLMProvider
from ..schema_utils import strict_schema

DEFAULT_MODEL = "claude-opus-5-5"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str | None, model: str | None, timeout: float, effort: str = "medium"):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise LLMError("pip install anthropic to use the anthropic provider") from exc
        self._anthropic = anthropic
        # api_key=None lets the SDK resolve credentials from its usual sources.
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self.model = model or DEFAULT_MODEL
        self.effort = effort

    async def complete_json(self, *, system, user, schema, max_tokens=4000) -> dict[str, Any]:
        a = self._anthropic
        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=max(max_tokens, 2000),
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": strict_schema(schema)},
                },
                # Server-side refusal fallback: reroutes a declined request automatically.
                extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
                extra_body={"fallbacks": "default"},
            )
        except a.RateLimitError as exc:
            raise LLMError(f"rate limited: {exc}") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"anthropic API error {exc.status_code}: {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise LLMError(f"anthropic connection error: {exc}") from exc

        if response.stop_reason == "refusal":
            raise LLMError("model declined the request")
        if response.stop_reason == "max_tokens":
            raise LLMError("response truncated")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            raise LLMError("empty response")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise LLMError("expected a JSON object")
        return data

    async def aclose(self) -> None:
        await self._client.close()
