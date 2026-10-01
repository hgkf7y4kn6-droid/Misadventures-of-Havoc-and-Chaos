"""Real-time event names and the client → server message protocol."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class RealtimeEvent(StrEnum):
    PLAYER_JOINED = "PLAYER_JOINED"
    PLAYER_LEFT = "PLAYER_LEFT"
    PLAYER_READY = "PLAYER_READY"
    PLAYER_KICKED = "PLAYER_KICKED"
    SETTINGS_CHANGED = "SETTINGS_CHANGED"
    GAME_STARTED = "GAME_STARTED"
    THEME_SUBMITTED = "THEME_SUBMITTED"
    THEME_SUBMISSIONS_CLOSED = "THEME_SUBMISSIONS_CLOSED"
    THEME_VOTING_STARTED = "THEME_VOTING_STARTED"
    THEME_VOTE_CAST = "THEME_VOTE_CAST"
    THEME_SELECTED = "THEME_SELECTED"
    OBJECTIVE_REVEALED = "OBJECTIVE_REVEALED"
    CHARACTER_CREATION_STARTED = "CHARACTER_CREATION_STARTED"
    CHARACTER_UPDATED = "CHARACTER_UPDATED"
    SCENE_STARTED = "SCENE_STARTED"
    DECISION_STARTED = "DECISION_STARTED"
    DECISION_SUBMITTED = "DECISION_SUBMITTED"
    DECISION_RESOLVED = "DECISION_RESOLVED"
    GROUP_FORMED = "GROUP_FORMED"
    GROUP_DECISION_STARTED = "GROUP_DECISION_STARTED"
    RESOURCE_CHANGED = "RESOURCE_CHANGED"
    RANDOM_EVENT = "RANDOM_EVENT"
    NPC_UPDATED = "NPC_UPDATED"
    PRIVATE_INFORMATION = "PRIVATE_INFORMATION"
    SCENE_RESOLVED = "SCENE_RESOLVED"
    FINAL_CHALLENGE_STARTED = "FINAL_CHALLENGE_STARTED"
    GAME_WON = "GAME_WON"
    GAME_LOST = "GAME_LOST"
    FINAL_STORY_GENERATION_STARTED = "FINAL_STORY_GENERATION_STARTED"
    FINAL_STORY_GENERATED = "FINAL_STORY_GENERATED"
    FINAL_AUDIO_GENERATION_STARTED = "FINAL_AUDIO_GENERATION_STARTED"
    FINAL_AUDIO_CHAPTER_READY = "FINAL_AUDIO_CHAPTER_READY"
    FINAL_AUDIO_GENERATION_COMPLETE = "FINAL_AUDIO_GENERATION_COMPLETE"
    GAME_ENDED = "GAME_ENDED"
    GAME_RESTARTED = "GAME_RESTARTED"
    CHAT_MESSAGE = "CHAT_MESSAGE"
    ERROR = "ERROR"


# ---------------------------------------------------------------------------
# Client -> server actions (validated; anything else is rejected)
# ---------------------------------------------------------------------------

Text = Annotated[str, Field(max_length=500)]


class SetReady(BaseModel):
    action: Literal["set_ready"]
    ready: bool


class UpdateLobbyProfile(BaseModel):
    action: Literal["update_profile"]
    name: Annotated[str, Field(min_length=1, max_length=32)] | None = None
    avatar: Annotated[str, Field(max_length=8)] | None = None
    color: Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")] | None = None
    archetype: Annotated[str, Field(max_length=80)] | None = None


class UpdateSettings(BaseModel):
    action: Literal["update_settings"]
    settings: dict


class StartGame(BaseModel):
    action: Literal["start_game"]


class KickPlayer(BaseModel):
    action: Literal["kick_player"]
    player_id: str


class RestartGame(BaseModel):
    action: Literal["restart_game"]


class SubmitTheme(BaseModel):
    action: Literal["submit_theme"]
    text: Annotated[str, Field(min_length=3, max_length=200)]


class CloseThemes(BaseModel):
    action: Literal["close_phase"]


class CastVote(BaseModel):
    action: Literal["cast_vote"]
    option_id: str


class SaveCharacter(BaseModel):
    action: Literal["save_character"]
    name: Annotated[str, Field(min_length=1, max_length=40)]
    archetype: Annotated[str, Field(max_length=80)] = ""
    personality: Annotated[str, Field(max_length=200)] = ""
    special_ability: Annotated[str, Field(max_length=200)] = ""
    weakness: Annotated[str, Field(max_length=200)] = ""
    secret_motivation: Annotated[str, Field(max_length=200)] = ""
    starting_item: Annotated[str, Field(max_length=80)] = ""
    humorous_trait: Annotated[str, Field(max_length=200)] = ""
    avatar: Annotated[str, Field(max_length=8)] = "🎲"
    stats: dict[str, int] | None = None


class SuggestCharacter(BaseModel):
    action: Literal["suggest_character"]


class SubmitDecision(BaseModel):
    action: Literal["submit_decision"]
    group_id: str
    choice_id: str | None = None
    freeform: Annotated[str, Field(max_length=300)] | None = None
    push_luck: bool = False
    use_ability: bool = False
    item_id: str | None = None


class SendChat(BaseModel):
    action: Literal["chat"]
    channel: Annotated[str, Field(max_length=80)]
    text: Annotated[str, Field(min_length=1, max_length=400)]
    to: list[str] | None = None  # recipients for a new private channel


class SetPreferences(BaseModel):
    action: Literal["set_preferences"]
    audio_enabled: bool | None = None
    narration_volume: Annotated[float, Field(ge=0, le=1)] | None = None
    preferred_voice: Annotated[str, Field(max_length=32)] | None = None


class Ping(BaseModel):
    action: Literal["ping"]


ClientMessage = Annotated[
    SetReady
    | UpdateLobbyProfile
    | UpdateSettings
    | StartGame
    | KickPlayer
    | RestartGame
    | SubmitTheme
    | CloseThemes
    | CastVote
    | SaveCharacter
    | SuggestCharacter
    | SubmitDecision
    | SendChat
    | SetPreferences
    | Ping,
    Field(discriminator="action"),
]
