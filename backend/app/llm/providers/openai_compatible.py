"""Any OpenAI-compatible chat-completions endpoint (OpenAI, local servers, gateways)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..base import LLMError, LLMProvider
from ..schema_utils import strict_schema


class OpenAICompatibleProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str | None, model: str | None, base_url: str | None, timeout: float):
        if not model:
            raise LLMError("HAVOC_LLM_MODEL is required for the openai provider")
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=(base_url or "https://api.openai.com/v1").rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=timeout,
        )

    async def complete_json(self, *, system, user, schema, max_tokens=4000) -> dict[str, Any]:
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": strict_schema(schema), "strict": True},
            },
        }
        try:
            r = await self._client.post("/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"connection error: {exc}") from exc
        if r.status_code >= 400:
            raise LLMError(f"API error {r.status_code}: {r.text[:300]}")
        try:
            content = r.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
        except (KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
            raise LLMError(f"unparseable response: {exc}") from exc
        if not isinstance(data, dict):
            raise LLMError("expected a JSON object")
        return data

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        try:
            r = await self._client.post("/embeddings", json={"model": "text-embedding-3-small", "input": texts})
            if r.status_code >= 400:
                return None
            return [d["embedding"] for d in r.json()["data"]]
        except (httpx.HTTPError, KeyError, ValueError):
            return None

    async def aclose(self) -> None:
        await self._client.aclose()
