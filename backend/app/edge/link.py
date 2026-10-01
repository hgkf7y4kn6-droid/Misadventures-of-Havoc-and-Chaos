"""EdgeLink: the engine's Publisher + Scheduler when a Durable Object owns the game.

All outbound operations for a game (socket messages, kicks, alarm arm/cancel)
go into one ordered per-game buffer and are flushed as a single signed POST to
``{edge_url}/internal/rooms/{code}/ops``. Ordering matters: "cancel alarm"
must never overtake the "arm alarm" that follows it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from ..engine.ports import FireTimer
from .signing import sign

log = logging.getLogger("havoc.edge")


class EdgeLink:
    def __init__(self, edge_url: str, secret: str, client: httpx.AsyncClient | None = None):
        self.edge_url = edge_url.rstrip("/")
        self.secret = secret
        self.client = client or httpx.AsyncClient(timeout=15)
        self._buffers: dict[str, list[dict[str, Any]]] = {}
        self._flushers: dict[str, asyncio.Task] = {}

    # -- Publisher --------------------------------------------------------------

    async def to_player(self, code: str, player_id: str, message: dict[str, Any]) -> None:
        self._enqueue(code, {"op": "send", "player_id": player_id, "message": message})

    async def disconnect_player(self, code: str, player_id: str) -> None:
        self._enqueue(code, {"op": "kick", "player_id": player_id})

    # -- Scheduler (alarms are delivered back via POST /internal/games/{code}/alarm) --

    def arm(self, code: str, seconds: float, tag: str, token: str, fire: FireTimer) -> None:
        self._enqueue(code, {"op": "alarm", "at_ms": int((time.time() + seconds) * 1000), "tag": tag, "token": token})

    def cancel(self, code: str) -> None:
        self._enqueue(code, {"op": "cancel_alarm"})

    # -- plumbing -------------------------------------------------------------------

    def _enqueue(self, code: str, op: dict[str, Any]) -> None:
        self._buffers.setdefault(code, []).append(op)
        task = self._flushers.get(code)
        if task is None or task.done():
            try:
                self._flushers[code] = asyncio.get_running_loop().create_task(self._flush(code))
            except RuntimeError:
                pass

    async def _flush(self, code: str) -> None:
        await asyncio.sleep(0)  # coalesce everything produced in the same tick
        while self._buffers.get(code):
            ops, self._buffers[code] = self._buffers[code], []
            await self._post(code, ops)

    async def _post(self, code: str, ops: list[dict[str, Any]]) -> None:
        path = f"/internal/rooms/{code}/ops"
        body = json.dumps({"ops": ops}, default=str, separators=(",", ":")).encode()
        for attempt in range(4):
            try:
                headers = {"content-type": "application/json", **sign(self.secret, "POST", path, body)}
                r = await self.client.post(self.edge_url + path, content=body, headers=headers)
                if r.status_code < 500:
                    if r.status_code >= 400:
                        log.warning("edge rejected ops for %s: %s %s", code, r.status_code, r.text[:200])
                    return
            except httpx.HTTPError as exc:
                log.warning("edge unreachable (%s), attempt %s", exc, attempt + 1)
            await asyncio.sleep(0.25 * 2**attempt)
        log.error("dropped %d ops for %s after retries", len(ops), code)

    async def drain(self) -> None:
        for task in list(self._flushers.values()):
            if not task.done():
                await task

    async def aclose(self) -> None:
        await self.drain()
        await self.client.aclose()
