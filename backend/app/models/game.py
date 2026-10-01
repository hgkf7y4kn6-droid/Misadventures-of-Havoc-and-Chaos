"""Authoritative domain model.

Everything the game knows lives in ``GameState``. The LLM never owns any of
these fields: it is asked to *describe* them, and its output is validated and
then applied by the engine.
"""

from __future__ import annotations

import time
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Phase(StrEnum):
    LOBBY = "lobby"
    THEME_SUBMISSION = "theme_submission"
    THEME_VOTING = "theme_voting"
    OBJECTIVE_REVEAL = "objective_reveal"
    CHARACTER_CREATION = "character_creation"
    ADVENTURE = "adventure"
    FINAL_CHALLENGE = "final_challenge"
    STORY_GENERATION = "story_generation"
    ENDED = "ended"


class Visibility(StrEnum):
    PUBLIC = "public"  # everyone, always
    PRIVATE = "private"  # one player during play; revealed in the final story
    GROUP = "group"  # a subset during play; revealed in the final story
    ENDGAME_REVEAL = "endgame_reveal"  # only the audience (often nobody) until the end
    NEVER_REVEAL = "never_reveal"  # audience only, excluded from the final story & share links


class CommMode(StrEnum):
    OPEN = "open"  # global + private + group chat
    RESTRICTED = "restricted"  # group chat only
    PRIVATE_ONLY = "private_only"  # direct messages only
    DISABLED = "disabled"  # silence


