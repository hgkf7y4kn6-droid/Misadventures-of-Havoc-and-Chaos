"""Product analytics (PostHog) behind the ``Analytics`` port.

Rules enforced here, not just by convention:
* properties may only be numbers, booleans, short identifiers/enums, or small dicts of those;
* any string longer than 64 characters or containing whitespace beyond a few words is dropped,
  so player-written text (themes, actions, chat, secrets) can never be sent by accident.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from ..config import Settings
from ..engine.ports import Analytics, NullAnalytics

log = logging.getLogger("havoc.analytics")

MAX_STR = 64


def sanitize(props: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (props or {}).items():
        if isinstance(v, bool | int | float) or v is None:
            out[k] = v
        elif isinstance(v, str) and len(v) <= MAX_STR and v.count(" ") <= 2:
            out[k] = v
        elif isinstance(v, dict):
            nested = sanitize(v)
            if nested:
                out[k] = nested
    return out


class PostHogAnalytics:
    """Batches events and ships them to PostHog's /batch endpoint in the background."""

    def __init__(self, api_key: str, host: str, flush_every: float = 2.0, max_batch: int = 100):
        self.api_key = api_key
        self.host = host.rstrip("/")
        self.flush_every = flush_every
        self.max_batch = max_batch
        self._queue: list[dict[str, Any]] = []
        self._task: asyncio.Task | None = None
        self._client = httpx.AsyncClient(timeout=10)

    def capture(self, event: str, distinct_id: str, properties: dict[str, Any] | None = None,
                groups: dict[str, str] | None = None) -> None:
        props = sanitize(properties)
        if groups:
            props["$groups"] = {k: str(v)[:MAX_STR] for k, v in groups.items()}
        self._queue.append({"event": event, "distinct_id": distinct_id, "properties": props,
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        if len(self._queue) >= self.max_batch:
            self._kick(0)
        else:
            self._kick(self.flush_every)

    def _kick(self, delay: float) -> None:
        if self._task and not self._task.done():
            return
        try:
            self._task = asyncio.get_running_loop().create_task(self._flush_later(delay))
        except RuntimeError:
            pass

    async def _flush_later(self, delay: float) -> None:
        await asyncio.sleep(delay)
        await self.flush()

    async def flush(self) -> None:
        while self._queue:
            batch, self._queue = self._queue[: self.max_batch], self._queue[self.max_batch :]
            try:
                r = await self._client.post(f"{self.host}/batch/", json={"api_key": self.api_key, "batch": batch})
                if r.status_code >= 400:
                    log.warning("posthog rejected batch: %s", r.status_code)
            except httpx.HTTPError as exc:
                log.warning("posthog unreachable: %s", exc)
                return

    async def aclose(self) -> None:
        await self.flush()
        await self._client.aclose()


def build_analytics(settings: Settings) -> Analytics:
    if settings.posthog_api_key:
        return PostHogAnalytics(settings.posthog_api_key, settings.posthog_host)
    return NullAnalytics()
