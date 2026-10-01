"""WebSocket fan-out.

The engine addresses every message to exactly one player (projections are
per-player), so the hub never decides who may see what — it only delivers.

With ``HAVOC_REDIS_URL`` set, messages go through Redis pub/sub so any API
instance holding a player's socket can deliver it. Game *authority* still
lives in one process per game (route a game code to one instance, e.g. with a
consistent-hash load balancer); Redis provides fan-out, not shared state.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("havoc.hub")


class Hub:
    def __init__(self, redis_url: str | None = None):
        self.sockets: dict[str, dict[str, set[WebSocket]]] = defaultdict(lambda: defaultdict(set))
        self.redis_url = redis_url
        self._redis = None
        self._listener: asyncio.Task | None = None

    async def start(self) -> None:
        if not self.redis_url:
            return
        try:
            import redis.asyncio as redis

            self._redis = redis.from_url(self.redis_url, decode_responses=True)
            await self._redis.ping()
            self._listener = asyncio.create_task(self._listen())
            log.info("realtime fan-out via redis")
        except Exception as exc:  # noqa: BLE001
            log.warning("redis unavailable (%s); using in-process fan-out", exc)
            self._redis = None

    async def stop(self) -> None:
        if self._listener:
            self._listener.cancel()
        if self._redis:
            await self._redis.aclose()

    async def _listen(self) -> None:
        assert self._redis is not None
        pubsub = self._redis.pubsub()
        await pubsub.psubscribe("havoc:*")
        async for item in pubsub.listen():
            if item.get("type") != "pmessage":
                continue
            try:
                envelope = json.loads(item["data"])
                await self._deliver_local(envelope["code"], envelope["player_id"], envelope.get("message"), envelope.get("kick", False))
            except Exception:  # noqa: BLE001
                log.exception("bad fan-out message")

    # -- connections -------------------------------------------------------

    def add(self, code: str, player_id: str, ws: WebSocket) -> None:
        self.sockets[code][player_id].add(ws)

    def remove(self, code: str, player_id: str, ws: WebSocket) -> bool:
        """Remove a socket; returns True if the player has no sockets left on this instance."""
        conns = self.sockets.get(code, {}).get(player_id)
        if conns is None:
            return True
        conns.discard(ws)
        return not conns

    # -- Publisher protocol --------------------------------------------------

    async def to_player(self, code: str, player_id: str, message: dict[str, Any]) -> None:
        if self._redis is not None:
            await self._redis.publish(f"havoc:{code}", json.dumps({"code": code, "player_id": player_id, "message": message}, default=str))
        else:
            await self._deliver_local(code, player_id, message)

    async def disconnect_player(self, code: str, player_id: str) -> None:
        if self._redis is not None:
            await self._redis.publish(f"havoc:{code}", json.dumps({"code": code, "player_id": player_id, "kick": True}))
        else:
            await self._deliver_local(code, player_id, None, kick=True)

    async def _deliver_local(self, code: str, player_id: str, message: dict[str, Any] | None, kick: bool = False) -> None:
        for ws in list(self.sockets.get(code, {}).get(player_id, ())):
            try:
                if kick:
                    await ws.send_json({"type": "kicked"})
                    await ws.close(code=4003)
                else:
                    await ws.send_text(json.dumps(message, default=str))
            except Exception:  # noqa: BLE001 - a dead socket is cleaned up by its handler
                self.sockets[code][player_id].discard(ws)