class AdventureLength(StrEnum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class OutcomeTier(StrEnum):
    CATASTROPHIC_SUCCESS = "catastrophic_success"
    UNEXPECTED_SUCCESS = "unexpected_success"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    COMPLICATION = "complication"
    FAILURE = "failure"


class GameOutcomeKind(StrEnum):
    FULL_SUCCESS = "full_success"
    PARTIAL_SUCCESS = "partial_success"
    FAILURE = "failure"
    COSTLY_SUCCESS = "costly_success"
    ACCIDENTAL_SUCCESS = "accidental_success"
    SUCCESS_NEW_PROBLEM = "success_new_problem"


class PlayerStatus(StrEnum):
    ACTIVE = "active"
    INCAPACITATED = "incapacitated"
    LEFT = "left"


class DecisionKind(StrEnum):
    INDIVIDUAL = "individual"
    GROUP = "group"
    FINAL = "final"


class Risk(StrEnum):
    SAFE = "safe"
    RISKY = "risky"
    WILD = "wild"


SHARED_RESOURCE_KEYS = ("food", "money", "time", "supplies", "morale", "reputation", "vehicle")
STATS = ("brawn", "brains", "charm", "sneak", "weird")


# ---------------------------------------------------------------------------
# Settings & players
# ---------------------------------------------------------------------------


class GameSettings(BaseModel):
    min_players: int = 2
    max_players: int = 8
    adventure_length: AdventureLength = AdventureLength.SHORT
    theme_submission_seconds: int = 90
    theme_voting_seconds: int = 60
    character_creation_seconds: int = 180
    decision_seconds: int = 120
    default_comm_mode: CommMode = CommMode.OPEN
    allow_public_share: bool = True
    narrator_voice: str = "storyteller"
    auto_advance: bool = True  # automatically move on when timers expire


class AudioPreferences(BaseModel):
    audio_enabled: bool = True
    narration_volume: float = Field(default=0.9, ge=0.0, le=1.0)
    preferred_voice: str = "storyteller"


class Character(BaseModel):
    name: str = ""
    archetype: str = ""
    personality: str = ""
    special_ability: str = ""
    weakness: str = ""
    secret_motivation: str = ""
    starting_item: str = ""
    humorous_trait: str = ""
    avatar: str = "🎲"
    color: str = "#f97316"
    stats: dict[str, int] = Field(default_factory=lambda: {s: 1 for s in STATS})
    complete: bool = False


class Item(BaseModel):
    id: str = Field(default_factory=lambda: new_id("itm_"))
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    hidden: bool = False  # hidden items are not shown to other players
    origin_event_id: str | None = None


class PlayerResources(BaseModel):
    health: int = 10
    luck: int = 3
    trust: int = 7
    ability_charges: int = 2


class PlayerStats(BaseModel):
    """Counters that feed achievements and the final story. Only the engine writes these."""

    actions: int = 0
    freeform_actions: int = 0
    successes: int = 0
    failures: int = 0
    complications_caused: int = 0
    catastrophes: int = 0
    helps: int = 0
    betrayals: int = 0
    social_successes: int = 0
    secrets_found: int = 0
    items_taken: int = 0
    shared_resources_spent: int = 0
    luck_spent: int = 0
    highest_risk_failure_roll: int | None = None
    worst_decision_event_id: str | None = None
    final_contribution: int = 0
    pre_final_contribution: int = 0
    objective_progress: int = 0
    times_soloed: int = 0
    harmful_events_triggered: int = 0
    npc_interactions: dict[str, int] = Field(default_factory=dict)


class Player(BaseModel):
    id: str = Field(default_factory=lambda: new_id("p_"))
    token: str = ""  # secret, never projected
    name: str
    is_host: bool = False
    ready: bool = False
    connected: bool = False
    status: PlayerStatus = PlayerStatus.ACTIVE
    joined_at: float = Field(default_factory=now)
    character: Character = Field(default_factory=Character)
    resources: PlayerResources = Field(default_factory=PlayerResources)
    inventory: list[Item] = Field(default_factory=list)
    location_id: str | None = None
    stats: PlayerStats = Field(default_factory=PlayerStats)
    preferences: AudioPreferences = Field(default_factory=AudioPreferences)
    path_tags: list[str] = Field(default_factory=list)  # approach tags from the last round

    @property
    def display(self) -> str:
        return self.character.name or self.name


# ---------------------------------------------------------------------------
# Themes & objective
# ---------------------------------------------------------------------------


class ThemeSubmission(BaseModel):
    player_id: str
    text: str
    submitted_at: float = Field(default_factory=now)


class ThemeOption(BaseModel):
    id: str = Field(default_factory=lambda: new_id("th_"))
    title: str
    originals: list[str] = Field(default_factory=list)
    player_ids: list[str] = Field(default_factory=list)


class ResourceTrack(BaseModel):
    key: str
    label: str
    emoji: str
    value: int
    max: int
    description: str = ""


class HiddenVariable(BaseModel):
    id: str = Field(default_factory=lambda: new_id("hv_"))
    fact: str
    hint: str  # what a player learns on discovery
    trigger_keywords: list[str]
    final_bonus: int = 3
    discovered_by: list[str] = Field(default_factory=list)
    visibility: Visibility = Visibility.ENDGAME_REVEAL


class Location(BaseModel):
    id: str = Field(default_factory=lambda: new_id("loc_"))
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    state: list[str] = Field(default_factory=list)  # e.g. "on fire", "bridge destroyed"


class Objective(BaseModel):
    title: str
    description: str
    tagline: str = ""
    success_conditions: list[str]
    failure_conditions: list[str]
    world_rules: list[str]
    progress_target: int
    final_challenge: str
    antagonist: str = ""
    macguffin: str = ""
    approximate_rounds: int = 5


# ---------------------------------------------------------------------------
# Information, NPCs, decisions
# ---------------------------------------------------------------------------


class InfoItem(BaseModel):
    id: str = Field(default_factory=lambda: new_id("inf_"))
    visibility: Visibility
    audience: list[str] = Field(default_factory=list)
    kind: str = "discovery"
    title: str
    text: str
    round: int = 0
    source_event_id: str | None = None
    created_at: float = Field(default_factory=now)


class NPC(BaseModel):
    id: str = Field(default_factory=lambda: new_id("npc_"))
    name: str
    emoji: str = "🧑"
    personality: str
    goal: str
    relationships: dict[str, str] = Field(default_factory=dict)  # player_id/npc_id -> note
    knowledge: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    location_id: str | None = None
    memory: list[str] = Field(default_factory=list)
    disposition: int = 0  # -5 hostile .. +5 devoted
    known_by: list[str] = Field(default_factory=list)  # player ids who have met them
    introduced_round: int = 0


class Choice(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ch_"))
    label: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    risk: Risk = Risk.RISKY
    stat: str = "brains"
    cost: dict[str, int] = Field(default_factory=dict)  # shared resource key -> amount
    target_npc_id: str | None = None
    advances_objective: bool = False


class ActionInterpretation(BaseModel):
    """Structured reading of a free-text action (LLM or deterministic)."""

    summary: str
    feasible: bool = True
    feasibility_note: str = ""
    risk: Risk = Risk.RISKY
    stat: str = "weird"
    tags: list[str] = Field(default_factory=list)
    cost: dict[str, int] = Field(default_factory=dict)
    target_player_id: str | None = None
    target_npc_id: str | None = None
    advances_objective: bool = False
    possible_consequences: list[str] = Field(default_factory=list)


class Submission(BaseModel):
    player_id: str
    choice_id: str | None = None
    freeform: str | None = None
    push_luck: bool = False
    use_ability: bool = False
    item_id: str | None = None
    submitted_at: float = Field(default_factory=now)
    interpretation: ActionInterpretation | None = None
    auto: bool = False  # submitted by the engine on timeout


class ResolvedAction(BaseModel):
    player_ids: list[str]
    description: str
    tier: OutcomeTier
    roll: int
    target: int
    modifiers: dict[str, int] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    resource_changes: dict[str, int] = Field(default_factory=dict)
    player_changes: dict[str, dict[str, int]] = Field(default_factory=dict)
    progress: int = 0
    interactions: list[str] = Field(default_factory=list)
    event_id: str | None = None
    rogue: bool = False


class DecisionGroup(BaseModel):
    group_id: str = Field(default_factory=lambda: new_id("grp_"))
    round: int
    kind: DecisionKind
    player_ids: list[str]
    location_id: str | None = None
    title: str = ""
    shared_context: str
    visible_information: list[str] = Field(default_factory=list)  # info ids
    available_choices: list[Choice]
    allow_freeform: bool = True
    deadline: float | None = None
    comm_mode: CommMode = CommMode.OPEN
    submissions: dict[str, Submission] = Field(default_factory=dict)
    resolution: list[ResolvedAction] = Field(default_factory=list)
    resolved: bool = False


# ---------------------------------------------------------------------------
# Story, chronicle, memory
# ---------------------------------------------------------------------------


class StoryEntry(BaseModel):
    id: str = Field(default_factory=lambda: new_id("st_"))
    round: int
    kind: str  # scene | resolution | event | system | private | group | final
    title: str = ""
    text: str
    visibility: Visibility = Visibility.PUBLIC
    audience: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=now)


class AdventureEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: new_id("ev_"))
    timestamp: float = Field(default_factory=now)
    sequence_number: int = 0
    round: int = 0
    event_type: str
    player_ids: list[str] = Field(default_factory=list)
    npc_ids: list[str] = Field(default_factory=list)
    location: str | None = None
    visibility: Visibility = Visibility.PUBLIC
    public_information: str = ""
    private_information: dict[str, str] = Field(default_factory=dict)
    group_information: dict[str, str] = Field(default_factory=dict)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    consequences: list[str] = Field(default_factory=list)
    resource_changes: dict[str, int] = Field(default_factory=dict)
    narrative_summary: str = ""
    importance: int = 1  # 1 (trivia) .. 5 (legendary)
    related_events: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class RunningJoke(BaseModel):
    subject: str
    description: str
    mentions: int = 1
    first_round: int = 0
    last_round: int = 0
    event_ids: list[str] = Field(default_factory=list)


