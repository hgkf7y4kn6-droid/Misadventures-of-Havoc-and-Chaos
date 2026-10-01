"""Owns every live GameManager in this process and rehydrates games from the database."""

from __future__ import annotations

import asyncio
import logging

from .config import Settings
from .engine.game_manager import GameError, GameManager, make_code
from .engine.ports import Analytics, AsyncioScheduler, NullAnalytics, Publisher, Scheduler
from .llm.services import LLMService
from .models.events import RealtimeEvent as E
from .models.game import GameState, Phase
from .persistence.db import Repository
from .realtime.hub import Hub
from .tts.service import TTSService, voice_for

log = logging.getLogger("havoc.registry")


class GameRegistry:
    def __init__(self, settings: Settings, llm: LLMService, tts: TTSService, hub: Hub, repo: Repository, *,
                 publisher: Publisher | None = None, scheduler: Scheduler | None = None,
                 analytics: Analytics | None = None):
        self.settings = settings
        self.llm = llm
        self.tts = tts
        self.hub = hub
        self.repo = repo
        # Standalone: local hub + asyncio timers. Edge mode: an EdgeLink for both (sockets + alarms live in the DO).
        self.publisher: Publisher = publisher or hub
        self.scheduler: Scheduler = scheduler or AsyncioScheduler()
        self.analytics: Analytics = analytics or NullAnalytics()
        self.games: dict[str, GameManager] = {}
        self._lock = asyncio.Lock()
        from .identity import IdentityService

        self.identity = IdentityService(settings)

    def _manager(self, state: GameState) -> GameManager:
        return GameManager(state, self.llm, self.settings, self.publisher, self.repo, self.audio_hook, self.scheduler, self.analytics)

    async def create(self, host_name: str, game_settings: dict | None, *, user_id: str | None = None,
                     code: str | None = None) -> tuple[GameManager, str, str]:
        if code and (code in self.games or await self.repo.load(code)):
            raise GameError("That game code is taken.")
        mgr, host = GameManager.new(host_name, self.llm, self.settings, self.publisher, self.repo, game_settings, self.audio_hook,
                                    user_id=user_id, code=code, scheduler=self.scheduler, analytics=self.analytics)
        while not code and (mgr.state.code in self.games or await self.repo.load(mgr.state.code)):
            mgr.state.code = make_code()
        self.games[mgr.state.code] = mgr
        await self.repo.save(mgr.state)
        return mgr, host.id, host.token

    async def get(self, code: str) -> GameManager:
        code = code.upper().strip()
        if code in self.games:
            return self.games[code]
        async with self._lock:
            if code in self.games:
                return self.games[code]
            state = await self.repo.load(code)
            if state is None:
                raise GameError("No adventure with that code.")
            mgr = self._rehydrate(state)
            self.games[code] = mgr
            return mgr

    def _rehydrate(self, state: GameState) -> GameManager:
        for p in state.players.values():
            p.connected = False
        state.world_state["resolving"] = False
        mgr = self._manager(state)
        mgr.rearm()
        if state.phase == Phase.STORY_GENERATION:
            mgr._spawn(mgr._generate_final_story())
        return mgr

    async def audio_hook(self, state: GameState) -> None:
        """Called once the final story exists: pre-generate narration chapter by chapter."""
        mgr = self.games.get(state.code)
        story = state.final_story
        if not mgr or not story:
            return
        if not self.tts.server_side:
            state.audio_status = {"state": "client", "provider": "browser", "chapters": len(story.chapters), "ready": []}
            await mgr._commit()
            await mgr.sync()
            return
        voice = voice_for(state.settings.narrator_voice)
        state.audio_status = {"state": "generating", "provider": self.tts.provider.name, "voice": voice.voice_style.value,
                              "chapters": len(story.chapters), "ready": [], "failed": []}
        await mgr.emit(E.FINAL_AUDIO_GENERATION_STARTED, {"chapters": len(story.chapters)})
        await mgr.sync()

        async def on_chapter(i: int, ok: bool) -> None:
            state.audio_status["ready" if ok else "failed"].append(i)
            if ok:
                await mgr.emit(E.FINAL_AUDIO_CHAPTER_READY, {"chapter": i, "of": len(story.chapters)})
            await mgr.sync()

        await self.tts.generate_all(story, voice, on_chapter)
        state.audio_status["state"] = "complete"
        await mgr._commit()
        try:
            await self.repo.archive(state)
        except Exception:  # noqa: BLE001
            log.exception("archive after audio failed")
        await mgr.emit(E.FINAL_AUDIO_GENERATION_COMPLETE, {"ready": state.audio_status["ready"]})
        await mgr.sync()

    async def shutdown(self) -> None:
        for mgr in self.games.values():
            await mgr.shutdown()
