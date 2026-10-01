"""The authoritative game engine.

One ``GameManager`` owns one ``GameState``. Every client action and every
timer goes through it under a per-game lock. The LLM is consulted for words,
never for rules: phases, timers, votes, rolls, resources, visibility,
permissions and outcomes are decided here.
"""

from __future__ import annotations

import asyncio
import logging
import math
import secrets
import string
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from ..config import GAME_TITLE, Settings
from ..llm.safety import clean_text, defang, looks_like_injection
from ..llm.services import DecisionSpec, LLMService, NarrationRequest, chapters_from_llm
from ..models import events as ev
from ..models.events import RealtimeEvent as E
from ..models.game import (
    AdventureLength,
    Character,
    ChatMessage,
    Choice,
    CommMode,
    DecisionGroup,
    DecisionKind,
    FinalStory,
    GameOutcomeKind,
    GameSettings,
    GameState,
    InfoItem,
    Item,
    Outcome,
    OutcomeTier,
    Phase,
    Player,
    PlayerStatus,
    Risk,
    Round,
    StoryEntry,
    Submission,
    ThemeSubmission,
    Visibility,
    now,
)
from . import achievements, chronicle, director, procgen, rng
from .resolution import (
    SUCCESSES,
    ActionOutcome,
    PlannedAction,
    change_shared,
    describe_delta,
    final_score,
    label,
    resolve_round,
    round_upkeep,
)
from .visibility import authorized_context, project, public_context

log = logging.getLogger("havoc.engine")


class GameError(Exception):
    """A rule violation reported back to the offending client."""


class Publisher(Protocol):
    async def to_player(self, code: str, player_id: str, message: dict[str, Any]) -> None: ...
    async def disconnect_player(self, code: str, player_id: str) -> None: ...


class Store(Protocol):
    async def save(self, state: GameState) -> None: ...
    async def archive(self, state: GameState) -> None: ...


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


def make_code() -> str:
    alphabet = "".join(c for c in string.ascii_uppercase if c not in "IO")
    return "".join(secrets.choice(alphabet) for _ in range(5))