class StoryMemory(BaseModel):
    short_term: list[str] = Field(default_factory=list)
    medium_summary: str = ""
    long_term_facts: list[str] = Field(default_factory=list)
    character_memories: dict[str, list[str]] = Field(default_factory=dict)
    world_facts: list[str] = Field(default_factory=list)
    unresolved_threads: list[str] = Field(default_factory=list)
    running_jokes: list[RunningJoke] = Field(default_factory=list)
    major_decisions: list[str] = Field(default_factory=list)
    important_consequences: list[str] = Field(default_factory=list)


class DelayedConsequence(BaseModel):
    id: str = Field(default_factory=lambda: new_id("dc_"))
    trigger_round: int
    player_ids: list[str]
    description: str
    resource_changes: dict[str, int] = Field(default_factory=dict)
    health_change: int = 0
    source_event_id: str | None = None
    fired: bool = False


class ActiveEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rev_"))
    round: int
    title: str
    text: str
    category: str
    player_ids: list[str] = Field(default_factory=list)
    visibility: Visibility = Visibility.PUBLIC
    resource_changes: dict[str, int] = Field(default_factory=dict)
    subject: str = ""


class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: new_id("msg_"))
    channel: str  # global | group:<gid> | dm:<a>:<b> | system
    sender_id: str | None
    sender_name: str
    text: str
    audience: list[str] = Field(default_factory=list)  # empty == everyone
    created_at: float = Field(default_factory=now)


