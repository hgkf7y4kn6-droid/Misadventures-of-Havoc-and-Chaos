"""Provider-independent LLM interface.

Providers only know how to turn (system, user, JSON schema) into a JSON
object. Everything game-related — validation, retries, fallbacks, and what
context a call is *allowed* to see — lives in ``services.py``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMError(RuntimeError):
    pass


class LLMProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    async def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int = 4000
    ) -> dict[str, Any]:
        """Return a JSON object that *should* match ``schema``. Callers validate it."""

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Optional embeddings for semantic theme matching. ``None`` when unsupported."""
        return None

    async def aclose(self) -> None:  # pragma: no cover - trivial
        return None
