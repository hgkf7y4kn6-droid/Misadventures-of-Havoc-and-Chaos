"""Ports: everything the engine needs from its host runtime.

The engine is authoritative over rules, but it does not own sockets, clocks or
storage. Each deployment plugs in adapters:

=================  ==========================  ===========================================
Port               Standalone (FastAPI)        Cloudflare edge (Durable Object per game)
=================  ==========================  ===========================================
Publisher          realtime.hub.Hub            edge.link.EdgeLink  → DO sends on its sockets
Scheduler          AsyncioScheduler            edge.link.EdgeLink  → DO storage alarm
Store              persistence.db.Repository   persistence.db.Repository (Postgres via Hyperdrive)
Analytics          analytics.PostHogAnalytics  analytics.PostHogAnalytics
=================  ==========================  ===========================================

Timers are *armed* with an opaque token that is also persisted in
``GameState.timers``; a firing whose token no longer matches is stale and
ignored. That makes timers safe to deliver late, twice, or after a restart —
exactly the guarantees Durable Object alarms give (at-least-once).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from ..models.game import GameState

log = logging.getLogger("havoc.ports")

FireTimer = Callable[[str, str], Awaitable[None]]  # (tag, token)


class Publisher(Protocol):
    async def to_player(self, code: str, player_id: str, message: dict[str, Any]) -> None: ...
    async def disconnect_player(self, code: str, player_id: str) -> None: ...


class Store(Protocol):
    async def save(self, state: GameState) -> None: ...
    async def archive(self, state: GameState) -> None: ...


class Scheduler(Protocol):
    def arm(self, code: str, seconds: float, tag: str, token: str, fire: FireTimer) -> None: ...
    def cancel(self, code: str) -> None: ...


class Analytics(Protocol):
    def capture(self, event: str, distinct_id: str, properties: dict[str, Any] | None = None,
                groups: dict[str, str] | None = None) -> None: ...


# ---------------------------------------------------------------------------
# Default adapters
# ---------------------------------------------------------------------------


class NullPublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, dict[str, Any]]] = []

    async def to_player(self, code: str, player_id: str, message: dict[str, Any]) -> None:
        self.messages.append((code, player_id, message))

    async def disconnect_player(self, code: str, player_id: str) -> None:
        return None


class NullStore:
    async def save(self, state: GameState) -> None:
        return None

    async def archive(self, state: GameState) -> None:
        return None


class NullAnalytics:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any], dict[str, str]]] = []

    def capture(self, event: str, distinct_id: str, properties: dict[str, Any] | None = None,
                groups: dict[str, str] | None = None) -> None:
        self.events.append((event, distinct_id, dict(properties or {}), dict(groups or {})))


class AsyncioScheduler:
    """In-process timers for the standalone server (one task per game)."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    def arm(self, code: str, seconds: float, tag: str, token: str, fire: FireTimer) -> None:
        self.cancel(code)

        async def run() -> None:
            try:
                await asyncio.sleep(seconds)
                await fire(tag, token)
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                log.exception("timer %s for %s failed", tag, code)

        try:
            self._tasks[code] = asyncio.get_running_loop().create_task(run())
        except RuntimeError:  # no running loop (sync contexts)
            pass

    def cancel(self, code: str) -> None:
        task = self._tasks.pop(code, None)
        # Never cancel the task that is currently firing (it is the one re-arming).
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()


class ManualScheduler:
    """Records arms instead of sleeping; tests fire timers explicitly (like a DO alarm would)."""

    def __init__(self) -> None:
        self.armed: dict[str, tuple[float, str, str, FireTimer]] = {}

    def arm(self, code: str, seconds: float, tag: str, token: str, fire: FireTimer) -> None:
        self.armed[code] = (seconds, tag, token, fire)

    def cancel(self, code: str) -> None:
        self.armed.pop(code, None)

    async def fire(self, code: str) -> bool:
        item = self.armed.pop(code, None)
        if not item:
            return False
        _, tag, token, fire = item
        await fire(tag, token)
        return True