class Round(BaseModel):
    number: int
    beat: str
    title: str
    scene_text: str = ""
    comm_mode: CommMode = CommMode.OPEN
    group_ids: list[str] = Field(default_factory=list)
    random_event_id: str | None = None
    resolved: bool = False
    escalation: int = 1


class Achievement(BaseModel):
    key: str
    title: str
    description: str
    emoji: str = "🏆"
    evidence_event_ids: list[str] = Field(default_factory=list)


class Outcome(BaseModel):
    kind: GameOutcomeKind
    headline: str
    group_summary: str
    final_roll: int = 0
    final_target: int = 0
    personal: dict[str, str] = Field(default_factory=dict)
    achievements: dict[str, list[Achievement]] = Field(default_factory=dict)


class StoryChapter(BaseModel):
    index: int
    title: str
    text: str
    word_count: int = 0


class ValidationReport(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list)
    attempts: int = 1


class FinalStory(BaseModel):
    title: str
    chapters: list[StoryChapter]
    epilogues: dict[str, str] = Field(default_factory=dict)
    word_count: int = 0
    generated_by: str = "procedural"
    validation: ValidationReport | None = None


class Timers(BaseModel):
    phase_deadline: float | None = None
    phase_started_at: float = Field(default_factory=now)


class GameState(BaseModel):
    game_id: str = Field(default_factory=lambda: new_id("g_"))
    code: str
    created_at: float = Field(default_factory=now)
    version: int = 0
    phase: Phase = Phase.LOBBY
    settings: GameSettings = Field(default_factory=GameSettings)
    host_id: str | None = None
    players: dict[str, Player] = Field(default_factory=dict)
    kicked_tokens: list[str] = Field(default_factory=list)

    theme_submissions: dict[str, ThemeSubmission] = Field(default_factory=dict)
    theme_options: list[ThemeOption] = Field(default_factory=list)
    theme_votes: dict[str, str] = Field(default_factory=dict)  # player -> option id
    theme: str | None = None
    theme_tally: dict[str, int] = Field(default_factory=dict)

    objective: Objective | None = None
    success_conditions: list[str] = Field(default_factory=list)
    failure_conditions: list[str] = Field(default_factory=list)
    shared_resources: dict[str, ResourceTrack] = Field(default_factory=dict)
    objective_progress: int = 0
    hidden_variables: list[HiddenVariable] = Field(default_factory=list)
    locations: dict[str, Location] = Field(default_factory=dict)
    world_state: dict[str, Any] = Field(default_factory=dict)

    current_scene: str = ""
    rounds: list[Round] = Field(default_factory=list)
    story_history: list[StoryEntry] = Field(default_factory=list)
    adventure_chronicle: list[AdventureEvent] = Field(default_factory=list)
    active_events: list[ActiveEvent] = Field(default_factory=list)
    pending_decisions: dict[str, DecisionGroup] = Field(default_factory=dict)
    decision_archive: list[DecisionGroup] = Field(default_factory=list)
    hidden_information: dict[str, InfoItem] = Field(default_factory=dict)
    npcs: dict[str, NPC] = Field(default_factory=dict)
    delayed_consequences: list[DelayedConsequence] = Field(default_factory=list)
    memory: StoryMemory = Field(default_factory=StoryMemory)
    chat: list[ChatMessage] = Field(default_factory=list)

    random_seed: int = 0
    turn_number: int = 0
    total_rounds: int = 5
    timers: Timers = Field(default_factory=Timers)
    comm_mode: CommMode = CommMode.OPEN

    outcome: Outcome | None = None
    final_story: FinalStory | None = None
    share_id: str | None = None
    audio_status: dict[str, Any] = Field(default_factory=dict)

    # -- helpers -----------------------------------------------------------

    def active_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.status != PlayerStatus.LEFT]

    def able_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.status == PlayerStatus.ACTIVE]

    def player_by_token(self, token: str) -> Player | None:
        for p in self.players.values():
            if p.token and p.token == token:
                return p
        return None

    def res(self, key: str) -> int:
        track = self.shared_resources.get(key)
        return track.value if track else 0

    def current_round(self) -> Round | None:
        return self.rounds[-1] if self.rounds else None
