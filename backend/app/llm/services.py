"""Specialised LLM services.

Each service:
  * receives only the context it is *authorised* to see (built by the engine),
  * asks the provider for a structured object,
  * validates it with Pydantic and sanitises it,
  * falls back to the procedural generator on any failure.

No service ever mutates ``GameState``; the engine applies results.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import GAME_TITLE, Settings
from ..engine import procgen, rng, themes
from ..models.game import (
    NPC,
    ActionInterpretation,
    Character,
    Choice,
    HiddenVariable,
    Location,
    Objective,
    Risk,
    StoryChapter,
    ThemeOption,
    ThemeSubmission,
)
from ..models.llm_schemas import (
    TAG_VOCABULARY,
    LLMActionInterpretation,
    LLMCharacter,
    LLMDecisionBatch,
    LLMFinalStory,
    LLMNarrationBatch,
    LLMObjectiveBundle,
    LLMScene,
    LLMSummary,
    LLMThemeJudgement,
    LLMThemeTitles,
    LLMTwist,
)
from .base import LLMError, LLMProvider
from .safety import GUARD, clean_text, quote

log = logging.getLogger("havoc.llm")
M = TypeVar("M", bound=BaseModel)

STYLE = (
    f"You are part of the narrative engine for '{GAME_TITLE}', a multiplayer cooperative comedy adventure. "
    "Voice: fast, absurd, specific, visually descriptive, character-driven. Prefer concrete, surprising details "
    "over generic fantasy prose. Never use phrases like 'little did they know' or 'in a world where'. "
    "Keep it PG-13. The game engine is authoritative: never invent resource numbers, dice results, "
    "success or failure — describe the outcomes you are given. " + GUARD
)

STATS = ("brawn", "brains", "charm", "sneak", "weird")
COSTABLE = ("food", "money", "supplies", "time", "vehicle", "morale", "reputation")


def _tags(tags: list[str]) -> list[str]:
    out = []
    for t in tags:
        t = t.strip().lower()
        if t in TAG_VOCABULARY and t not in out:
            out.append(t)
    return out[:4]


def _stat(stat: str) -> str:
    stat = (stat or "").strip().lower()
    return stat if stat in STATS else "weird"


def _cost(resource: str, amount: int) -> dict[str, int]:
    resource = (resource or "").strip().lower()
    if resource in COSTABLE and amount > 0:
        return {resource: min(int(amount), 3)}
    return {}


class BaseService:
    max_tokens = 3000

    def __init__(self, provider: LLMProvider | None, settings: Settings):
        self.provider = provider
        self.settings = settings

    async def _call(self, model: type[M], system: str, user: str, max_tokens: int | None = None) -> M | None:
        if self.provider is None:
            return None
        schema = model.model_json_schema()
        attempts = 1 + max(0, self.settings.llm_max_retries)
        prompt = user
        for attempt in range(attempts):
            try:
                raw = await self.provider.complete_json(
                    system=system, user=prompt, schema=schema, max_tokens=max_tokens or self.max_tokens
                )
                return model.model_validate(raw)
            except ValidationError as exc:
                log.warning("%s: invalid structured output (attempt %s): %s", type(self).__name__, attempt + 1, exc)
                prompt = f"{user}\n\nYour previous answer failed validation: {str(exc)[:500]}. Return valid JSON only."
            except LLMError as exc:
                log.warning("%s: provider error: %s", type(self).__name__, exc)
                return None
            except Exception:  # noqa: BLE001 - never let narrative generation crash the game
                log.exception("%s: unexpected provider failure", type(self).__name__)
                return None
        return None


# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------


class ThemeNormalizer(BaseService):
    def normalize(self, text: str) -> str:
        return themes.normalize(clean_text(text, 200))


class ThemeMerger(BaseService):
    async def merge(self, submissions: list[ThemeSubmission]) -> list[ThemeOption]:
        subs = sorted(submissions, key=lambda s: (s.submitted_at, s.player_id))
        texts = [clean_text(s.text, 200) for s in subs]
        embeddings = None
        if self.provider is not None and len(texts) > 1:
            try:
                embeddings = await self.provider.embed(texts)
            except Exception:  # noqa: BLE001
                embeddings = None

        # Ask the LLM once about every borderline pair, then cluster deterministically.
        borderline = [
            (i, j)
            for i in range(len(texts))
            for j in range(i + 1, len(texts))
            if themes.BORDERLINE[0] <= themes.lexical_similarity(texts[i], texts[j]) < themes.BORDERLINE[1]
        ]
        same: set[tuple[int, int]] = set()
        if borderline and self.provider is not None:
            listing = "\n".join(f"{i}: {quote(t, 200)}" for i, t in enumerate(texts))
            pairs = ", ".join(f"[{i},{j}]" for i, j in borderline)
            result = await self._call(
                LLMThemeJudgement,
                STYLE,
                f"Story theme submissions:\n{listing}\n\nFor each candidate pair {pairs}, decide whether both "
                "describe essentially the same adventure premise (same protagonists doing the same thing). "
                "Return only the pairs that are duplicates.",
                800,
            )
            if result:
                same = {tuple(sorted(p[:2])) for p in result.duplicate_pairs if len(p) >= 2}  # type: ignore[misc]
        lookup = {t: i for i, t in enumerate(texts)}

        def judge(a: str, b: str) -> bool | None:
            key = tuple(sorted((lookup[a], lookup[b])))
            return key in same

        cleaned = [s.model_copy(update={"text": t}) for s, t in zip(subs, texts)]
        options = themes.build_options(cleaned, embeddings=embeddings, judge=judge)

        if self.provider is not None and any(len(o.originals) > 1 for o in options):
            listing = "\n".join(
                f"Cluster {n}: " + " | ".join(quote(t, 200) for t in o.originals) for n, o in enumerate(options)
            )
            titles = await self._call(
                LLMThemeTitles,
                STYLE,
                f"{listing}\n\nWrite one short, funny voting-option title (max 10 words) per cluster that "
                "captures the shared premise, e.g. 'Pirates Attempt to Steal the Moon'.",
                600,
            )
            if titles and len(titles.titles) == len(options):
                for o, t in zip(options, titles.titles):
                    if len(o.originals) > 1 and t.strip():
                        o.title = clean_text(t, 120)
        return options


# ---------------------------------------------------------------------------
# Objective & world
# ---------------------------------------------------------------------------


class ObjectiveBundle(BaseModel):
    objective: Objective
    resource_labels: dict[str, str]
    locations: list[Location]
    npcs: list[NPC]
    hidden_variables: list[HiddenVariable]


class ObjectiveGenerator(BaseService):
    async def generate(self, theme: str, seed: int, rounds: int, n_players: int) -> ObjectiveBundle:
        fallback_obj = procgen.make_objective(theme, seed, rounds, n_players)
        result = await self._call(
            LLMObjectiveBundle,
            STYLE,
            f"Winning theme: {quote(theme, 200)}\nPlayers: {n_players}. Adventure length: about {rounds} scenes.\n"
            "Design the shared objective. It must be clear, achievable, difficult, funny, capable of failure and "
            "flexible. Provide: title, a 2-3 sentence description, tagline, 3 success conditions, 3 failure "
            "conditions, 3-4 world rules, the final challenge, an antagonist, a macguffin, themed display labels "
            "for the shared resources (keys: food, money, time, supplies, morale, reputation, vehicle), 6 themed "
            "locations (tags from: dark, high, water, crowded, social, quiet, secrets, open, mechanical, food), "
            "3 NPCs with secrets, and 2 hidden variables — secret facts about the world that players can discover "
            "by doing actions related to the trigger keywords, and which help in the finale.",
            3500,
        )
        if result is None:
            locations = procgen.make_locations(theme, seed)
            npcs = [procgen.make_npc(seed, i, fallback_obj, locations[(i + 1) % len(locations)].id, 0) for i in range(3)]
            return ObjectiveBundle(
                objective=fallback_obj,
                resource_labels={},
                locations=locations,
                npcs=npcs,
                hidden_variables=procgen.make_hidden_variables(seed, fallback_obj),
            )
        objective = Objective(
            title=clean_text(result.title, 120),
            description=clean_text(result.description, 600),
            tagline=clean_text(result.tagline, 140),
            success_conditions=[clean_text(s, 200) for s in result.success_conditions[:4]] or fallback_obj.success_conditions,
            failure_conditions=[clean_text(s, 200) for s in result.failure_conditions[:4]] or fallback_obj.failure_conditions,
            world_rules=[clean_text(s, 200) for s in result.world_rules[:5]] or fallback_obj.world_rules,
            progress_target=fallback_obj.progress_target,  # the engine owns the numbers
            final_challenge=clean_text(result.final_challenge, 200),
            antagonist=clean_text(result.antagonist, 80),
            macguffin=clean_text(result.macguffin, 80),
            approximate_rounds=rounds,
        )
        locations = [
            Location(name=clean_text(l.name, 60), description=clean_text(l.description, 200), tags=[t.lower() for t in l.tags[:4]])
            for l in result.locations[:7]
        ] or procgen.make_locations(theme, seed)
        npcs = [
            NPC(
                name=clean_text(n.name, 60), emoji=clean_text(n.emoji, 4) or "🧑", personality=clean_text(n.personality, 160),
                goal=clean_text(n.goal, 160), knowledge=[clean_text(k, 160) for k in n.knowledge[:3]],
                secrets=[clean_text(s, 160) for s in n.secrets[:2]],
                location_id=locations[(i + 1) % len(locations)].id,
                disposition=rng.stream(seed, "npc_disp", i).randint(-1, 2),
            )
            for i, n in enumerate(result.npcs[:4])
        ]
        hidden = [
            HiddenVariable(fact=clean_text(h.fact, 200), hint=clean_text(h.hint, 240),
                           trigger_keywords=[k.lower()[:24] for k in h.trigger_keywords[:8]] or ["investigate"])
            for h in result.hidden_variables[:3]
        ] or procgen.make_hidden_variables(seed, objective)
        labels = {r.key: clean_text(r.label, 32) for r in result.resource_labels if r.key in COSTABLE}
        return ObjectiveBundle(objective=objective, resource_labels=labels, locations=locations, npcs=npcs, hidden_variables=hidden)


class CharacterGenerator(BaseService):
    async def suggest(self, theme: str, objective: Objective | None, display_name: str, seed: int, key: object) -> Character:
        fallback = procgen.suggest_character(seed, key, display_name)
        result = await self._call(
            LLMCharacter,
            STYLE,
            f"Theme: {quote(theme or '', 200)}\nObjective: {objective.title if objective else 'unknown'}\n"
            f"Player display name: {quote(display_name, 40)}\nInvent a lightweight, funny character for this player "
            "(use their display name as the character name unless it is unusable).",
            800,
        )
        if result is None:
            return fallback
        ch = Character(
            name=clean_text(result.name, 40) or fallback.name,
            archetype=clean_text(result.archetype, 80),
            personality=clean_text(result.personality, 200),
            special_ability=clean_text(result.special_ability, 200),
            weakness=clean_text(result.weakness, 200),
            secret_motivation=clean_text(result.secret_motivation, 200),
            starting_item=clean_text(result.starting_item, 80),
            humorous_trait=clean_text(result.humorous_trait, 200),
            avatar=fallback.avatar,
        )
        ch.stats = procgen.default_stats_for(ch.archetype, ch.special_ability)
        return ch


class NPCGenerator(BaseService):
    async def create(self, seed: int, key: object, objective: Objective | None, location_id: str | None, round_no: int,
                     public_context: str) -> NPC:
        fallback = procgen.make_npc(seed, key, objective, location_id, round_no)
        from ..models.llm_schemas import LLMNPC

        result = await self._call(
            LLMNPC, STYLE,
            f"Public story so far: {public_context}\nInvent one new memorable NPC who fits this adventure: "
            "name, emoji, personality, goal, 2 things they know, 1 secret.",
            600,
        )
        if result is None:
            return fallback
        return fallback.model_copy(update={
            "name": clean_text(result.name, 60), "emoji": clean_text(result.emoji, 4) or fallback.emoji,
            "personality": clean_text(result.personality, 160), "goal": clean_text(result.goal, 160),
            "knowledge": [clean_text(k, 160) for k in result.knowledge[:3]], "secrets": [clean_text(s, 160) for s in result.secrets[:2]],
        })


# ---------------------------------------------------------------------------
# Scenes & decisions
# ---------------------------------------------------------------------------


class SceneGenerator(BaseService):
    async def scene(self, seed: int, beat: str, round_no: int, ctx: dict[str, str], public_context: str) -> tuple[str, str]:
        title, text = procgen.scene_text(seed, beat, round_no, ctx)
        result = await self._call(
            LLMScene, STYLE,
            f"PUBLIC context (everything here is known to all players):\n{public_context}\n\n"
            f"Story beat: {beat}. Location: {ctx.get('loc')}. Write the opening of scene {round_no}: a short title "
            "and 2-4 punchy sentences addressed to the whole party. Do not resolve anything.",
            700,
        )
        if result is None:
            return title, text
        return clean_text(result.title, 80) or title, clean_text(result.narrative, 900) or text


class DecisionSpec(BaseModel):
    group_id: str
    kind: str
    player_names: list[str]
    location: str
    authorized_context: str  # only what every member of this group may know
    fallback_title: str
    fallback_context: str
    fallback_choices: list[Choice]


class DecisionGenerator(BaseService):
    async def generate(self, beat: str, specs: list[DecisionSpec]) -> dict[str, tuple[str, str, list[Choice]]]:
        out = {s.group_id: (s.fallback_title, s.fallback_context, s.fallback_choices) for s in specs}
        if self.provider is None or not specs:
            return out
        # One call per spec keeps contexts isolated: a group's prompt never contains another group's secrets.
        for s in specs:
            result = await self._call(
                LLMDecisionBatch, STYLE,
                f"Story beat: {beat}. Decision type: {s.kind}.\nPlayers in this decision: {', '.join(s.player_names)}.\n"
                f"Location: {s.location}.\nWhat these players know:\n{s.authorized_context}\n\n"
                f"Write ONE decision with group_id '{s.group_id}': a title, a 1-3 sentence situation addressed to "
                f"them ('you'), and 3 distinct choices. Tags must come from {TAG_VOCABULARY}. stat is one of "
                f"{list(STATS)}. risk is safe/risky/wild. cost_resource is one of {list(COSTABLE)} or ''. "
                "At least one choice should be safe and one wild. Make the situation specific and funny.",
                1200,
            )
            if not result or not result.decisions:
                continue
            d = result.decisions[0]
            choices = [
                Choice(label=clean_text(c.label, 80), description=clean_text(c.description, 200), tags=_tags(c.tags) or ["explore"],
                       risk=c.risk, stat=_stat(c.stat), cost=_cost(c.cost_resource, c.cost_amount),
                       advances_objective=bool(c.advances_objective))
                for c in d.choices[:4]
                if c.label.strip()
            ]
            if len(choices) >= 2:
                out[s.group_id] = (clean_text(d.title, 80) or s.fallback_title, clean_text(d.context, 600) or s.fallback_context, choices)
        return out


# ---------------------------------------------------------------------------
# Free-text actions
# ---------------------------------------------------------------------------

KEYWORD_TAGS = {
    "hide": ["hide", "duck", "crouch", "camouflage", "blend"],
    "investigate": ["investigate", "inspect", "examine", "look", "search", "read", "study", "check", "listen", "sniff"],
    "lock": ["lock", "barricade", "block", "seal", "bolt"],
    "warn": ["warn", "alert", "shout to", "signal"],
    "distract": ["distract", "diversion", "decoy", "chicken", "costume", "dress up", "lure"],
    "fight": ["fight", "punch", "attack", "kick", "tackle", "wrestle", "duel", "hit", "slap"],
    "flee": ["run", "flee", "escape", "sprint", "leave", "retreat"],
    "steal": ["steal", "take", "grab", "pocket", "swipe", "pickpocket", "loot", "snatch", "treasure"],
    "negotiate": ["convince", "persuade", "bribe", "negotiate", "ask", "talk", "beg", "flirt", "compliment", "trade", "bargain"],
    "build": ["build", "construct", "make", "craft", "rig", "repair", "fix", "tape", "rope"],
    "destroy": ["burn", "destroy", "smash", "break", "explode", "blow up", "wreck", "fire", "demolish"],
    "help": ["help", "save", "heal", "protect", "carry", "rescue", "assist"],
    "explore": ["explore", "wander", "go to", "head", "find", "follow", "climb down", "tunnel"],
    "betray": ["betray", "abandon", "double-cross", "run away with", "sell out", "leave them", "ditch"],
    "perform": ["sing", "dance", "perform", "jazz", "juggle", "joke", "rap", "play", "magic trick", "speech"],
    "climb": ["climb", "scale", "jump"],
    "cross": ["cross", "swim", "bridge"],
    "rest": ["rest", "sleep", "nap", "eat", "wait"],
}
TAG_STAT = {"hide": "sneak", "steal": "sneak", "explore": "sneak", "betray": "sneak", "investigate": "brains",
            "build": "brains", "lock": "brawn", "fight": "brawn", "climb": "brawn", "cross": "brawn", "destroy": "brawn",
            "negotiate": "charm", "perform": "charm", "warn": "charm", "help": "brawn", "distract": "charm",
            "flee": "sneak", "rest": "brains"}
WILD_WORDS = ["burn", "explode", "betray", "blow up", "jump off", "eat the", "fight the", "lick", "summon", "chicken", "set fire", "dragon"]
IMPOSSIBLE = ["teleport", "time travel", "become god", "kill everyone", "instantly win", "end the game", "omnipotent"]


def interpret_deterministic(text: str, player_names: dict[str, str], npc_names: dict[str, str], objective_words: list[str]) -> ActionInterpretation:
    low = text.lower()
    tags: list[str] = []
    for tag, words in KEYWORD_TAGS.items():
        if any(re.search(rf"\b{re.escape(w)}", low) for w in words):
            tags.append(tag)
    if any(w in low for w in ("loud", "scream", "yell", "bang", "explode", "jazz", "sing")):
        tags.append("noise")
    tags = tags[:4] or ["explore"]
    risk = Risk.WILD if any(w in low for w in WILD_WORDS) else (Risk.SAFE if tags[0] in ("hide", "rest", "investigate", "lock") else Risk.RISKY)
    impossible = any(w in low for w in IMPOSSIBLE)
    cost: dict[str, int] = {}
    if any(w in low for w in ("bribe", "pay", "buy")):
        cost["money"] = 2
    if "build" in tags:
        cost["supplies"] = 1
    target_player = next((pid for pid, name in player_names.items() if name and re.search(rf"\b{re.escape(name.lower())}\b", low)), None)
    target_npc = next((nid for nid, name in npc_names.items() if name and name.split()[0].lower() in low), None)
    if target_player and "help" not in tags and any(w in low for w in ("convince", "make", "get")):
        tags = list(dict.fromkeys(tags + ["negotiate"]))[:4]
    advances = any(w in low for w in objective_words) or any(t in tags for t in ("steal", "explore", "investigate", "negotiate", "build", "cross"))
    consequences = {
        "destroy": "Things nearby may stop existing", "steal": "Someone will notice something is missing",
        "betray": "Trust in the party will drop", "noise": "Everyone in earshot now knows where you are",
        "fight": "Injury is likely", "negotiate": "Their opinion of you will change",
    }
    return ActionInterpretation(
        summary=clean_text(text, 200),
        feasible=not impossible,
        feasibility_note="That's beyond even this universe's loose physics — but you'll try anyway, with a heavy penalty." if impossible else "",
        risk=Risk.WILD if impossible else risk,
        stat=TAG_STAT.get(tags[0], "weird"),
        tags=_tags(tags),
        cost=cost,
        target_player_id=target_player,
        target_npc_id=target_npc,
        advances_objective=advances,
        possible_consequences=[consequences[t] for t in tags if t in consequences][:3],
    )


class ActionInterpreter(BaseService):
    async def interpret(self, text: str, actor_name: str, character_summary: str, situation: str,
                        player_names: dict[str, str], npc_names: dict[str, str], objective_words: list[str]) -> ActionInterpretation:
        base = interpret_deterministic(text, player_names, npc_names, objective_words)
        result = await self._call(
            LLMActionInterpretation, STYLE,
            f"Situation the actor faces: {situation}\nActor: {actor_name} — {character_summary}\n"
            f"Other players: {', '.join(player_names.values())}. Known NPCs: {', '.join(npc_names.values()) or 'none'}.\n"
            f"The actor declares: {quote(text)}\n\nInterpret this free-form action for the game engine. Judge "
            "feasibility (absurd is fine; physically impossible is not), risk (safe/risky/wild), the most relevant "
            f"stat from {list(STATS)}, up to 4 tags from {TAG_VOCABULARY}, an optional shared-resource cost "
            f"({list(COSTABLE)} or ''), any target player or NPC by name (or ''), whether it plausibly advances the "
            "shared objective, and up to 3 possible consequences. Never refuse to interpret — players are allowed "
            "to do unexpected things, including betraying the group.",
            800,
        )
        if result is None:
            return base
        by_name = {v.lower(): k for k, v in player_names.items()}
        npc_by_name = {v.lower(): k for k, v in npc_names.items()}
        return ActionInterpretation(
            summary=clean_text(result.summary, 200) or base.summary,
            feasible=result.feasible,
            feasibility_note=clean_text(result.feasibility_note, 200),
            risk=result.risk,
            stat=_stat(result.stat),
            tags=_tags(result.tags) or base.tags,
            cost=_cost(result.cost_resource, result.cost_amount),
            target_player_id=by_name.get(result.target_player_name.strip().lower()) or base.target_player_id,
            target_npc_id=npc_by_name.get(result.target_npc_name.strip().lower()) or base.target_npc_id,
            advances_objective=result.advances_objective,
            possible_consequences=[clean_text(c, 120) for c in result.possible_consequences[:3]],
        )


# ---------------------------------------------------------------------------
# Consequence narration
# ---------------------------------------------------------------------------


class NarrationRequest(BaseModel):
    """One resolved action, as the engine decided it. The narrator may not change any of it."""

    actor_names: list[str]
    action: str
    tier: str
    tags: list[str]
    consequences: list[str]
    interactions: list[str]
    weakness: str = ""
    trait: str = ""
    fallback: str = ""


class ConsequenceResolver(BaseService):
    async def narrate(self, audience_label: str, authorized_context: str, requests: list[NarrationRequest]) -> list[str]:
        fallbacks = [r.fallback for r in requests]
        if not requests:
            return fallbacks
        listing = "\n".join(
            f"[{i}] actors={r.actor_names} action={quote(r.action)} OUTCOME={r.tier} tags={r.tags} "
            f"consequences={r.consequences} interactions_with_others={r.interactions} weakness={quote(r.weakness, 120)}"
            for i, r in enumerate(requests)
        )
        result = await self._call(
            LLMNarrationBatch, STYLE,
            f"Audience: {audience_label}. What this audience is allowed to know:\n{authorized_context}\n\n"
            f"Resolved actions (outcomes are FINAL and decided by the engine):\n{listing}\n\n"
            "For each action write 2-4 vivid, funny sentences that narrate exactly that outcome and its listed "
            "consequences, using the character names. Mention interactions only as listed. Also give a one-sentence "
            "public_summary.",
            2000,
        )
        if result is None:
            return fallbacks
        out = list(fallbacks)
        for n in result.narrations:
            if 0 <= n.action_index < len(out) and n.narrative.strip():
                out[n.action_index] = clean_text(n.narrative, 1200)
        return out


class NarrativeDirectorLLM(BaseService):
    async def twist(self, public_context: str, fallback: str) -> str:
        result = await self._call(
            LLMTwist, STYLE,
            f"Public story so far:\n{public_context}\n\nPropose ONE short plot twist (1-2 sentences) that escalates "
            "the comedy using an existing character, NPC, or running joke. Do not decide outcomes.",
            400,
        )
        return clean_text(result.twist, 400) if result and result.twist.strip() else fallback


class StorySummarizer(BaseService):
    async def summarize(self, previous: str, recent: list[str]) -> tuple[str, list[str]]:
        fallback = (previous + " " + " ".join(r.split(". ")[0] + "." for r in recent[-6:])).strip()[-1500:]
        result = await self._call(
            LLMSummary, STYLE,
            f"Previous summary:\n{previous or '(none)'}\n\nRecent public events:\n" + "\n".join(recent[-12:]) +
            "\n\nWrite an updated summary (max 180 words) and list unresolved plot threads.",
            800,
        )
        if result is None:
            return fallback, []
        return clean_text(result.summary, 1500), [clean_text(t, 160) for t in result.unresolved_threads[:6]]


# ---------------------------------------------------------------------------
# Final story
# ---------------------------------------------------------------------------


class FinalStoryGenerator(BaseService):
    max_tokens = 16000

    async def generate(self, chronicle_text: str, player_names: list[str], words: tuple[int, int], chapters: int) -> LLMFinalStory | None:
        return await self._call(
            LLMFinalStory, STYLE + " You are now writing the definitive retelling of the whole adventure.",
            f"STORY CHRONICLE (the canonical record — every major fact must come from here):\n{chronicle_text}\n\n"
            f"Write '{GAME_TITLE}' — the complete combined story of this adventure as a polished humorous narrative "
            f"in {chapters} chapters plus per-player epilogues, {words[0]}-{words[1]} words total. Structure: opening "
            "(players, setting, theme, objective), rising action (how they split up), parallel adventures woven "
            "together with 'meanwhile' cuts, hidden events revealed entertainingly, intersections where one "
            "player's choice affected another (the 'OH, THAT's what you were doing!' moments), escalation, climax, "
            "resolution that matches the recorded outcome exactly, epilogue. Never write it as 'Player A did X'. "
            f"Use every character name: {', '.join(player_names)}. Include callbacks to running jokes. Do not "
            "invent major events, items, deaths or outcomes not in the chronicle; small connective details are fine.",
            16000,
        )


class FinalStoryEditor(BaseService):
    max_tokens = 16000

    async def repair(self, story: LLMFinalStory, issues: list[str], chronicle_text: str) -> LLMFinalStory | None:
        return await self._call(
            LLMFinalStory, STYLE + " You are the copy editor and fact-checker for the final story.",
            f"STORY CHRONICLE (canonical):\n{chronicle_text}\n\nDRAFT STORY:\n{json.dumps(story.model_dump())[:60000]}\n\n"
            "The draft failed validation for these reasons:\n- " + "\n- ".join(issues) +
            "\n\nReturn a corrected version of the full story (same structure) that fixes every issue while "
            "keeping the voice.",
            16000,
        )


def chapters_from_llm(story: LLMFinalStory) -> list[StoryChapter]:
    out = []
    for i, ch in enumerate(story.chapters):
        text = ch.text.strip()
        out.append(StoryChapter(index=i, title=clean_text(ch.title, 100), text=text, word_count=len(text.split())))
    return out


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------


class LLMService:
    def __init__(self, provider: LLMProvider | None, settings: Settings):
        self.provider = provider
        self.settings = settings
        self.theme_normalizer = ThemeNormalizer(provider, settings)
        self.theme_merger = ThemeMerger(provider, settings)
        self.objective_generator = ObjectiveGenerator(provider, settings)
        self.character_generator = CharacterGenerator(provider, settings)
        self.scene_generator = SceneGenerator(provider, settings)
        self.decision_generator = DecisionGenerator(provider, settings)
        self.action_interpreter = ActionInterpreter(provider, settings)
        self.consequence_resolver = ConsequenceResolver(provider, settings)
        self.npc_generator = NPCGenerator(provider, settings)
        self.narrative_director = NarrativeDirectorLLM(provider, settings)
        self.story_summarizer = StorySummarizer(provider, settings)
        self.final_story_generator = FinalStoryGenerator(provider, settings)
        self.final_story_editor = FinalStoryEditor(provider, settings)

    @property
    def name(self) -> str:
        return self.provider.name if self.provider else "offline"

    async def aclose(self) -> None:
        if self.provider:
            await self.provider.aclose()


def build_provider(settings: Settings) -> LLMProvider | None:
    kind = (settings.llm_provider or "offline").lower()
    if kind == "offline":
        return None
    if kind == "anthropic":
        from .providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(settings.llm_api_key, settings.llm_model, settings.llm_timeout_seconds)
    if kind in ("openai", "openai_compatible"):
        from .providers.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider(settings.llm_api_key, settings.llm_model, settings.llm_base_url, settings.llm_timeout_seconds)
    raise ValueError(f"unknown LLM provider {kind!r}")


def summarize_any(obj: Any) -> str:  # small helper used in prompts
    return json.dumps(obj, default=str)[:4000]
