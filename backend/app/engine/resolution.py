"""Authoritative action resolution.

Given every action declared in a round, the engine:

1. pays costs,
2. computes cross-player *interactions* (one player's distraction covers
   another's theft; a locked door strands a fleeing friend; a destroyed bridge
   ruins someone's crossing two scenes later),
3. rolls deterministic dice with modifiers from stats, abilities, weaknesses,
   items, resources, NPC dispositions and world scars,
4. maps the result onto an outcome tier,
5. applies concrete consequences to shared and individual resources.

The LLM narrates the results afterwards; it never decides them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..models.game import (
    DelayedConsequence,
    GameState,
    InfoItem,
    Item,
    OutcomeTier,
    Player,
    PlayerStatus,
    ResolvedAction,
    Risk,
    Visibility,
)
from . import rng
from .procgen import join_names, keywords

BASE_TARGET = {Risk.SAFE: 8, Risk.RISKY: 12, Risk.WILD: 15}
CONTRIBUTION = {
    OutcomeTier.CATASTROPHIC_SUCCESS: 3, OutcomeTier.UNEXPECTED_SUCCESS: 3, OutcomeTier.SUCCESS: 2,
    OutcomeTier.PARTIAL_SUCCESS: 1, OutcomeTier.COMPLICATION: 0, OutcomeTier.FAILURE: -1,
}
SUCCESSES = {OutcomeTier.CATASTROPHIC_SUCCESS, OutcomeTier.UNEXPECTED_SUCCESS, OutcomeTier.SUCCESS}
LOOT = ["a suspiciously heavy key", "a laminated map (upside down)", "a single golden potato", "a walkie-talkie tuned to smooth jazz",
        "a VIP lanyard", "a jar of mystery pickles", "a tiny ceremonial crown", "a rubber chicken", "the manager's clipboard"]
SNEAKY = {"hide", "steal", "explore", "cross", "climb", "flee"}


@dataclass
class PlannedAction:
    key: str
    group_id: str
    player_ids: list[str]
    description: str
    tags: list[str]
    risk: Risk
    stat: str
    cost: dict[str, int] = field(default_factory=dict)
    target_player_id: str | None = None
    target_npc_id: str | None = None
    advances_objective: bool = False
    feasible: bool = True
    push_luck: list[str] = field(default_factory=list)
    use_ability: list[str] = field(default_factory=list)
    item_ids: dict[str, str] = field(default_factory=dict)
    rogue: bool = False
    location_id: str | None = None
    final: bool = False
    freeform: bool = False


@dataclass
class ActionOutcome:
    planned: PlannedAction
    result: ResolvedAction
    consequences: list[str] = field(default_factory=list)
    private_notes: dict[str, list[str]] = field(default_factory=dict)  # player -> secret lines
    new_info: list[InfoItem] = field(default_factory=list)
    interaction_partners: list[str] = field(default_factory=list)  # other action keys
    contribution: int = 0
    catastrophe: bool = False
    related: list[str] = field(default_factory=list)  # chronicle events this action depended on


@dataclass
class RoundResolution:
    outcomes: list[ActionOutcome]
    interactions: list[tuple[str, str, str]]  # (from_key, to_key, description)


# ---------------------------------------------------------------------------
# Resource helpers
# ---------------------------------------------------------------------------


def change_shared(state: GameState, key: str, delta: int) -> int:
    track = state.shared_resources.get(key)
    if not track or delta == 0:
        return 0
    before = track.value
    track.value = max(0, min(track.max, track.value + delta))
    return track.value - before


def change_health(p: Player, delta: int) -> int:
    before = p.resources.health
    p.resources.health = max(0, min(10, p.resources.health + delta))
    if p.resources.health == 0 and p.status == PlayerStatus.ACTIVE:
        p.status = PlayerStatus.INCAPACITATED
    elif p.resources.health > 0 and p.status == PlayerStatus.INCAPACITATED:
        p.status = PlayerStatus.ACTIVE
    return p.resources.health - before


def label(state: GameState, key: str) -> str:
    t = state.shared_resources.get(key)
    return t.label if t else key


def describe_delta(state: GameState, key: str, delta: int) -> str:
    if delta == 0:
        return ""
    return f"{label(state, key)} {'+' if delta > 0 else ''}{delta}"


# ---------------------------------------------------------------------------
# Interactions
# ---------------------------------------------------------------------------


def _names(state: GameState, a: PlannedAction) -> str:
    return join_names([state.players[p].display for p in a.player_ids if p in state.players])


def _was(a: PlannedAction) -> str:
    return "were" if len(a.player_ids) > 1 else "was"


def compute_interactions(state: GameState, actions: list[PlannedAction]) -> tuple[dict[str, int], dict[str, list[str]], list[tuple[str, str, str]]]:
    mods: dict[str, int] = {a.key: 0 for a in actions}
    notes: dict[str, list[str]] = {a.key: [] for a in actions}
    links: list[tuple[str, str, str]] = []
    counts: dict[str, int] = {a.key: 0 for a in actions}

    out_counts: dict[str, int] = {a.key: 0 for a in actions}

    def link(src: PlannedAction, dst: PlannedAction, delta: int, text: str) -> None:
        # each action receives at most 2 interactions and causes at most 2, so stories stay legible
        if counts[dst.key] >= 2 or out_counts[src.key] >= 2:
            return
        counts[dst.key] += 1
        out_counts[src.key] += 1
        mods[dst.key] += delta
        notes[dst.key].append(text)
        links.append((src.key, dst.key, text))

    for a in actions:
        for b in actions:
            if a is b or set(a.player_ids) & set(b.player_ids):
                continue
            A, B = _names(state, a), _names(state, b)
            at, bt = set(a.tags), set(b.tags)
            if ("distract" in at or "perform" in at) and bt & SNEAKY:
                link(a, b, 2, f"Elsewhere, {A}'s diversion pulled every eye away — which is the only reason {B} {_was(b)}n't caught.")
            elif "noise" in at and "distract" not in at and bt & {"hide", "steal"}:
                link(a, b, -2, f"Somewhere nearby, {A} made a noise so loud it ruined {B}'s attempt at subtlety.")
            if "lock" in at and bt & {"flee", "explore", "cross"}:
                link(a, b, -2, f"{B} ran straight into a door that had been locked moments earlier — by {A}.")
            if "warn" in at and b.risk != Risk.SAFE:
                link(a, b, 1, f"{B} half-heard {A}'s garbled warning and ducked just in time.")
            if "steal" in at and "negotiate" in bt:
                link(a, b, -2, f"{B} negotiated hard for something that {A} had, unbeknownst to everyone, already stolen.")
            if "destroy" in at and bt & {"cross", "climb", "build"}:
                link(a, b, -3, f"{B} was relying on the very thing {A} had just destroyed.")
            if "help" in at and (a.target_player_id in b.player_ids):
                link(a, b, 2, f"{A} came to {B}'s aid.")
            if "fight" in at and bt & {"hide", "steal"}:
                link(a, b, 1, f"While {A} brawled loudly, nobody was watching {B}.")
            if "investigate" in at and "investigate" in bt and a.key < b.key:
                link(a, b, 1, f"{A} and {B} were investigating the same mystery from opposite sides — and scared each other half to death.")
            if "betray" in at:
                link(a, b, -1, f"{B} couldn't shake the feeling that someone on the team was up to something. (It was {A}.)")
    return mods, notes, links


def scar_modifiers(state: GameState, a: PlannedAction) -> tuple[int, list[str], list[str]]:
    """Persistent world changes from earlier rounds affect later actions."""
    delta, notes, related = 0, [], []
    for scar in state.world_state.get("scars", []):
        if scar["round"] >= state.turn_number:
            continue
        if set(a.player_ids) & {scar["player_id"]}:
            continue
        if scar["kind"] == "destroyed" and set(a.tags) & {"cross", "climb", "explore", "flee"}:
            delta -= 2
            notes.append(f"Unfortunately, {scar['desc']} — courtesy of {scar['player_name']}, back in scene {scar['round']}.")
            related.append(scar["event_id"])
        elif scar["kind"] == "built" and set(a.tags) & {"cross", "climb", "flee", "explore"}:
            delta += 2
            notes.append(f"Luckily, {scar['desc']} — left behind by {scar['player_name']} in scene {scar['round']}.")
            related.append(scar["event_id"])
        if len(notes) >= 1:
            break
    return delta, notes, [r for r in related if r]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _overlap(text: str, other: str) -> bool:
    a, b = set(keywords(text)), set(keywords(other))
    return bool(a & b)


def tier_for(roll: int, margin: int, risk: Risk, seed: int, key: str) -> OutcomeTier:
    if roll == 20:
        return OutcomeTier.CATASTROPHIC_SUCCESS if risk == Risk.WILD else OutcomeTier.UNEXPECTED_SUCCESS
    if roll == 1:
        return OutcomeTier.FAILURE
    if margin >= 0:
        if risk == Risk.WILD and rng.chance(seed, 0.25, "catastrophic", key):
            return OutcomeTier.CATASTROPHIC_SUCCESS
        if roll <= 6:
            return OutcomeTier.UNEXPECTED_SUCCESS
        return OutcomeTier.SUCCESS
    if margin >= -3:
        return OutcomeTier.PARTIAL_SUCCESS
    if margin >= -6:
        return OutcomeTier.COMPLICATION
    return OutcomeTier.FAILURE


def resolve_round(state: GameState, actions: list[PlannedAction], escalation: int) -> RoundResolution:
    seed = state.random_seed
    inter_mods, inter_notes, links = compute_interactions(state, actions)
    outcomes: list[ActionOutcome] = []
    for a in actions:
        outcomes.append(_resolve_one(state, a, escalation, inter_mods[a.key], inter_notes[a.key], seed))
    by_key = {o.planned.key: o for o in outcomes}
    for src, dst, _ in links:
        by_key[dst].interaction_partners.append(src)
        by_key[src].interaction_partners.append(dst)
    return RoundResolution(outcomes=outcomes, interactions=links)


def _resolve_one(state: GameState, a: PlannedAction, escalation: int, inter_mod: int, inter_notes: list[str], seed: int) -> ActionOutcome:
    actors = [state.players[p] for p in a.player_ids if p in state.players]
    lead = max(actors, key=lambda p: p.character.stats.get(a.stat, 1))
    mods: dict[str, int] = {}
    consequences: list[str] = []
    private: dict[str, list[str]] = {}
    shared_changes: dict[str, int] = {}
    player_changes: dict[str, dict[str, int]] = {}

    def pc(pid: str, key: str, delta: int) -> None:
        if delta:
            player_changes.setdefault(pid, {})
            player_changes[pid][key] = player_changes[pid].get(key, 0) + delta

    def sc(key: str, delta: int) -> int:
        actual = change_shared(state, key, delta)
        if actual:
            shared_changes[key] = shared_changes.get(key, 0) + actual
        return actual

    # --- costs ---------------------------------------------------------------
    for key, amount in a.cost.items():
        paid = -sc(key, -amount)
        if paid < amount:
            mods["couldn't afford it"] = mods.get("couldn't afford it", 0) - 3
            consequences.append(f"the party couldn't fully afford it ({label(state, key)} ran dry)")
        for p in actors:
            p.stats.shared_resources_spent += paid

    # --- modifiers -----------------------------------------------------------
    mods["stat"] = lead.character.stats.get(a.stat, 1)
    if len(actors) > 1:
        mods["teamwork"] = min(2, len(actors) - 1)
    for p in actors:
        if p.id in a.use_ability and p.resources.ability_charges > 0:
            p.resources.ability_charges -= 1
            mods["special ability"] = mods.get("special ability", 0) + 3
            pc(p.id, "ability_charges", -1)
        elif _overlap(a.description, p.character.special_ability):
            mods["fits their ability"] = mods.get("fits their ability", 0) + 1
        if p.character.weakness and _overlap(a.description, p.character.weakness):
            mods["weakness"] = mods.get("weakness", 0) - 2
        item_id = a.item_ids.get(p.id)
        if item_id and any(i.id == item_id for i in p.inventory):
            mods["item"] = mods.get("item", 0) + 2
        if p.status == PlayerStatus.INCAPACITATED:
            mods["incapacitated"] = mods.get("incapacitated", 0) - 4
        elif p.resources.health <= 3:
            mods["injured"] = mods.get("injured", 0) - 2
    morale = state.res("morale")
    if morale >= 8:
        mods["high morale"] = 1
    elif morale <= 3:
        mods["low morale"] = -2
    if state.res("food") == 0:
        mods["hungry"] = -1
    if "negotiate" in a.tags:
        mods["reputation"] = (state.res("reputation") - 4) // 2
        npc = state.npcs.get(a.target_npc_id or "")
        if npc:
            mods["npc disposition"] = npc.disposition // 2
    if not a.feasible:
        mods["near-impossible"] = -5
    if inter_mod:
        mods["other players"] = inter_mod
    scar_mod, scar_notes, related = scar_modifiers(state, a)
    if scar_mod:
        mods["world state"] = scar_mod
    mods = {k: v for k, v in mods.items() if v}

    # --- roll ----------------------------------------------------------------
    roll = rng.d20(seed, "roll", state.turn_number, a.key)
    lucky = [p for p in actors if p.id in a.push_luck and p.resources.luck > 0]
    if lucky:
        second = rng.d20(seed, "luck", state.turn_number, a.key)
        for p in lucky[:1]:
            p.resources.luck -= 1
            p.stats.luck_spent += 1
            pc(p.id, "luck", -1)
        if second > roll:
            consequences.append(f"{lucky[0].display} pushed their luck, and luck pushed back (rerolled {roll} → {second})")
            roll = second
    target = BASE_TARGET[a.risk] + max(0, escalation - 1) + (1 if a.final else 0)
    total = roll + sum(mods.values())
    tier = tier_for(roll, total - target, a.risk, seed, a.key)
    catastrophe = roll == 1

    # --- consequences --------------------------------------------------------
    names = _names(state, a)
    progress = 0
    success = tier in SUCCESSES
    if a.advances_objective:
        if tier == OutcomeTier.CATASTROPHIC_SUCCESS:
            progress = 2
        elif tier == OutcomeTier.UNEXPECTED_SUCCESS:
            progress = 2
        elif tier in (OutcomeTier.SUCCESS, OutcomeTier.PARTIAL_SUCCESS):
            progress = 1
    if progress and not a.final:
        state.objective_progress += progress
        consequences.append(f"the party moves {progress} step{'s' if progress > 1 else ''} closer to the objective")

    physical = bool(set(a.tags) & {"fight", "climb", "cross", "destroy", "flee"}) or a.stat == "brawn"
    if tier == OutcomeTier.FAILURE:
        for p in actors:
            dmg = -(3 if catastrophe else (2 if physical else 1))
            d = change_health(p, dmg)
            pc(p.id, "health", d)
            p.stats.failures += 1
            if p.status == PlayerStatus.INCAPACITATED:
                consequences.append(f"{p.display} is knocked out cold")
        if (len(actors) > 1 or catastrophe) and sc("morale", -1):
            consequences.append("morale takes a hit")
    elif tier == OutcomeTier.COMPLICATION:
        key = rng.pick(seed, ["supplies", "money", "reputation", "food", "vehicle"], "complication", a.key)
        if sc(key, -1):
            consequences.append(f"{label(state, key)} drops by 1")
        for p in actors:
            p.stats.complications_caused += 1
            if physical:
                pc(p.id, "health", change_health(p, -1))
    elif success:
        for p in actors:
            p.stats.successes += 1

    if catastrophe:
        key = "supplies" if "build" in a.tags else ("vehicle" if set(a.tags) & {"flee", "cross"} else "reputation")
        sc(key, -2)
        for p in actors:
            p.stats.catastrophes += 1
        consequences.append(f"a genuine catastrophe — {label(state, key)} -2")
    if tier == OutcomeTier.CATASTROPHIC_SUCCESS:
        sc("reputation", -1)
        for p in actors:
            p.stats.catastrophes += 1
        consequences.append("it worked so well that something else is now very broken")

    # tag-driven effects
    tags = set(a.tags)
    if "steal" in tags and (success or tier == OutcomeTier.PARTIAL_SUCCESS):
        loot = rng.pick(seed, LOOT, "loot", a.key)
        for p in actors[:1]:
            p.inventory.append(Item(name=loot, description=f"Acquired by {p.display} in scene {state.turn_number}.", tags=["stolen"], hidden=True))
            p.stats.items_taken += 1
            private.setdefault(p.id, []).append(f"You quietly pocket {loot}. Nobody saw. Probably.")
        if tier == OutcomeTier.PARTIAL_SUCCESS and sc("reputation", -1):
            consequences.append("someone definitely saw something")
    if "negotiate" in tags:
        npc = state.npcs.get(a.target_npc_id or "")
        delta = 2 if tier == OutcomeTier.UNEXPECTED_SUCCESS else (1 if success else (-1 if tier in (OutcomeTier.FAILURE, OutcomeTier.COMPLICATION) else 0))
        if npc and delta:
            npc.disposition = max(-5, min(5, npc.disposition + delta))
            npc.memory.append(f"Scene {state.turn_number}: {names} tried '{a.description[:80]}' — {tier.value.replace('_', ' ')}.")
            consequences.append(f"{npc.name} now feels {'better' if delta > 0 else 'worse'} about the party")
            for p in actors:
                if p.id not in npc.known_by:
                    npc.known_by.append(p.id)
                p.stats.npc_interactions[npc.id] = p.stats.npc_interactions.get(npc.id, 0) + 1
            if success and npc.disposition >= 3 and npc.knowledge:
                fact = npc.knowledge[0]
                private.setdefault(lead.id, []).append(f"{npc.name} leans in and confides that they {fact}.")
        if success:
            for p in actors:
                p.stats.social_successes += 1
            sc("reputation", 1)
        elif tier == OutcomeTier.FAILURE:
            sc("reputation", -1)
    if "help" in tags and (success or tier == OutcomeTier.PARTIAL_SUCCESS):
        tgt = state.players.get(a.target_player_id or "")
        if tgt and tgt.id not in a.player_ids:
            pc(tgt.id, "health", change_health(tgt, 2))
            consequences.append(f"{tgt.display} is patched up (+2 health)")
        elif sc("morale", 1):
            consequences.append("morale rises")
        for p in actors:
            p.stats.helps += 1
    if "perform" in tags and success and sc("morale", 2):
        consequences.append("morale soars (+2)")
    if "rest" in tags:
        for p in actors:
            pc(p.id, "health", change_health(p, 1))
        sc("morale", 1)
    if tags & {"explore", "investigate"} and success:
        if rng.chance(seed, 0.5, "find", a.key):
            sc("supplies", 1)
            consequences.append(f"{label(state, 'supplies')} +1 from a lucky find")
        elif "investigate" in tags:
            sc("money", 1)
            consequences.append("finds some loose change (+1 money)")
    if "build" in tags and success:
        if state.res("vehicle") < state.shared_resources["vehicle"].max and set(a.tags) & {"build"} and rng.chance(seed, 0.4, "repair", a.key):
            sc("vehicle", 1)
            consequences.append(f"{label(state, 'vehicle')} repaired (+1)")
        _add_scar(state, "built", f"a rickety but functional contraption built by {names} still stands", a, lead)
    if "destroy" in tags and tier != OutcomeTier.FAILURE:
        loc = state.locations.get(a.location_id or "")
        where = loc.name if loc else "the area"
        _add_scar(state, "destroyed", f"{where} is now mostly rubble", a, lead)
        if loc and "destroyed" not in loc.state:
            loc.state.append("destroyed")
        sc("reputation", -1)
        consequences.append(f"{where} will never be the same")
    if "betray" in tags:
        for p in actors:
            p.stats.betrayals += 1
            p.resources.trust = max(0, p.resources.trust - 3)
            pc(p.id, "trust", -3)
        if success:
            stolen = -change_shared(state, "money", -2)
            if stolen:
                shared_changes["money"] = shared_changes.get("money", 0) - stolen
            for p in actors[:1]:
                p.inventory.append(Item(name=f"{stolen} coins of party money", tags=["betrayal"], hidden=True))
                private.setdefault(p.id, []).append(f"You slip {stolen} coins of party money into your own pocket. Nobody needs to know.")
        sc("morale", -1)
    if "fight" in tags and success:
        consequences.append(f"{state.objective.antagonist if state.objective else 'the opposition'} is pushed back")

    for p in actors:
        p.stats.actions += 1
        if a.freeform:
            p.stats.freeform_actions += 1
        if a.final:
            p.stats.final_contribution += CONTRIBUTION[tier]
        else:
            p.stats.pre_final_contribution += CONTRIBUTION[tier] + progress
            p.stats.objective_progress += progress
        if len(a.player_ids) == 1:
            p.stats.times_soloed += 1
        if tier == OutcomeTier.FAILURE and a.risk == Risk.WILD:
            if p.stats.highest_risk_failure_roll is None or roll <= p.stats.highest_risk_failure_roll:
                p.stats.highest_risk_failure_roll = roll
        p.path_tags = list(a.tags)

    # --- delayed consequences -------------------------------------------------
    if (catastrophe or tier in (OutcomeTier.CATASTROPHIC_SUCCESS, OutcomeTier.COMPLICATION)) and not a.final:
        if catastrophe or tier == OutcomeTier.CATASTROPHIC_SUCCESS or rng.chance(seed, 0.5, "delayed", a.key):
            key = rng.pick(seed, ["reputation", "supplies", "morale", "time"], "delayed_res", a.key)
            state.delayed_consequences.append(DelayedConsequence(
                trigger_round=state.turn_number + rng.stream(seed, "delay", a.key).randint(1, 2),
                player_ids=list(a.player_ids),
                description=f"what {names} did in scene {state.turn_number} (\"{a.description[:70]}\") comes back to haunt everyone",
                resource_changes={key: -1},
            ))

    # --- hidden variables ----------------------------------------------------
    new_info: list[InfoItem] = []
    words = set(keywords(a.description)) | tags
    for hv in state.hidden_variables:
        if any(p.id in hv.discovered_by for p in actors):
            continue
        hit = bool(words & set(hv.trigger_keywords))
        if not hit and tags & {"investigate", "explore"} and tier in (OutcomeTier.SUCCESS, OutcomeTier.UNEXPECTED_SUCCESS):
            hit = rng.chance(seed, 0.35, "hv", hv.id, a.key)
        if hit and tier != OutcomeTier.FAILURE:
            for p in actors:
                hv.discovered_by.append(p.id)
                p.stats.secrets_found += 1
            new_info.append(InfoItem(
                visibility=Visibility.PRIVATE if len(actors) == 1 else Visibility.GROUP,
                audience=[p.id for p in actors], kind="secret", title="A Hidden Truth", text=hv.hint,
                round=state.turn_number,
            ))
            break

    result = ResolvedAction(
        player_ids=list(a.player_ids), description=a.description, tier=tier, roll=roll, target=target,
        modifiers=mods, tags=list(a.tags), resource_changes=shared_changes, player_changes=player_changes,
        progress=progress, interactions=inter_notes + scar_notes, rogue=a.rogue,
    )
    outcome = ActionOutcome(planned=a, result=result, consequences=consequences, private_notes=private,
                            new_info=new_info, contribution=CONTRIBUTION[tier], catastrophe=catastrophe,
                            related=related)
    return outcome


def _add_scar(state: GameState, kind: str, desc: str, a: PlannedAction, lead: Player) -> None:
    state.world_state.setdefault("scars", []).append({
        "kind": kind, "desc": desc, "round": state.turn_number, "player_id": lead.id,
        "player_name": lead.display, "location_id": a.location_id, "event_id": None, "action_key": a.key,
    })


def round_upkeep(state: GameState) -> list[str]:
    """Time passes, people eat, and the consequences of scarcity land."""
    notes = []
    able = state.able_players()
    if change_shared(state, "time", -1):
        notes.append(f"{label(state, 'time')} -1")
    if state.res("vehicle") == 0 and change_shared(state, "time", -1):
        notes.append(f"with the {label(state, 'vehicle').lower()} broken, everything takes longer ({label(state, 'time')} -1 more)")
    eat = max(1, math.ceil(len(able) / 2))
    had = state.res("food")
    change_shared(state, "food", -eat)
    if had < eat:
        for p in able:
            change_health(p, -1)
        change_shared(state, "morale", -1)
        notes.append(f"the {label(state, 'food').lower()} have run out — everyone is hungry (-1 health each, morale -1)")
    else:
        notes.append(f"the party eats ({label(state, 'food')} -{eat})")
    return notes


def final_score(state: GameState, outcomes: list[ActionOutcome]) -> tuple[int, int, dict[str, int]]:
    """Return (score, target, breakdown) for the final challenge."""
    n = max(1, len(state.active_players()))
    target_progress = max(1, state.objective.progress_target if state.objective else 4)
    contributions = sum(o.contribution for o in outcomes)
    hidden = sum(hv.final_bonus for hv in state.hidden_variables
                 if any(state.players[p].status != PlayerStatus.LEFT for p in hv.discovered_by if p in state.players))
    progress_bonus = round((min(state.objective_progress, target_progress * 1.5) / target_progress - 0.7) * n * 2)
    morale = state.res("morale")
    morale_bonus = 1 if morale >= 7 else (-2 if morale <= 2 else 0)
    time_penalty = -n if state.res("time") <= 0 and state.world_state.get("time_ran_out") else 0
    down = -sum(1 for p in state.active_players() if p.status == PlayerStatus.INCAPACITATED)
    breakdown = {"contributions": contributions, "hidden truths": hidden, "objective progress": progress_bonus,
                 "morale": morale_bonus, "time": time_penalty, "party down": down}
    score = sum(breakdown.values())
    target = math.ceil(n * 1.2) + 2
    return score, target, breakdown
