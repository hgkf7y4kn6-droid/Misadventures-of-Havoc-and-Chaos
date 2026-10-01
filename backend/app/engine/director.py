"""The Narrative Director: pacing, structure, pressure, spotlight, callbacks.

The Director is rule-based so it can be *authoritative* about structure: it
decides each scene's beat, who decides alone or together, how much the
players may talk, whether a random event fires, who gets the spotlight, and
when the finale starts. It guarantees the adventure ends.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models.game import (
    ActiveEvent,
    AdventureLength,
    CommMode,
    GameState,
    PlayerStatus,
    RunningJoke,
    Visibility,
)
from . import rng

BEATS: dict[AdventureLength, list[str]] = {
    AdventureLength.SHORT: ["early_adventure", "first_major_decision", "branching_paths", "escalation"],
    AdventureLength.MEDIUM: ["early_adventure", "first_major_decision", "branching_paths", "resource_pressure",
                             "group_interaction", "major_consequence"],
    AdventureLength.LONG: ["early_adventure", "first_major_decision", "branching_paths", "resource_pressure",
                           "group_interaction", "escalation", "plot_twist", "major_consequence", "escalation"],
}

# beat -> (grouping mode, communication mode)
BEAT_RULES: dict[str, tuple[str, CommMode]] = {
    "early_adventure": ("together", CommMode.OPEN),
    "first_major_decision": ("solo", CommMode.RESTRICTED),  # everyone alone; no groups exist, so: silence
    "branching_paths": ("pairs", CommMode.RESTRICTED),  # only temporary group chat
    "resource_pressure": ("pairs", CommMode.OPEN),
    "group_interaction": ("halves", CommMode.OPEN),
    "escalation": ("solo", CommMode.PRIVATE_ONLY),  # scheming by DM only
    "plot_twist": ("solo", CommMode.DISABLED),
    "major_consequence": ("pairs", CommMode.RESTRICTED),
    "final_challenge": ("final", CommMode.OPEN),
}


@dataclass
class RoundPlan:
    number: int
    beat: str
    comm_mode: CommMode
    escalation: int
    groups: list[list[str]]
    spotlight: str | None = None
    random_event: ActiveEvent | None = None
    callbacks: list[str] = field(default_factory=list)
    is_final: bool = False


def total_rounds(length: AdventureLength) -> int:
    return len(BEATS[length])


def should_start_finale(state: GameState) -> bool:
    if state.turn_number >= state.total_rounds:
        return True
    if state.res("time") <= 0:
        state.world_state["time_ran_out"] = True
        return True
    if state.shared_resources and state.res("morale") <= 0:
        # The party is at breaking point: the finale arrives early, and morale counts against them.
        state.world_state["morale_broke"] = True
        return True
    return False


def is_doomed(state: GameState) -> str | None:
    """Immediate failure conditions the engine checks between scenes."""
    if state.active_players() and all(p.status == PlayerStatus.INCAPACITATED for p in state.active_players()):
        return "Every member of the party is incapacitated."
    return None


def group_players(state: GameState, mode: str, round_no: int) -> list[list[str]]:
    players = [p for p in state.active_players()]
    ids = [p.id for p in players]
    if mode in ("together", "final") or len(ids) <= 1:
        return [ids]
    if mode == "solo":
        return [[pid] for pid in ids]
    # Players following similar paths (same approach tags last scene) end up together.
    ordered = sorted(players, key=lambda p: ((p.path_tags or ["~"])[0], rng.stream(state.random_seed, "grp", round_no, p.id).random()))
    ordered_ids = [p.id for p in ordered]
    if mode == "halves":
        mid = (len(ordered_ids) + 1) // 2
        return [g for g in (ordered_ids[:mid], ordered_ids[mid:]) if g]
    groups = [ordered_ids[i : i + 2] for i in range(0, len(ordered_ids), 2)]
    if len(groups) > 1 and len(groups[-1]) == 1:
        # an odd one out goes it alone half the time — lone wolves make good stories
        if rng.chance(state.random_seed, 0.5, "lonewolf", round_no):
            return groups
        groups[-2].extend(groups.pop())
    return groups


def pick_spotlight(state: GameState, round_no: int) -> str | None:
    players = state.active_players()
    if not players:
        return None
    least = min(p.stats.actions + p.stats.successes for p in players)
    candidates = sorted([p.id for p in players if p.stats.actions + p.stats.successes == least])
    return rng.pick(state.random_seed, candidates, "spotlight", round_no)


def plan_round(state: GameState, round_no: int) -> RoundPlan:
    beats = BEATS[state.settings.adventure_length]
    beat = beats[min(round_no - 1, len(beats) - 1)]
    mode, comm = BEAT_RULES.get(beat, ("pairs", CommMode.OPEN))
    if state.settings.default_comm_mode == CommMode.DISABLED:
        comm = CommMode.DISABLED
    escalation = 1 + (round_no - 1) // 2
    groups = group_players(state, mode, round_no)
    plan = RoundPlan(number=round_no, beat=beat, comm_mode=comm, escalation=escalation, groups=groups,
                     spotlight=pick_spotlight(state, round_no))
    if round_no > 1:
        plan.random_event = draw_random_event(state, round_no, escalation, plan.spotlight)
    plan.callbacks = [j.description for j in state.memory.running_jokes if j.mentions >= 1][:2]
    return plan


def plan_final(state: GameState) -> RoundPlan:
    return RoundPlan(number=state.turn_number + 1, beat="final_challenge", comm_mode=CommMode.OPEN,
                     escalation=1 + state.turn_number // 2, groups=[[p.id for p in state.active_players()]], is_final=True)


# ---------------------------------------------------------------------------
# Random events
# ---------------------------------------------------------------------------

# (category, subject, title, text, resource_changes, audience: "all"|"spotlight")
EVENT_DECK: list[tuple[str, str, str, str, dict[str, int], str]] = [
    ("absurd", "goose", "The Goose Situation", "A goose has somehow acquired your map. It will not be negotiating.", {"time": -1}, "all"),
    ("absurd", "vending machine", "A New Leader Emerges", "The vending machine now considers itself your team leader. It dispenses one free snack as a show of good faith.", {"food": 1}, "all"),
    ("neutral", "potato", "The Potato Offer", "A mysterious stranger offers you exactly one potato. Then leaves. Nobody knows why.", {}, "all"),
    ("helpful", "delivery", "Wrong Delivery", "A delivery driver drops off a box of supplies addressed to 'whoever is causing all this'.", {"supplies": 2}, "all"),
    ("helpful", "wallet", "Found Money", "Someone finds a wallet with cash and a note: 'For emergencies. Or crimes.'", {"money": 3}, "all"),
    ("harmful", "raccoons", "Raccoon Audit", "A family of raccoons performs a surprise audit of your food supply. They take most of it.", {"food": -3}, "all"),
    ("harmful", "rumor", "Bad Press", "A rumor spreads that you are 'the ones who did the thing'. You did not do the thing. Mostly.", {"reputation": -2}, "all"),
    ("harmful", "flat tire", "Flat Tire", "Your transport gets a flat. Then a second flat. Then a third, somehow.", {"vehicle": -2}, "all"),
    ("absurd", "mime", "The Mime", "A mime follows the party for a while, silently judging. Morale is shaken.", {"morale": -1}, "all"),
    ("helpful", "pep talk", "Unexpected Pep Talk", "A motivational poster falls off the wall and lands face-up. It says 'HANG IN THERE'. It works.", {"morale": 2}, "all"),
    ("neutral", "fog", "Fog Rolls In", "A thick fog rolls in. It smells faintly of popcorn.", {}, "all"),
    ("character", "personal", "A Personal Matter", "Something here is suspiciously relevant to {name}'s secret motivation.", {}, "spotlight"),
    ("character", "nemesis", "An Old Nemesis", "{name} spots someone from their past. They wave. {name} does not wave back.", {"morale": -1}, "spotlight"),
    ("helpful", "shortcut", "A Shortcut", "{name} notices a shortcut nobody else saw. They keep it to themselves for now.", {"time": 1}, "spotlight"),
]

CALLBACK_VARIANTS = {
    "goose": ("The Goose Returns", "The goose is back. It is wearing a tiny hat now. It has clearly been promoted.", {"morale": -1}),
    "vending machine": ("The Vending Machine Speaks Again", "The vending machine has issued a memo. It is disappointed in everyone.", {"morale": -1}),
    "potato": ("The Potato's Purpose", "Someone remembers the potato. It turns out to be exactly what was needed.", {"supplies": 1}),
    "raccoons": ("The Raccoons Have Unionized", "The raccoons are back, with demands and a spokesperson.", {"food": -1}),
    "mime": ("The Mime Remembers", "The mime is back, mimicking your previous mistakes in exhausting detail.", {"morale": -1}),
}


def draw_random_event(state: GameState, round_no: int, escalation: int, spotlight: str | None) -> ActiveEvent | None:
    seed = state.random_seed
    p = 0.45 + 0.08 * escalation
    if not rng.chance(seed, p, "event", round_no):
        return None
    low = [k for k, t in state.shared_resources.items() if t.value <= 1]
    flush = state.res("morale") >= 7 and state.res("food") >= 6

    # Callbacks: bring back a running joke
    jokes = [j for j in state.memory.running_jokes if j.subject in CALLBACK_VARIANTS]
    if jokes and rng.chance(seed, 0.45, "callback", round_no):
        j = rng.pick(seed, jokes, "callback_pick", round_no)
        title, text, changes = CALLBACK_VARIANTS[j.subject]
        return ActiveEvent(round=round_no, title=title, text=text, category="callback", resource_changes=dict(changes), subject=j.subject)

    deck = EVENT_DECK
    if low:
        deck = [e for e in EVENT_DECK if e[0] in ("helpful", "neutral", "absurd")] or EVENT_DECK
    elif flush:
        deck = [e for e in EVENT_DECK if e[0] in ("harmful", "absurd", "character")] or EVENT_DECK
    used = {e.subject for e in state.active_events}
    fresh = [e for e in deck if e[1] not in used] or deck
    cat, subject, title, text, changes, audience = rng.pick(seed, fresh, "event_pick", round_no)
    name = state.players[spotlight].display if spotlight and spotlight in state.players else "Someone"
    ev = ActiveEvent(round=round_no, title=title, text=text.format(name=name), category=cat,
                     resource_changes=dict(changes), subject=subject)
    if audience == "spotlight" and spotlight:
        ev.player_ids = [spotlight]
        ev.visibility = Visibility.PRIVATE
        if subject == "personal":
            secret = state.players[spotlight].character.secret_motivation
            ev.text = f"You notice something that could help with your secret goal — '{secret}'. Nobody else noticed."
    return ev


def register_joke(state: GameState, subject: str, description: str, round_no: int, event_id: str | None) -> None:
    for j in state.memory.running_jokes:
        if j.subject == subject:
            j.mentions += 1
            j.last_round = round_no
            if event_id:
                j.event_ids.append(event_id)
            return
    state.memory.running_jokes.append(RunningJoke(subject=subject, description=description, first_round=round_no,
                                                  last_round=round_no, event_ids=[event_id] if event_id else []))
