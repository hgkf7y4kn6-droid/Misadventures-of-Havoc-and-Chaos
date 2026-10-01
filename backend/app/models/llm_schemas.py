"""Structured-output schemas for every LLM service.

Every model response is parsed into one of these and then *sanitised* by the
engine (tags filtered to the known vocabulary, costs clamped, ids checked)
before anything touches game state.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .game import Risk

TAG_VOCABULARY = [
    "hide", "investigate", "lock", "warn", "distract", "fight", "flee", "steal", "negotiate", "build",
    "destroy", "help", "explore", "betray", "perform", "climb", "cross", "rest", "noise",
]


class LLMThemeJudgement(BaseModel):
    duplicate_pairs: list[list[int]] = Field(description="Pairs [i, j] of submission indices that describe the same adventure")


class LLMThemeTitles(BaseModel):
    titles: list[str] = Field(description="One punchy, funny title per cluster, in order")


class LLMHiddenVariable(BaseModel):
    fact: str
    hint: str
    trigger_keywords: list[str]


class LLMLocation(BaseModel):
    name: str
    description: str
    tags: list[str]


class LLMNPC(BaseModel):
    name: str
    emoji: str
    personality: str
    goal: str
    knowledge: list[str]
    secrets: list[str]


class LLMResourceLabel(BaseModel):
    key: str
    label: str


class LLMObjectiveBundle(BaseModel):
    title: str
    description: str
    tagline: str
    success_conditions: list[str]
    failure_conditions: list[str]
    world_rules: list[str]
    final_challenge: str
    antagonist: str
    macguffin: str
    resource_labels: list[LLMResourceLabel]
    locations: list[LLMLocation]
    npcs: list[LLMNPC]
    hidden_variables: list[LLMHiddenVariable]


class LLMCharacter(BaseModel):
    name: str
    archetype: str
    personality: str
    special_ability: str
    weakness: str
    secret_motivation: str
    starting_item: str
    humorous_trait: str


class LLMScene(BaseModel):
    title: str
    narrative: str


class LLMChoice(BaseModel):
    label: str
    description: str
    tags: list[str]
    risk: Risk
    stat: str
    cost_resource: str = Field(description="shared resource key to spend, or empty string")
    cost_amount: int
    advances_objective: bool


class LLMDecisionContext(BaseModel):
    group_id: str
    title: str
    context: str
    choices: list[LLMChoice]


class LLMDecisionBatch(BaseModel):
    decisions: list[LLMDecisionContext]


class LLMActionInterpretation(BaseModel):
    summary: str
    feasible: bool
    feasibility_note: str
    risk: Risk
    stat: str
    tags: list[str]
    cost_resource: str
    cost_amount: int
    target_player_name: str
    target_npc_name: str
    advances_objective: bool
    possible_consequences: list[str]


class LLMNarration(BaseModel):
    action_index: int
    narrative: str


class LLMNarrationBatch(BaseModel):
    public_summary: str
    narrations: list[LLMNarration]


class LLMSummary(BaseModel):
    summary: str
    unresolved_threads: list[str]


class LLMChapter(BaseModel):
    title: str
    text: str


class LLMFinalStory(BaseModel):
    title: str
    chapters: list[LLMChapter]
    epilogues: list[LLMChapter] = Field(description="One per player: title = player name, text = their epilogue")


class LLMTwist(BaseModel):
    twist: str