class GameManager:
    def __init__(self, state: GameState, llm: LLMService, settings: Settings, publisher: Publisher, store: Store,
                 audio_hook: Callable[[GameState], Awaitable[None]] | None = None):
        self.state = state
        self.llm = llm
        self.settings = settings
        self.publisher = publisher
        self.store = store
        self.audio_hook = audio_hook
        self.lock = asyncio.Lock()
        self._timer: asyncio.Task | None = None
        self._timer_tag: str | None = None
        self._background: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ setup

    @classmethod
    def new(cls, host_name: str, llm: LLMService, settings: Settings, publisher: Publisher, store: Store,
            game_settings: dict | None = None, audio_hook=None) -> tuple[GameManager, Player]:
        gs = GameSettings(
            min_players=settings.min_players, max_players=settings.default_max_players,
            theme_submission_seconds=settings.theme_submission_seconds, theme_voting_seconds=settings.theme_voting_seconds,
            character_creation_seconds=settings.character_creation_seconds, decision_seconds=settings.decision_seconds,
        )
        state = GameState(code=make_code(), settings=gs, random_seed=secrets.randbits(48))
        mgr = cls(state, llm, settings, publisher, store, audio_hook)
        if game_settings:
            mgr._apply_settings(game_settings)
        host = mgr._add_player(host_name, is_host=True)
        return mgr, host

    def _add_player(self, name: str, is_host: bool = False) -> Player:
        name = clean_text(name, 32) or "Mystery Guest"
        existing = {p.name.lower() for p in self.state.players.values()}
        base, n = name, 2
        while name.lower() in existing:
            name, n = f"{base} {n}", n + 1
        p = Player(name=name, token=secrets.token_urlsafe(24), is_host=is_host)
        p.character.name = name
        p.character.avatar = rng.pick(self.state.random_seed, procgen.AVATARS, "avatar", len(self.state.players))
        p.character.color = rng.pick(self.state.random_seed, ["#f97316", "#a855f7", "#22c55e", "#eab308", "#ec4899", "#06b6d4", "#ef4444", "#84cc16"], "color", len(self.state.players))
        self.state.players[p.id] = p
        if is_host:
            self.state.host_id = p.id
        return p

    async def join(self, name: str) -> Player:
        async with self.lock:
            s = self.state
            if s.phase != Phase.LOBBY:
                raise GameError("This adventure has already started. Ask the host to restart it.")
            if len(s.active_players()) >= s.settings.max_players:
                raise GameError("The lobby is full.")
            p = self._add_player(name)
            await self._commit()
        await self.emit(E.PLAYER_JOINED, {"player_id": p.id, "name": p.name})
        await self.sync()
        return p

    def authenticate(self, token: str) -> Player:
        if token in self.state.kicked_tokens:
            raise GameError("You were removed from this game.")
        p = self.state.player_by_token(token)
        if not p or p.status == PlayerStatus.LEFT:
            raise GameError("Unknown player token.")
        return p

    async def set_connected(self, player_id: str, connected: bool) -> None:
        p = self.state.players.get(player_id)
        if not p:
            return
        p.connected = connected
        if not connected:
            await self.emit(E.PLAYER_LEFT, {"player_id": player_id, "temporary": True})
        await self.sync()

    # ------------------------------------------------------------ messaging

    async def emit(self, event: E, data: dict[str, Any] | None = None, audience: list[str] | None = None) -> None:
        msg = {"type": "event", "event": event.value, "data": data or {}, "ts": time.time()}
        targets = audience if audience is not None else list(self.state.players)
        for pid in targets:
            p = self.state.players.get(pid)
            if p and p.connected:
                await self.publisher.to_player(self.state.code, pid, msg)

    async def sync(self, only: list[str] | None = None) -> None:
        for pid, p in list(self.state.players.items()):
            if (only is None or pid in only) and p.connected and p.status != PlayerStatus.LEFT:
                await self.publisher.to_player(self.state.code, pid, {"type": "state", "state": project(self.state, pid)})

    async def _commit(self) -> None:
        self.state.version += 1
        try:
            # shielded: a client disconnecting mid-write must not cancel a DB transaction
            await asyncio.shield(self.store.save(self.state))
        except Exception:  # noqa: BLE001 - persistence trouble must not freeze a live table
            log.exception("failed to persist game %s", self.state.code)

    def _system(self, text: str, title: str = "", visibility: Visibility = Visibility.PUBLIC,
                audience: list[str] | None = None, kind: str = "system") -> StoryEntry:
        entry = StoryEntry(round=self.state.turn_number, kind=kind, title=title, text=text, visibility=visibility,
                           audience=audience or [])
        self.state.story_history.append(entry)
        return entry

    # ------------------------------------------------------------ dispatch

    async def handle(self, player_id: str, msg: Any) -> None:
        """Entry point for every validated client message."""
        p = self.state.players.get(player_id)
        if not p or p.status == PlayerStatus.LEFT:
            raise GameError("You are not in this game.")
        if isinstance(msg, ev.Ping):
            return
        if isinstance(msg, ev.SendChat):
            return await self.chat(p, msg)
        if isinstance(msg, ev.SetPreferences):
            return await self.set_preferences(p, msg)
        handlers = {
            ev.SetReady: self.set_ready, ev.UpdateLobbyProfile: self.update_profile, ev.UpdateSettings: self.update_settings,
            ev.StartGame: self.start_game, ev.KickPlayer: self.kick, ev.RestartGame: self.restart,
            ev.SubmitTheme: self.submit_theme, ev.CloseThemes: self.close_phase, ev.CastVote: self.cast_vote,
            ev.SaveCharacter: self.save_character, ev.SuggestCharacter: self.suggest_character,
            ev.SubmitDecision: self.submit_decision,
        }
        handler = handlers.get(type(msg))
        if handler is None:
            raise GameError("Unsupported action.")
        await handler(p, msg)

    def _require_host(self, p: Player) -> None:
        if p.id != self.state.host_id:
            raise GameError("Only the host can do that.")

    def _require_phase(self, *phases: Phase) -> None:
        if self.state.phase not in phases:
            raise GameError(f"Not allowed during {self.state.phase.value.replace('_', ' ')}.")

    # ------------------------------------------------------------ lobby

    async def set_ready(self, p: Player, msg: ev.SetReady) -> None:
        async with self.lock:
            self._require_phase(Phase.LOBBY)
            p.ready = msg.ready
            await self._commit()
        await self.emit(E.PLAYER_READY, {"player_id": p.id, "ready": p.ready})
        await self.sync()

    async def update_profile(self, p: Player, msg: ev.UpdateLobbyProfile) -> None:
        async with self.lock:
            self._require_phase(Phase.LOBBY, Phase.CHARACTER_CREATION)
            if msg.name:
                p.name = clean_text(msg.name, 32) or p.name
                p.character.name = p.name
            if msg.avatar:
                p.character.avatar = clean_text(msg.avatar, 8)
            if msg.color:
                p.character.color = msg.color
            if msg.archetype is not None:
                p.character.archetype = clean_text(msg.archetype, 80)
            await self._commit()
        await self.emit(E.CHARACTER_UPDATED, {"player_id": p.id})
        await self.sync()

    def _apply_settings(self, raw: dict) -> None:
        s = self.state.settings
        data = s.model_dump()
        allowed = {"max_players", "adventure_length", "theme_submission_seconds", "theme_voting_seconds",
                   "character_creation_seconds", "decision_seconds", "default_comm_mode", "allow_public_share",
                   "narrator_voice", "auto_advance", "min_players"}
        for k, v in raw.items():
            if k in allowed:
                data[k] = v
        new = GameSettings.model_validate(data)
        new.max_players = max(2, min(self.settings.max_players_limit, new.max_players))
        new.min_players = max(self.settings.min_players, min(new.max_players, new.min_players))
        for key in ("theme_submission_seconds", "theme_voting_seconds", "character_creation_seconds", "decision_seconds"):
            setattr(new, key, max(5, min(900, getattr(new, key))))
        self.state.settings = new

    async def update_settings(self, p: Player, msg: ev.UpdateSettings) -> None:
        async with self.lock:
            self._require_host(p)
            self._require_phase(Phase.LOBBY)
            try:
                self._apply_settings(msg.settings)
            except Exception as exc:  # noqa: BLE001
                raise GameError(f"Invalid settings: {exc}") from exc
            await self._commit()
        await self.emit(E.SETTINGS_CHANGED, {"settings": self.state.settings.model_dump()})
        await self.sync()

    async def kick(self, p: Player, msg: ev.KickPlayer) -> None:
        async with self.lock:
            self._require_host(p)
            target = self.state.players.get(msg.player_id)
            if not target or target.id == p.id:
                raise GameError("Cannot remove that player.")
            self.state.kicked_tokens.append(target.token)
            if self.state.phase == Phase.LOBBY:
                del self.state.players[target.id]
            else:
                target.status = PlayerStatus.LEFT
                target.connected = False
            await self._commit()
        await self.publisher.disconnect_player(self.state.code, target.id)
        await self.emit(E.PLAYER_KICKED, {"player_id": target.id, "name": target.name})
        await self.sync()
        await self._maybe_advance()

    async def start_game(self, p: Player, msg: ev.StartGame | None = None) -> None:
        async with self.lock:
            self._require_host(p)
            self._require_phase(Phase.LOBBY)
            players = self.state.active_players()
            if len(players) < self.state.settings.min_players:
                raise GameError(f"Need at least {self.state.settings.min_players} players to start.")
            not_ready = [x.name for x in players if not x.ready and x.id != p.id]
            if not_ready:
                raise GameError("Waiting for: " + ", ".join(not_ready))
            p.ready = True
            self._enter(Phase.THEME_SUBMISSION, self.state.settings.theme_submission_seconds)
            self._system(f"Welcome to {GAME_TITLE}. Everybody: pitch a story theme. Nobody can see your pitch until submissions close.",
                         "Theme Submissions Open")
            await self._commit()
        await self.emit(E.GAME_STARTED, {"phase": self.state.phase})
        await self.sync()

    async def restart(self, p: Player, msg: ev.RestartGame | None = None) -> None:
        async with self.lock:
            self._require_host(p)
            self._require_phase(Phase.ENDED, Phase.LOBBY)
            self._cancel_timer()
            old = self.state
            fresh = GameState(code=old.code, settings=old.settings, random_seed=secrets.randbits(48), host_id=old.host_id,
                              kicked_tokens=old.kicked_tokens)
            for pl in old.active_players():
                fresh.players[pl.id] = Player(id=pl.id, token=pl.token, name=pl.name, is_host=pl.is_host,
                                              connected=pl.connected, joined_at=pl.joined_at, preferences=pl.preferences,
                                              character=Character(name=pl.name, avatar=pl.character.avatar, color=pl.character.color))
            self.state = fresh
            await self._commit()
        await self.emit(E.GAME_RESTARTED, {})
        await self.sync()

    # ------------------------------------------------------------ timers

    def _enter(self, phase: Phase, seconds: int | None) -> None:
        self.state.phase = phase
        self.state.timers.phase_started_at = now()
        self.state.timers.phase_deadline = now() + seconds if seconds else None
        self._cancel_timer()
        if seconds and self.state.settings.auto_advance:
            self._schedule(seconds, phase.value)

    def _cancel_timer(self) -> None:
        if self._timer and not self._timer.done() and self._timer is not asyncio.current_task():
            self._timer.cancel()
        self._timer = None
        self._timer_tag = None

    def _schedule(self, seconds: float, tag: str) -> None:
        version_tag = f"{tag}:{self.state.turn_number}:{time.monotonic()}"
        self._timer_tag = version_tag

        async def fire() -> None:
            try:
                await asyncio.sleep(seconds)
                if self._timer_tag == version_tag:
                    await self.on_timeout(tag)
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                log.exception("timer %s failed", tag)

        try:
            self._timer = asyncio.get_running_loop().create_task(fire())
        except RuntimeError:  # no loop (sync tests)
            self._timer = None

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def on_timeout(self, tag: str) -> None:
        phase = self.state.phase
        if tag != phase.value and not (tag == "resolution_pause" and phase == Phase.ADVENTURE):
            return
        if phase == Phase.THEME_SUBMISSION:
            await self._close_theme_submissions()
        elif phase == Phase.THEME_VOTING:
            await self._close_voting()
        elif phase == Phase.OBJECTIVE_REVEAL:
            await self._begin_character_creation()
        elif phase == Phase.CHARACTER_CREATION:
            await self._finish_character_creation()
        elif phase in (Phase.ADVENTURE, Phase.FINAL_CHALLENGE):
            if tag == "resolution_pause" or (phase == Phase.ADVENTURE and not self.state.pending_decisions):
                await self._next_round()
            else:
                await self._resolve_current(timeout=True)

    async def close_phase(self, p: Player, msg: Any = None) -> None:
        """Host override: close the current phase now (helps when someone wandered off)."""
        self._require_host(p)
        async with self.lock:
            pass  # let any in-flight resolution finish so we act on settled state
        await self.on_timeout(self.state.phase.value)

    async def _maybe_advance(self) -> None:
        s = self.state
        active = [x.id for x in s.active_players()]
        if s.phase == Phase.THEME_SUBMISSION and all(pid in s.theme_submissions for pid in active):
            await self._close_theme_submissions()
        elif s.phase == Phase.THEME_VOTING and all(pid in s.theme_votes for pid in active):
            await self._close_voting()
        elif s.phase == Phase.CHARACTER_CREATION and all(s.players[pid].character.complete for pid in active):
            await self._finish_character_creation()
        elif s.phase in (Phase.ADVENTURE, Phase.FINAL_CHALLENGE) and s.pending_decisions and all(
            all(pid in g.submissions or s.players[pid].status == PlayerStatus.LEFT for pid in g.player_ids)
            for g in s.pending_decisions.values()
        ):
            await self._resolve_current(timeout=False)

    # ------------------------------------------------------------ themes

    async def submit_theme(self, p: Player, msg: ev.SubmitTheme) -> None:
        async with self.lock:
            self._require_phase(Phase.THEME_SUBMISSION)
            text = clean_text(msg.text, 200)
            if len(text) < 3:
                raise GameError("That theme is a little too short.")
            self.state.theme_submissions[p.id] = ThemeSubmission(player_id=p.id, text=defang(text))
            await self._commit()
        await self.emit(E.THEME_SUBMITTED, {"player_id": p.id})
        await self.sync()
        await self._maybe_advance()

    async def _close_theme_submissions(self) -> None:
        async with self.lock:
            if self.state.phase != Phase.THEME_SUBMISSION:
                return
            s = self.state
            subs = list(s.theme_submissions.values())
            if not subs:
                subs = [ThemeSubmission(player_id=s.host_id or "", text=t) for t in (
                    "Medieval knights attempting to deliver a pizza before it gets cold",
                    "Office workers trapped inside a haunted Costco")]
            self._cancel_timer()
            s.phase = Phase.THEME_VOTING  # block re-entry while merging
            s.theme_options = await self.llm.theme_merger.merge(subs)
            self._enter(Phase.THEME_VOTING, s.settings.theme_voting_seconds)
            merged = sum(len(o.originals) - 1 for o in s.theme_options)
            self._system(
                f"Submissions are closed. {len(subs)} pitches became {len(s.theme_options)} options"
                + (f" ({merged} great minds thought alike and were merged)." if merged else ".")
                + " Vote in secret.", "Theme Voting")
            await self._commit()
        await self.emit(E.THEME_SUBMISSIONS_CLOSED, {"count": len(self.state.theme_options)})
        await self.emit(E.THEME_VOTING_STARTED, {"options": [o.title for o in self.state.theme_options]})
        await self.sync()
        if len(self.state.theme_options) == 1:
            await self._close_voting()

    async def cast_vote(self, p: Player, msg: ev.CastVote) -> None:
        async with self.lock:
            self._require_phase(Phase.THEME_VOTING)
            if msg.option_id not in {o.id for o in self.state.theme_options}:
                raise GameError("That is not one of the options.")
            self.state.theme_votes[p.id] = msg.option_id
            await self._commit()
        await self.emit(E.THEME_VOTE_CAST, {"player_id": p.id})
        await self.sync()
        await self._maybe_advance()

    async def _close_voting(self) -> None:
        async with self.lock:
            s = self.state
            if s.phase != Phase.THEME_VOTING:
                return
            self._cancel_timer()
            s.phase = Phase.OBJECTIVE_REVEAL
            from .themes import tally

            winner, counts = tally(s.theme_options, s.theme_votes, s.random_seed)
            s.theme = winner.title
            s.theme_tally = counts
            n = len(s.active_players())
            s.total_rounds = director.total_rounds(s.settings.adventure_length)
            bundle = await self.llm.objective_generator.generate(s.theme, s.random_seed, s.total_rounds, n)
            obj = bundle.objective
            obj.progress_target = max(3, math.ceil(0.4 * s.total_rounds * n))
            s.objective = obj
            s.success_conditions = obj.success_conditions
            s.failure_conditions = obj.failure_conditions
            s.shared_resources = procgen.make_resources(s.theme, n, s.total_rounds)
            for key, lbl in bundle.resource_labels.items():
                if key in s.shared_resources and lbl:
                    s.shared_resources[key].label = lbl
            s.locations = {loc.id: loc for loc in bundle.locations}
            s.npcs = {npc.id: npc for npc in bundle.npcs}
            s.hidden_variables = bundle.hidden_variables
            s.world_state = {"scars": [], "world_rules": obj.world_rules}
            s.memory.world_facts = list(obj.world_rules)
            s.memory.long_term_facts = [f"Objective: {obj.title}", f"Antagonist: {obj.antagonist}", f"Macguffin: {obj.macguffin}"]
            for hv in s.hidden_variables:
                s.hidden_information[hv.id] = InfoItem(visibility=Visibility.ENDGAME_REVEAL, audience=[], kind="hidden_truth",
                                                       title="Hidden Truth", text=hv.fact)
            self._enter(Phase.OBJECTIVE_REVEAL, self.settings.objective_reveal_seconds)
            self._system(f"{obj.description}", f"The Objective: {obj.title}", kind="objective")
            chronicle.record(s, "objective", importance=4, public_information=obj.description,
                             narrative_summary=f"The party chose the theme \"{s.theme}\" and set out to {obj.title.lower()}: {obj.description}")
            await self._commit()
        await self.emit(E.THEME_SELECTED, {"theme": self.state.theme, "tally": self.state.theme_tally})
        await self.emit(E.OBJECTIVE_REVEALED, {"objective": self.state.objective.model_dump() if self.state.objective else None})
        await self.sync()

    # ------------------------------------------------------------ characters

    async def _begin_character_creation(self) -> None:
        async with self.lock:
            if self.state.phase != Phase.OBJECTIVE_REVEAL:
                return
            self._enter(Phase.CHARACTER_CREATION, self.state.settings.character_creation_seconds)
            await self._commit()
        await self.emit(E.CHARACTER_CREATION_STARTED, {})
        await self.sync()

    async def suggest_character(self, p: Player, msg: Any = None) -> None:
        self._require_phase(Phase.CHARACTER_CREATION, Phase.OBJECTIVE_REVEAL, Phase.LOBBY)
        key = f"{p.id}:{len(p.character.archetype)}:{time.time_ns() % 1000}"
        ch = await self.llm.character_generator.suggest(self.state.theme or "", self.state.objective, p.name, self.state.random_seed, key)
        await self.publisher.to_player(self.state.code, p.id, {"type": "character_suggestion", "character": ch.model_dump()})

    async def save_character(self, p: Player, msg: ev.SaveCharacter) -> None:
        async with self.lock:
            self._require_phase(Phase.CHARACTER_CREATION, Phase.OBJECTIVE_REVEAL)
            fields = {k: clean_text(getattr(msg, k), 200) for k in (
                "name", "archetype", "personality", "special_ability", "weakness", "secret_motivation", "starting_item", "humorous_trait")}
            ch = Character(**fields, avatar=clean_text(msg.avatar, 8) or p.character.avatar, color=p.character.color, complete=True)
            ch.name = ch.name or p.name
            ch.stats = self._validate_stats(msg.stats) or procgen.default_stats_for(ch.archetype, ch.special_ability)
            p.character = ch
            p.inventory = [i for i in p.inventory if "starting" not in i.tags]
            if ch.starting_item:
                p.inventory.insert(0, Item(name=ch.starting_item, description="Brought from home, for some reason.", tags=["starting"]))
            await self._commit()
        await self.emit(E.CHARACTER_UPDATED, {"player_id": p.id})
        await self.sync()
        await self._maybe_advance()

    @staticmethod
    def _validate_stats(stats: dict[str, int] | None) -> dict[str, int] | None:
        if not stats:
            return None
        keys = ("brawn", "brains", "charm", "sneak", "weird")
        clean = {k: max(0, min(3, int(stats.get(k, 0)))) for k in keys}
        if sum(clean.values()) > 8:
            return None
        return clean

    async def _finish_character_creation(self) -> None:
        async with self.lock:
            s = self.state
            if s.phase != Phase.CHARACTER_CREATION:
                return
            self._cancel_timer()
            for p in s.active_players():
                if not p.character.complete:
                    sug = await self.llm.character_generator.suggest(s.theme or "", s.objective, p.name, s.random_seed, p.id)
                    sug.avatar, sug.color, sug.complete = p.character.avatar, p.character.color, True
                    p.character = sug
                    if sug.starting_item:
                        p.inventory.insert(0, Item(name=sug.starting_item, tags=["starting"]))
                    self._system("You dawdled, so the universe made your character for you.", "Character Assigned",
                                 Visibility.PRIVATE, [p.id])
                s.memory.character_memories[p.id] = [f"I am {p.character.name}, {p.character.archetype}."]
            party = ", ".join(f"{p.character.avatar} {p.display} the {p.character.archetype}" for p in s.active_players())
            chronicle.record(s, "party_formed", importance=3, player_ids=[p.id for p in s.active_players()],
                             narrative_summary=f"The party assembled: {party}.")
            s.phase = Phase.ADVENTURE
            await self._commit()
        await self._next_round()

    # ------------------------------------------------------------ rounds

    async def _next_round(self) -> None:
        async with self.lock:
            s = self.state
            if s.phase != Phase.ADVENTURE or s.pending_decisions:
                return
            self._cancel_timer()
            doom = director.is_doomed(s)
            if doom:
                await self._finish_game(doom_reason=doom)
            elif director.should_start_finale(s):
                await self._start_final_challenge()
            else:
                s.turn_number += 1
                await self._start_round(director.plan_round(s, s.turn_number))
                await self._commit()
        await self.sync()

    async def _start_round(self, plan: director.RoundPlan) -> None:
        s = self.state
        seed = s.random_seed
        rnd = s.turn_number
        locations = list(s.locations.values())
        main_loc = locations[(rnd - 1) % len(locations)] if locations else None
        npcs = list(s.npcs.values())
        npc = npcs[(rnd - 1) % len(npcs)] if npcs else None

        obj = s.objective
        ctx = {"loc": main_loc.name if main_loc else "the unknown", "threat": obj.antagonist if obj else "something",
               "mac": obj.macguffin if obj else "the thing", "npc": npc.name if npc else "A stranger",
               "setting": procgen.profile_theme(s.theme or "").setting, "obj": obj.description if obj else "",
               "final": obj.final_challenge if obj else "The end"}

        # Delayed consequences land first: the past catches up.
        for dc in s.delayed_consequences:
            if not dc.fired and dc.trigger_round <= rnd:
                dc.fired = True
                changes = {k: change_shared(s, k, v) for k, v in dc.resource_changes.items()}
                text = f"Remember {dc.description.split(' comes back')[0]}? It comes back to haunt everyone. " + ", ".join(
                    describe_delta(s, k, v) for k, v in changes.items() if v)
                self._system(text, "Consequences Arrive", kind="event")
                cause = next((e for e in reversed(s.adventure_chronicle) if set(dc.player_ids) <= set(e.player_ids)
                              and e.event_type in ("action", "group_action")), None)
                chronicle.record(s, "delayed_consequence", player_ids=dc.player_ids, importance=3, resource_changes=changes,
                                 narrative_summary=text, related_events=[cause.event_id] if cause else [],
                                 consequences=[f"[link] Because of {', '.join(s.players[p].display for p in dc.player_ids if p in s.players)}'s earlier choice, the whole party paid the price."] if cause else [])
                await self.emit(E.RESOURCE_CHANGED, {"changes": changes, "reason": "delayed consequence"})

        title, scene = await self.llm.scene_generator.scene(seed, plan.beat, rnd, ctx, public_context(s))
        s.current_scene = scene
        round_obj = Round(number=rnd, beat=plan.beat, title=title, scene_text=scene, comm_mode=plan.comm_mode, escalation=plan.escalation)
        s.rounds.append(round_obj)
        s.comm_mode = plan.comm_mode
        self._system(scene, f"Scene {rnd}: {title}", kind="scene")
        chronicle.record(s, "scene", importance=2, location=main_loc.name if main_loc else None,
                         public_information=scene, narrative_summary=scene)

        if rnd == 1 and npc:
            npc.known_by = [p.id for p in s.active_players()]
            text = f"{npc.emoji} {npc.name} appears. {npc.name} {npc.personality}. Their goal: {npc.goal}."
            self._system(text, "A New Face", kind="event")
            chronicle.record(s, "npc_introduced", npc_ids=[npc.id], importance=2, narrative_summary=text)
            procgen_joke = npc.name.split()[0].lower()
            director.register_joke(s, procgen_joke, f"{npc.name}, who {npc.personality}", rnd, None)

        # Random event
        if plan.random_event:
            re_ = plan.random_event
            changes = {k: change_shared(s, k, v) for k, v in re_.resource_changes.items()}
            re_.resource_changes = {k: v for k, v in changes.items() if v}
            s.active_events.append(re_)
            round_obj.random_event_id = re_.id
            effect = ", ".join(describe_delta(s, k, v) for k, v in re_.resource_changes.items())
            text = re_.text + (f" ({effect})" if effect else "")
            self._system(text, f"Random Event: {re_.title}", re_.visibility, re_.player_ids, kind="event")
            cev = chronicle.record(s, "random_event", player_ids=re_.player_ids, importance=3 if re_.category == "callback" else 2,
                                   visibility=re_.visibility, resource_changes=re_.resource_changes, narrative_summary=f"{re_.title}: {text}",
                                   tags=[re_.category, re_.subject])
            if re_.visibility == Visibility.PRIVATE:
                s.hidden_information[re_.id] = InfoItem(visibility=Visibility.PRIVATE, audience=re_.player_ids, kind="event",
                                                        title=re_.title, text=re_.text, round=rnd, source_event_id=cev.event_id)
            if re_.category in ("absurd", "callback", "harmful", "neutral") and re_.subject:
                director.register_joke(s, re_.subject, f"{re_.title}: {re_.text}", rnd, cev.event_id)
            await self.emit(E.RANDOM_EVENT, {"title": re_.title, "text": text}, audience=re_.player_ids or None)
            if re_.resource_changes:
                await self.emit(E.RESOURCE_CHANGED, {"changes": re_.resource_changes, "reason": re_.title})

        # Decision groups
        specs, groups = [], []
        indiv = rng.shuffled(seed, procgen.individual_situations(ctx), "indiv", rnd)
        grp = rng.shuffled(seed, procgen.group_situations(ctx), "grp", rnd)
        deadline = now() + s.settings.decision_seconds
        for gi, members in enumerate(plan.groups):
            loc = locations[(rnd + gi) % len(locations)] if locations else None
            gctx = dict(ctx, loc=loc.name if loc else ctx["loc"])
            kind = DecisionKind.GROUP if len(members) > 1 else DecisionKind.INDIVIDUAL
            pool = procgen.group_situations(gctx) if kind == DecisionKind.GROUP else procgen.individual_situations(gctx)
            base = grp if kind == DecisionKind.GROUP else indiv
            title0 = base[gi % len(base)][0]
            stitle, scontext, choices = next(x for x in pool if x[0] == title0)
            choices = [c.model_copy(deep=True) for c in choices]
            g_npc = npcs[(rnd + gi) % len(npcs)] if npcs else None
            if g_npc and g_npc.name in scontext:
                for c in choices:
                    if "negotiate" in c.tags or "steal" in c.tags:
                        c.target_npc_id = g_npc.id
                for pid in members:
                    if pid not in g_npc.known_by:
                        g_npc.known_by.append(pid)
            # Plant a hunch toward an undiscovered hidden truth.
            undiscovered = [hv for hv in s.hidden_variables if not set(members) & set(hv.discovered_by)]
            if undiscovered and rng.chance(seed, 0.4, "hunch", rnd, gi):
                hv = rng.pick(seed, undiscovered, "hunch_pick", rnd, gi)
                kw = hv.trigger_keywords[0]
                choices.append(Choice(label=f"Follow a weird hunch about the {kw}", tags=["investigate"], risk=Risk.RISKY,
                                      stat="brains", description=f"Something about the {kw} keeps nagging at you."))
            if plan.spotlight in members and kind == DecisionKind.INDIVIDUAL:
                me = s.players[plan.spotlight]
                scontext += f" (This feels personal. Your weakness — {me.character.weakness.lower() or 'everything'} — is tingling.)"
            for pid in members:
                s.players[pid].location_id = loc.id if loc else None
            g = DecisionGroup(round=rnd, kind=kind, player_ids=list(members), location_id=loc.id if loc else None,
                              title=stitle, shared_context=scontext, available_choices=choices, deadline=deadline,
                              comm_mode=plan.comm_mode)
            groups.append(g)
            specs.append(DecisionSpec(
                group_id=g.group_id, kind=kind.value, player_names=[s.players[p].display for p in members],
                location=loc.name if loc else "", authorized_context=authorized_context(s, list(members)),
                fallback_title=stitle, fallback_context=scontext, fallback_choices=choices))
        generated = await self.llm.decision_generator.generate(plan.beat, specs)
        for g in groups:
            t, c, ch = generated[g.group_id]
            if self.llm.provider is not None:
                # keep the planted hunch even when the LLM rewrites the choices
                hunch = [x for x in g.available_choices if x.label.startswith("Follow a weird hunch")]
                g.available_choices = ch + hunch
            g.title, g.shared_context = t, c
            s.pending_decisions[g.group_id] = g
            round_obj.group_ids.append(g.group_id)

        self._enter(Phase.ADVENTURE, s.settings.decision_seconds)
        await self.emit(E.SCENE_STARTED, {"round": rnd, "title": title, "beat": plan.beat, "comm_mode": plan.comm_mode})
        for g in groups:
            if g.kind == DecisionKind.GROUP:
                await self.emit(E.GROUP_FORMED, {"group_id": g.group_id, "player_ids": g.player_ids}, audience=g.player_ids)
                await self.emit(E.GROUP_DECISION_STARTED, {"group_id": g.group_id, "title": g.title}, audience=g.player_ids)
            else:
                await self.emit(E.DECISION_STARTED, {"group_id": g.group_id, "title": g.title}, audience=g.player_ids)

    async def _start_final_challenge(self) -> None:
        s = self.state
        s.turn_number += 1
        plan = director.plan_final(s)
        obj = s.objective
        ctx = {"loc": "the heart of it all", "threat": obj.antagonist if obj else "", "mac": obj.macguffin if obj else "",
               "npc": "", "setting": "", "obj": "", "final": obj.final_challenge if obj else "The end"}
        title, scene = procgen.scene_text(s.random_seed, "final_challenge", s.turn_number, ctx)
        if s.world_state.get("time_ran_out"):
            scene = "Time has run out. Ready or not — mostly not — " + scene[0].lower() + scene[1:]
        elif s.world_state.get("morale_broke"):
            scene = ("Morale has collapsed. Half the party is arguing in a group chat they are all physically standing next to. "
                     "There is no more time for plans. " + scene)
        s.current_scene = scene
        s.rounds.append(Round(number=s.turn_number, beat="final_challenge", title=title, scene_text=scene, comm_mode=CommMode.OPEN))
        s.comm_mode = CommMode.OPEN
        self._system(scene, f"The Final Challenge: {title}", kind="scene")
        chronicle.record(s, "scene", importance=3, public_information=scene, narrative_summary=scene)
        deadline = now() + s.settings.decision_seconds
        for pid in plan.groups[0]:
            p = s.players[pid]
            hv_note = " You know something the others might not: " + "; ".join(
                hv.hint for hv in s.hidden_variables if pid in hv.discovered_by) if any(pid in hv.discovered_by for hv in s.hidden_variables) else ""
            g = DecisionGroup(round=s.turn_number, kind=DecisionKind.FINAL, player_ids=[pid], title="Your Part in the Finale",
                              shared_context=f"{scene} What do you do?{hv_note}",
                              available_choices=procgen.final_choices(obj) if obj else [], deadline=deadline, comm_mode=CommMode.OPEN)
            s.pending_decisions[g.group_id] = g
            s.rounds[-1].group_ids.append(g.group_id)
            if not p.connected:
                pass
        s.phase = Phase.FINAL_CHALLENGE
        self._enter(Phase.FINAL_CHALLENGE, s.settings.decision_seconds)
        await self._commit()
        await self.emit(E.FINAL_CHALLENGE_STARTED, {"title": title})

    # ------------------------------------------------------------ decisions

    async def submit_decision(self, p: Player, msg: ev.SubmitDecision) -> None:
        async with self.lock:
            self._require_phase(Phase.ADVENTURE, Phase.FINAL_CHALLENGE)
            g = self.state.pending_decisions.get(msg.group_id)
            if not g or p.id not in g.player_ids:
                raise GameError("That decision isn't yours to make.")
            if g.resolved or self.state.world_state.get("resolving"):
                raise GameError("Too late — this decision is already being resolved.")
            if msg.choice_id and msg.choice_id not in {c.id for c in g.available_choices}:
                raise GameError("Unknown choice.")
            freeform = clean_text(msg.freeform or "", 300)
            if not msg.choice_id and not freeform:
                raise GameError("Pick a choice or describe what you do.")
            if freeform and not g.allow_freeform:
                raise GameError("No improvising here.")
            if msg.item_id and not any(i.id == msg.item_id for i in p.inventory):
                raise GameError("You don't have that item.")
            if msg.push_luck and p.resources.luck <= 0:
                raise GameError("You're out of luck. Literally.")
            if msg.use_ability and p.resources.ability_charges <= 0:
                raise GameError("Your special ability is exhausted.")
            g.submissions[p.id] = Submission(player_id=p.id, choice_id=msg.choice_id, freeform=freeform or None,
                                             push_luck=msg.push_luck, use_ability=msg.use_ability, item_id=msg.item_id)
            if freeform and looks_like_injection(msg.freeform or ""):
                log.info("possible prompt injection from %s defanged", p.id)
            await self._commit()
        await self.emit(E.DECISION_SUBMITTED, {"group_id": g.group_id, "player_id": p.id}, audience=g.player_ids)
        await self.sync(only=g.player_ids)
        await self._maybe_advance()

    def _auto_submit(self, g: DecisionGroup) -> None:
        for pid in g.player_ids:
            if pid not in g.submissions and g.available_choices:
                safest = min(g.available_choices, key=lambda c: (["safe", "risky", "wild"].index(c.risk.value), c.id))
                g.submissions[pid] = Submission(player_id=pid, choice_id=safest.id, auto=True)

    async def _interpret(self, g: DecisionGroup, sub: Submission) -> PlannedAction:
        s = self.state
        p = s.players[sub.player_id]
        if sub.freeform:
            if sub.interpretation is None:
                names = {x.id: x.display for x in s.active_players() if x.id != p.id}
                npc_names = {n.id: n.name for n in s.npcs.values() if p.id in n.known_by}
                obj_words = procgen.keywords(f"{s.objective.title} {s.objective.macguffin}") if s.objective else []
                sub.interpretation = await self.llm.action_interpreter.interpret(
                    sub.freeform, p.display, f"{p.character.archetype}; ability {p.character.special_ability}; weakness {p.character.weakness}",
                    g.shared_context, names, npc_names, obj_words)
            it = sub.interpretation
            return PlannedAction(key=f"{g.group_id}:{p.id}", group_id=g.group_id, player_ids=[p.id], description=sub.freeform,
                                 tags=it.tags, risk=it.risk, stat=it.stat, cost=it.cost, target_player_id=it.target_player_id,
                                 target_npc_id=it.target_npc_id, advances_objective=it.advances_objective, feasible=it.feasible,
                                 push_luck=[p.id] if sub.push_luck else [], use_ability=[p.id] if sub.use_ability else [],
                                 item_ids={p.id: sub.item_id} if sub.item_id else {}, location_id=g.location_id,
                                 final=g.kind == DecisionKind.FINAL, freeform=True)
        c = next(c for c in g.available_choices if c.id == sub.choice_id)
        desc = c.label if not sub.auto else f"{c.label} (after hesitating so long the decision made itself)"
        return PlannedAction(key=f"{g.group_id}:{p.id}", group_id=g.group_id, player_ids=[p.id], description=desc,
                             tags=c.tags, risk=c.risk, stat=c.stat, cost=dict(c.cost), target_npc_id=c.target_npc_id,
                             advances_objective=c.advances_objective, push_luck=[p.id] if sub.push_luck else [],
                             use_ability=[p.id] if sub.use_ability else [], item_ids={p.id: sub.item_id} if sub.item_id else {},
                             location_id=g.location_id, final=g.kind == DecisionKind.FINAL)

    async def _plan_group(self, g: DecisionGroup) -> list[PlannedAction]:
        """Group decisions: plurality wins; dissenters who wrote their own action go rogue."""
        s = self.state
        subs = [g.submissions[pid] for pid in g.player_ids if pid in g.submissions and s.players[pid].status != PlayerStatus.LEFT]
        if not subs:
            return []
        if g.kind != DecisionKind.GROUP:
            return [await self._interpret(g, sub) for sub in subs]
        votes: dict[str, list[Submission]] = {}
        for sub in subs:
            key = sub.choice_id or f"free:{sub.freeform.lower().strip() if sub.freeform else ''}"
            votes.setdefault(key, []).append(sub)
        top = max(len(v) for v in votes.values())
        tied = sorted(k for k, v in votes.items() if len(v) == top)
        winner = tied[0] if len(tied) == 1 else rng.pick(s.random_seed, tied, "group_tie", g.group_id)
        winners = votes[winner]
        lead = await self._interpret(g, winners[0])
        followers = [sub for sub in subs if sub not in winners and not sub.freeform]
        lead.player_ids = [sub.player_id for sub in winners] + [sub.player_id for sub in followers]
        lead.key = f"{g.group_id}:group"
        for sub in subs:
            if sub.push_luck and sub.player_id in lead.player_ids:
                lead.push_luck.append(sub.player_id)
            if sub.use_ability and sub.player_id in lead.player_ids:
                lead.use_ability.append(sub.player_id)
            if sub.item_id and sub.player_id in lead.player_ids:
                lead.item_ids[sub.player_id] = sub.item_id
        lead.push_luck = list(dict.fromkeys(lead.push_luck))
        lead.use_ability = list(dict.fromkeys(lead.use_ability))
        actions = [lead]
        for sub in subs:
            if sub not in winners and sub.freeform:
                rogue = await self._interpret(g, sub)
                rogue.rogue = True
                actions.append(rogue)
        return actions

    async def _resolve_current(self, timeout: bool) -> None:
        async with self.lock:
            s = self.state
            if s.phase not in (Phase.ADVENTURE, Phase.FINAL_CHALLENGE) or not s.pending_decisions or s.world_state.get("resolving"):
                return
            s.world_state["resolving"] = True
            self._cancel_timer()
            try:
                await self._resolve_locked(timeout)
            finally:
                s.world_state["resolving"] = False
            await self._commit()
        await self.sync()

    async def _resolve_locked(self, timeout: bool) -> None:
        s = self.state
        groups = list(s.pending_decisions.values())
        for g in groups:
            self._auto_submit(g)
        actions: list[PlannedAction] = []
        for g in groups:
            actions.extend(await self._plan_group(g))
        is_final = s.phase == Phase.FINAL_CHALLENGE
        rnd = s.current_round()
        escalation = rnd.escalation if rnd else 1
        resolution = resolve_round(s, actions, escalation + (1 if is_final else 0))
        outcomes = resolution.outcomes

        # Narrate per audience — each call sees only what that audience may know.
        narr: dict[str, str] = {}
        for g in groups:
            g_out = [o for o in outcomes if o.planned.group_id == g.group_id]
            reqs = [self._narration_request(o) for o in g_out]
            texts = await self.llm.consequence_resolver.narrate(
                ", ".join(s.players[p].display for p in g.player_ids), authorized_context(s, g.player_ids), reqs)
            for o, t in zip(g_out, texts, strict=False):
                narr[o.planned.key] = t

        # Chronicle + story entries + private information
        key_to_event: dict[str, str] = {}
        for o in outcomes:
            a = o.planned
            g = s.pending_decisions[a.group_id]
            if a.rogue:
                vis = Visibility.PRIVATE
            elif g.kind == DecisionKind.FINAL or len(g.player_ids) == len(s.active_players()) > 1:
                vis = Visibility.PUBLIC
            elif g.kind == DecisionKind.GROUP:
                vis = Visibility.GROUP
            else:
                vis = Visibility.PRIVATE
            audience = list(a.player_ids) if vis == Visibility.PRIVATE else list(g.player_ids)
            text = narr.get(a.key, "")
            importance = 2 + (1 if o.result.tier in (OutcomeTier.CATASTROPHIC_SUCCESS, OutcomeTier.UNEXPECTED_SUCCESS, OutcomeTier.FAILURE) else 0) \
                + (1 if o.interaction_partners else 0) + (1 if set(a.tags) & {"betray", "destroy"} or o.new_info or a.rogue else 0)
            etype = "final_action" if is_final else ("group_action" if len(a.player_ids) > 1 else "action")
            cev = chronicle.record(
                s, etype, player_ids=list(a.player_ids), npc_ids=[a.target_npc_id] if a.target_npc_id else [],
                location=s.locations[a.location_id].name if a.location_id in s.locations else None,
                visibility=vis, public_information=self._public_line(o), narrative_summary=text,
                private_information={pid: " ".join(lines) for pid, lines in o.private_notes.items()},
                group_information={g.group_id: g.shared_context} if g.kind == DecisionKind.GROUP else {},
                decisions=[{"action": a.description, "tier": o.result.tier.value, "roll": o.result.roll, "target": o.result.target,
                            "freeform": a.freeform, "rogue": a.rogue, "tags": a.tags}],
                consequences=o.consequences + [f"[link] {t}" for t in o.result.interactions],
                resource_changes=o.result.resource_changes, importance=min(5, importance), tags=list(a.tags),
                related_events=list(o.related))
            o.result.event_id = cev.event_id
            key_to_event[a.key] = cev.event_id
            for scar in s.world_state.get("scars", []):
                if scar.get("action_key") == a.key and not scar.get("event_id"):
                    scar["event_id"] = cev.event_id
            tier_label = o.result.tier.value.replace("_", " ").upper()
            entry_title = f"{tier_label} — {', '.join(s.players[p].display for p in a.player_ids)}" + (" (went rogue)" if a.rogue else "")
            mech = f" [rolled {o.result.roll} vs {o.result.target}" + (
                ", " + ", ".join(f"{k} {v:+d}" for k, v in o.result.modifiers.items()) if o.result.modifiers else "") + "]"
            self._system(text + mech, entry_title, vis, audience, kind="resolution")
            for pid, lines in o.private_notes.items():
                info = InfoItem(visibility=Visibility.PRIVATE, audience=[pid], kind="discovery", title="Only You Know",
                                text=" ".join(lines), round=s.turn_number, source_event_id=cev.event_id)
                s.hidden_information[info.id] = info
                await self.emit(E.PRIVATE_INFORMATION, {"title": info.title}, audience=[pid])
            for info in o.new_info:
                info.source_event_id = cev.event_id
                s.hidden_information[info.id] = info
                chronicle.record(s, "discovery", player_ids=info.audience, visibility=info.visibility, importance=4,
                                 narrative_summary=f"{', '.join(s.players[p].display for p in info.audience)} discovered a hidden truth: {info.text}",
                                 related_events=[cev.event_id])
                await self.emit(E.PRIVATE_INFORMATION, {"title": info.title}, audience=info.audience)
            if "betray" in a.tags:
                who = procgen.join_names([s.players[p].display for p in a.player_ids])
                chronicle.record(s, "betrayal", player_ids=list(a.player_ids), visibility=Visibility.ENDGAME_REVEAL, importance=5,
                                 narrative_summary=f"{who} had quietly decided the team was more of a suggestion. Their exact words: \"{a.description}\".",
                                 related_events=[cev.event_id])
            for pid in a.player_ids:
                s.memory.character_memories.setdefault(pid, []).append(f"Scene {s.turn_number}: I tried '{a.description[:60]}' — {o.result.tier.value}.")
            if importance >= 4:
                s.memory.major_decisions.append(f"Scene {s.turn_number}: {', '.join(s.players[p].display for p in a.player_ids)} — {a.description[:80]} ({o.result.tier.value})")
            if a.target_npc_id and a.target_npc_id in s.npcs:
                npc = s.npcs[a.target_npc_id]
                await self.emit(E.NPC_UPDATED, {"npc_id": npc.id, "disposition": npc.disposition}, audience=npc.known_by)

        # link interacting events both ways now that every event has an id
        by_id = {e.event_id: e for e in s.adventure_chronicle}
        for src, dst, _ in resolution.interactions:
            se, de = key_to_event.get(src), key_to_event.get(dst)
            if se and de:
                if se not in by_id[de].related_events:
                    by_id[de].related_events.append(se)

        if is_final:
            await self._conclude_final(outcomes)
            return

        # Public aftermath: everyone sees the effects, not necessarily the causes.
        public_lines = [self._public_line(o) for o in outcomes]
        upkeep = round_upkeep(s)
        res_line = ", ".join(f"{t.emoji} {t.label} {t.value}/{t.max}" for t in s.shared_resources.values())
        aftermath = " ".join(public_lines) + f" Meanwhile: {'; '.join(upkeep)}."
        self._system(aftermath + f"\n\nShared resources: {res_line}. Objective progress: {s.objective_progress}/{s.objective.progress_target if s.objective else '?'}",
                     f"Scene {s.turn_number} — Aftermath", kind="resolution")
        s.memory.short_term = (s.memory.short_term + [aftermath])[-10:]
        for g in groups:
            g.resolved = True
            g.resolution = [o.result for o in outcomes if o.planned.group_id == g.group_id]
            s.decision_archive.append(g)
        s.pending_decisions = {}
        if rnd:
            rnd.resolved = True
        if s.turn_number % 2 == 0:
            s.memory.medium_summary, threads = await self.llm.story_summarizer.summarize(s.memory.medium_summary, s.memory.short_term)
            if threads:
                s.memory.unresolved_threads = threads
        changes: dict[str, int] = {}
        for o in outcomes:
            for k, v in o.result.resource_changes.items():
                changes[k] = changes.get(k, 0) + v
        await self.emit(E.DECISION_RESOLVED, {"round": s.turn_number})
        await self.emit(E.RESOURCE_CHANGED, {"changes": changes, "resources": {k: v.value for k, v in s.shared_resources.items()}})
        await self.emit(E.SCENE_RESOLVED, {"round": s.turn_number})
        # A short pause to read the fallout, then the Director moves on.
        s.timers.phase_deadline = now() + self.settings.resolution_pause_seconds
        if s.settings.auto_advance:
            self._schedule(self.settings.resolution_pause_seconds, "resolution_pause")
        # otherwise the host continues with close_phase

    def _narration_request(self, o: ActionOutcome) -> NarrationRequest:
        s = self.state
        a = o.planned
        actors = [s.players[p] for p in a.player_ids]
        names = [x.display for x in actors]
        fallback = procgen.narrate_action(s.random_seed, a.key, names, a.description, o.result.tier.value,
                                          a.tags, actors[0].character.weakness, actors[0].character.humorous_trait,
                                          o.result.interactions, o.consequences)
        return NarrationRequest(actor_names=names, action=a.description, tier=o.result.tier.value, tags=a.tags,
                                consequences=o.consequences, interactions=o.result.interactions,
                                weakness=actors[0].character.weakness, trait=actors[0].character.humorous_trait, fallback=fallback)

    def _public_line(self, o: ActionOutcome) -> str:
        s = self.state
        names = procgen.join_names([s.players[p].display for p in o.planned.player_ids])
        was = "were" if len(o.planned.player_ids) > 1 else "was"
        loud = set(o.planned.tags) & {"noise", "destroy", "fight", "perform"} or o.result.tier == OutcomeTier.CATASTROPHIC_SUCCESS
        if o.planned.final:
            return f"{names}: {o.planned.description} — {o.result.tier.value.replace('_', ' ')}."
        if loud:
            what = "a tremendous crash" if "destroy" in o.planned.tags else ("the unmistakable sounds of a scuffle" if "fight" in o.planned.tags else "an extremely loud commotion")
            return f"Everyone hears {what} from wherever {names} went."
        if o.result.tier == OutcomeTier.FAILURE:
            return f"{names} {'come' if was == 'were' else 'comes'} back looking worse for wear, and won't say why."
        return f"Nobody else saw what {names} {was} up to."

    # ------------------------------------------------------------ finale

    async def _conclude_final(self, outcomes: list[ActionOutcome]) -> None:
        s = self.state
        score, target, breakdown = final_score(s, outcomes)
        n = max(1, len(s.active_players()))
        catastrophic = any(o.result.tier == OutcomeTier.CATASTROPHIC_SUCCESS for o in outcomes)
        unexpected = any(o.result.tier == OutcomeTier.UNEXPECTED_SUCCESS for o in outcomes)
        depleted = sum(1 for t in s.shared_resources.values() if t.value <= 1)
        down = any(p.status == PlayerStatus.INCAPACITATED for p in s.active_players())
        if score >= target:
            if catastrophic or sum(p.stats.catastrophes for p in s.active_players()) >= 3:
                kind = GameOutcomeKind.SUCCESS_NEW_PROBLEM
            elif unexpected or breakdown["contributions"] < n:
                kind = GameOutcomeKind.ACCIDENTAL_SUCCESS
            elif down or depleted >= 2:
                kind = GameOutcomeKind.COSTLY_SUCCESS
            else:
                kind = GameOutcomeKind.FULL_SUCCESS
        elif score >= math.ceil(target * 0.5):
            kind = GameOutcomeKind.PARTIAL_SUCCESS
        else:
            kind = GameOutcomeKind.FAILURE
        for g in s.pending_decisions.values():
            g.resolved = True
            g.resolution = [o.result for o in outcomes if o.planned.group_id == g.group_id]
            s.decision_archive.append(g)
        s.pending_decisions = {}
        await self._finish_game(kind=kind, score=score, target=target, breakdown=breakdown, final_outcomes=outcomes)

    def _personal_outcome(self, pid: str, kind: GameOutcomeKind, final_outcomes: list[ActionOutcome]) -> str:
        s = self.state
        p = s.players[pid]
        mine = next((o for o in final_outcomes if pid in o.planned.player_ids), None)
        parts = []
        if mine:
            t = mine.result.tier
            what = (f'went off-script ("{mine.planned.description}")' if mine.planned.freeform
                    else "chose to " + procgen.lower_first(procgen.third_person(mine.planned.description, p.display)))
            parts.append(f"In the finale, {p.display} {what} — "
                         + {"catastrophic_success": "it worked, catastrophically",
                            "unexpected_success": "and somehow became the hero of the moment",
                            "success": "and it worked", "partial_success": "and it half-worked",
                            "complication": "and complicated everything", "failure": "and it went terribly"}[t.value] + ".")
        st = p.stats
        if st.betrayals:
            parts.append(f"Their {st.betrayals} secret betrayal(s) were, in the end, discovered by everyone.")
        if st.secrets_found:
            parts.append("They uncovered a hidden truth that changed the odds.")
        if st.helps:
            parts.append(f"They helped others {st.helps} time(s), which nobody thanked them for at the time.")
        if p.status == PlayerStatus.INCAPACITATED:
            parts.append("They spent the ending unconscious, which in hindsight was the most relaxing part of their day.")
        if not parts:
            parts.append(f"{p.display} was there for all of it, and responsible for at least some of it.")
        return " ".join(parts)

    async def _finish_game(self, kind: GameOutcomeKind | None = None, score: int = 0, target: int = 0,
                           breakdown: dict[str, int] | None = None, final_outcomes: list[ActionOutcome] | None = None,
                           doom_reason: str | None = None) -> None:
        s = self.state
        self._cancel_timer()
        final_outcomes = final_outcomes or []
        kind = kind or GameOutcomeKind.FAILURE
        obj = s.objective
        headlines = {
            GameOutcomeKind.FULL_SUCCESS: "Total Victory (Somehow)",
            GameOutcomeKind.PARTIAL_SUCCESS: "A Partial Success, Technically",
            GameOutcomeKind.FAILURE: "A Glorious Failure",
            GameOutcomeKind.COSTLY_SUCCESS: "Victory, at a Terrible Cost",
            GameOutcomeKind.ACCIDENTAL_SUCCESS: "An Accidental Triumph",
            GameOutcomeKind.SUCCESS_NEW_PROBLEM: "They Won — and Created a Brand-New Problem",
        }
        summary = doom_reason or (
            f"Final score {score} against a target of {target} (" + ", ".join(f"{k} {v:+d}" for k, v in (breakdown or {}).items() if v) + "). "
            + (f"The objective — {obj.title} — " if obj else "The objective ")
            + {"failure": "was not achieved.", "partial_success": "was half-achieved, which counts for something.",
               }.get(kind.value, "was achieved.")
        )
        if kind == GameOutcomeKind.SUCCESS_NEW_PROBLEM and obj:
            summary += f" Unfortunately, {obj.antagonist} is now in charge of something much bigger."
        s.outcome = Outcome(kind=kind, headline=headlines[kind], group_summary=summary, final_roll=score, final_target=target)
        s.outcome.personal = {p.id: self._personal_outcome(p.id, kind, final_outcomes) for p in s.active_players()}
        s.outcome.achievements = achievements.compute(s)
        chronicle.record(s, "outcome", importance=5, player_ids=[p.id for p in s.active_players()],
                         narrative_summary=f"{headlines[kind]}. {summary}")
        self._system(summary, headlines[kind], kind="final")
        s.phase = Phase.STORY_GENERATION
        s.timers.phase_deadline = None
        await self._commit()
        won = kind != GameOutcomeKind.FAILURE
        await self.emit(E.GAME_WON if won else E.GAME_LOST, {"kind": kind, "headline": headlines[kind]})
        await self.emit(E.FINAL_STORY_GENERATION_STARTED, {})
        self._spawn(self._generate_final_story())

    async def _generate_final_story(self) -> None:
        s = self.state
        await self.sync()
        try:
            story = await self.build_final_story()
        except Exception:  # noqa: BLE001
            log.exception("final story generation failed; using procedural story")
            sc = chronicle.build_story_chronicle(s)
            story = chronicle.procedural_story(s, sc, self.story_words())
        async with self.lock:
            s.final_story = story
            s.share_id = s.share_id or secrets.token_urlsafe(9)
            s.phase = Phase.ENDED
            s.audio_status = {"state": "pending", "chapters": len(story.chapters), "ready": []}
            await self._commit()
            try:
                await asyncio.shield(self.store.archive(s))
            except Exception:  # noqa: BLE001
                log.exception("failed to archive adventure %s", s.code)
        await self.emit(E.FINAL_STORY_GENERATED, {"title": story.title, "chapters": len(story.chapters), "words": story.word_count})
        await self.emit(E.GAME_ENDED, {"share_id": s.share_id})
        await self.sync()
        if self.audio_hook:
            self._spawn(self.audio_hook(s))

    def story_words(self) -> tuple[int, int]:
        return {
            AdventureLength.SHORT: self.settings.story_words_short,
            AdventureLength.MEDIUM: self.settings.story_words_medium,
            AdventureLength.LONG: self.settings.story_words_long,
        }[self.state.settings.adventure_length]

    async def build_final_story(self) -> FinalStory:
        """Chronicle → (LLM story → validate → repair)* → procedural fallback → validated story."""
        s = self.state
        sc = chronicle.build_story_chronicle(s)
        words = self.story_words()
        if self.llm.provider is not None:
            text = chronicle.chronicle_text(sc)
            names = [c["name"] for c in sc.characters]
            n_chapters = 5 + min(3, s.total_rounds // 3)
            draft = await self.llm.final_story_generator.generate(text, names, words, n_chapters)
            attempts = 0
            while draft is not None and attempts < 3:
                attempts += 1
                story = FinalStory(title=f"{GAME_TITLE}: {s.theme}", chapters=chapters_from_llm(draft),
                                   epilogues={e.title: e.text for e in draft.epilogues}, generated_by=self.llm.name)
                story.word_count = sum(c.word_count for c in story.chapters)
                report = chronicle.validate_story(s, sc, story, words)
                report.attempts = attempts
                story.validation = report
                if report.passed:
                    return story
                log.info("final story failed validation: %s", report.issues)
                if attempts < 3:
                    draft = await self.llm.final_story_editor.repair(draft, report.issues, text)
        story = chronicle.procedural_story(s, sc, words)
        story.validation = chronicle.validate_story(s, sc, story, words)
        return story

    # ------------------------------------------------------------ chat & prefs

    async def chat(self, p: Player, msg: ev.SendChat) -> None:
        s = self.state
        text = clean_text(msg.text, 400)
        if not text:
            return
        mode = s.comm_mode if s.phase in (Phase.ADVENTURE, Phase.FINAL_CHALLENGE) else CommMode.OPEN
        channel = msg.channel
        audience: list[str] = []
        if channel == "global":
            if mode != CommMode.OPEN:
                raise GameError("Global chat is closed for this scene. The Director wants you to sweat.")
        elif channel.startswith("group:"):
            g = s.pending_decisions.get(channel.split(":", 1)[1])
            if not g or p.id not in g.player_ids:
                raise GameError("You're not in that group.")
            if mode in (CommMode.DISABLED, CommMode.PRIVATE_ONLY):
                raise GameError("Group chat is closed for this scene.")
            audience = list(g.player_ids)
        elif channel.startswith("dm:") or msg.to:
            if mode in (CommMode.DISABLED, CommMode.RESTRICTED):
                raise GameError("Private messages are closed for this scene.")
            recipients = msg.to or [x for x in channel.split(":")[1:] if x != p.id]
            recipients = [r for r in recipients if r in s.players and r != p.id][:11]
            if not recipients:
                raise GameError("Choose who to message.")
            audience = sorted(set(recipients + [p.id]))
            channel = "dm:" + ":".join(audience)
        else:
            raise GameError("Unknown channel.")
        m = ChatMessage(channel=channel, sender_id=p.id, sender_name=p.display, text=text, audience=audience)
        s.chat.append(m)
        s.chat = s.chat[-500:]
        await self.emit(E.CHAT_MESSAGE, {"message": m.model_dump()}, audience=audience or None)

    async def set_preferences(self, p: Player, msg: ev.SetPreferences) -> None:
        if msg.audio_enabled is not None:
            p.preferences.audio_enabled = msg.audio_enabled
        if msg.narration_volume is not None:
            p.preferences.narration_volume = msg.narration_volume
        if msg.preferred_voice:
            p.preferences.preferred_voice = clean_text(msg.preferred_voice, 32)
        await self._commit()
        await self.sync(only=[p.id])

    # ------------------------------------------------------------ misc

    async def shutdown(self) -> None:
        self._cancel_timer()
        for t in list(self._background):
            t.cancel()


def summarize_tier(tier: OutcomeTier) -> bool:
    return tier in SUCCESSES


__all__ = ["GameError", "GameManager", "NullPublisher", "NullStore", "make_code", "label"]
