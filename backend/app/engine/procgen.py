"""Procedural content: the deterministic "offline narrator".

Every LLM service has a procedural fallback built from this module, so the
game is fully playable with no network access, tests are reproducible, and a
flaky provider never stalls a table of players mid-adventure.

The content is deliberately specific and silly; generic prose is the enemy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models.game import (
    NPC,
    Character,
    Choice,
    HiddenVariable,
    Location,
    Objective,
    ResourceTrack,
    Risk,
)
from . import rng

STOPWORDS = set(
    """a an the of to and or in on at by for with from into onto inside trying try tries attempt attempting
    attempts who that which is are be becomes become accidentally group bunch some their its it this
    our your my we they them very extremely incredibly somehow while before after during as up out""".split()
)

# Verb synonyms used both for theme normalisation and for parsing goals.
VERB_CANON = {
    "steal": "steal", "stealing": "steal", "steals": "steal", "rob": "steal", "robbing": "steal",
    "heist": "steal", "swipe": "steal", "pilfer": "steal", "nab": "steal", "snatch": "steal", "loot": "steal",
    "deliver": "deliver", "delivering": "deliver", "bring": "deliver", "transport": "deliver",
    "escape": "escape", "escaping": "escape", "flee": "escape", "leave": "escape",
    "find": "find", "finding": "find", "locate": "find", "search": "find", "discover": "find",
    "save": "save", "saving": "save", "rescue": "save", "rescuing": "save",
    "become": "become", "becomes": "become", "becoming": "become",
    "win": "win", "winning": "win", "cook": "cook", "cooking": "cook", "bake": "cook",
    "catch": "catch", "catching": "catch", "capture": "catch", "stop": "stop", "stopping": "stop",
    "prevent": "stop", "build": "build", "building": "build", "survive": "survive", "surviving": "survive",
    "return": "return", "returning": "return", "sell": "sell", "selling": "sell",
    "destroy": "destroy", "destroying": "destroy", "open": "open", "opening": "open",
    "throw": "throw", "plan": "plan", "planning": "plan", "host": "host", "hosting": "host",
    "trapped": "escape", "stuck": "escape", "lost": "escape",
}

NOUN_CANON = {
    "pirate": "pirate", "pirates": "pirate", "buccaneers": "pirate", "corsairs": "pirate",
    "moon": "moon", "lunar": "moon", "knights": "knight", "knight": "knight",
    "raccoons": "raccoon", "raccoon": "raccoon", "trash pandas": "raccoon",
    "wizards": "wizard", "wizard": "wizard", "mages": "wizard",
    "office": "office", "coworkers": "office", "employees": "office", "workers": "worker",
    "ghosts": "ghost", "haunted": "ghost", "ghost": "ghost", "pizza": "pizza", "pizzas": "pizza",
    "robots": "robot", "robot": "robot", "aliens": "alien", "alien": "alien",
    "dragons": "dragon", "dragon": "dragon", "cats": "cat", "cat": "cat", "dogs": "dog", "dog": "dog",
    "zombies": "zombie", "zombie": "zombie", "vampires": "vampire", "vampire": "vampire",
}

GOAL_SPLITTERS = [
    " trying to ", " attempting to ", " attempt to ", " try to ", " tries to ", " who must ", " must ",
    " have to ", " has to ", " need to ", " needs to ", " who want to ", " want to ", " wants to ",
    " accidentally ", " who ",
]


@dataclass
class ThemeProfile:
    theme: str
    subject: str  # "incompetent pirates"
    goal: str  # "steal the moon"
    setting: str  # "the moon"
    keywords: list[str] = field(default_factory=list)
    verb: str = "survive"


def title_case(text: str) -> str:
    small = {"a", "an", "the", "of", "to", "and", "or", "in", "on", "at", "for", "with", "before"}
    words = text.strip().split()
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        out.append(lw if (i and lw in small) else (w[:1].upper() + w[1:]))
    return " ".join(out)


def keywords(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z']+", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 2]


def profile_theme(theme: str) -> ThemeProfile:
    t = " " + re.sub(r"\s+", " ", theme.strip().rstrip(".!?")) + " "
    lower = t.lower()
    subject, goal = "", ""
    for sp in GOAL_SPLITTERS:
        idx = lower.find(sp)
        if idx > 0:
            subject, goal = t[:idx].strip(), t[idx + len(sp):].strip()
            break
    if not goal:
        words = t.strip().split()
        for i, w in enumerate(words):
            if w.lower() in VERB_CANON and i > 0:
                subject, goal = " ".join(words[:i]), " ".join(words[i:])
                break
    if not goal:
        subject, goal = "a group of deeply unqualified heroes", f"survive {t.strip()}"
    subject = re.sub(r"^(a group of|a bunch of|a team of|a gang of|some)\s+", "", subject, flags=re.I).strip() or "heroes"
    verb_word = goal.split()[0].lower() if goal.split() else "survive"
    verb = VERB_CANON.get(verb_word, verb_word)
    setting = ""
    m = re.search(r"\b(?:inside|in|at|on|aboard|beneath|under)\s+(?:a |an |the )?([\w' -]+)$", t.strip(), re.I)
    if m:
        setting = m.group(1).strip()
    if not setting:
        obj = " ".join(goal.split()[1:])
        obj = re.sub(r"^(a|an|the)\s+", "", obj, flags=re.I)
        setting = obj.split(" before ")[0].split(" while ")[0] or "the unknown"
    return ThemeProfile(theme=theme.strip(), subject=subject, goal=goal, setting=setting, keywords=keywords(theme), verb=verb)


# ---------------------------------------------------------------------------
# Objective, resources, world
# ---------------------------------------------------------------------------

DEADLINES = {
    "deliver": ["before it gets cold", "before the customer leaves a one-star review", "by 7:45 sharp"],
    "steal": ["before anyone notices it's missing", "before the security guard finishes his sandwich", "before dawn"],
    "escape": ["before sunrise", "before the doors lock forever", "before closing time"],
    "default": ["before sunrise", "before Tuesday", "before the authorities arrive", "before the snacks run out"],
}
MACGUFFIN_ADJ = ["legendary", "cursed", "slightly damp", "emergency", "forbidden", "ceremonial", "extremely large"]
MACGUFFIN_NOUN = ["pallet of pizza rolls", "golden spork", "ancestral fanny pack", "laminated map", "rubber duck of destiny",
                  "VIP lanyard", "jar of mayonnaise", "commemorative spoon", "tiny crown"]
ANTAGONISTS = ["a bureaucrat with a clipboard", "an extremely territorial goose", "the Night Manager",
               "a rival team of overconfident influencers", "a sentient fog", "Barbara from Compliance",
               "a mime who has seen things", "the Regional Inspector"]


def make_objective(theme: str, seed: int, rounds: int, n_players: int) -> Objective:
    p = profile_theme(theme)
    deadline = rng.pick(seed, DEADLINES.get(p.verb, DEADLINES["default"]), "deadline")
    mac = f"the {rng.pick(seed, MACGUFFIN_ADJ, 'macadj')} {rng.pick(seed, MACGUFFIN_NOUN, 'macnoun')}"
    antagonist = rng.pick(seed, ANTAGONISTS, "antagonist")
    has_deadline = bool(re.search(r"\b(before|by|until|within)\b", p.goal, re.I))
    goal = p.goal[0].upper() + p.goal[1:]
    if has_deadline:
        deadline = ""
    title = title_case(f"{p.goal} {deadline}".strip())
    target = max(3, rounds - 1 + n_players // 4)
    return Objective(
        title=title,
        description=(f"{(goal + ' ' + deadline).strip()} — while also recovering {mac}, which everyone agrees is "
                     f"'important' for reasons nobody can fully explain. Standing in the way: {antagonist}."),
        tagline=f"{title_case(p.subject)}. One plan. Zero competence.",
        success_conditions=[
            f"Make at least {target} points of real progress toward the goal",
            f"Survive the final challenge: a showdown with {antagonist}",
            "At least one member of the party is still standing at the end",
        ],
        failure_conditions=[
            "Time runs out before the final challenge is overcome",
            "Every member of the party is incapacitated",
            "Morale hits zero and the party descends into a group-chat argument",
        ],
        world_rules=[
            f"{antagonist[0].upper() + antagonist[1:]} is always one step behind you. Usually.",
            "Loud plans attract attention. Quiet plans attract suspicion.",
            "Anything that can be touched will, eventually, be touched by someone.",
            "Every resource the party spends is gone. Nobody is coming to refill the snacks.",
        ],
        progress_target=target,
        final_challenge=f"The final confrontation with {antagonist} over {mac}",
        antagonist=antagonist,
        macguffin=mac,
        approximate_rounds=rounds,
    )


def make_resources(theme: str, n_players: int, rounds: int) -> dict[str, ResourceTrack]:
    p = profile_theme(theme)
    food_label = "Snacks"
    if "pizza" in theme.lower():
        food_label = "Pizza Slices"
    elif "pirate" in theme.lower():
        food_label = "Ship Biscuits"
    vehicle = "Getaway Vehicle"
    if any(k in theme.lower() for k in ("ship", "pirate", "boat")):
        vehicle = "Leaky Ship"
    elif "knight" in theme.lower():
        vehicle = "Reluctant Horse"
    return {
        "food": ResourceTrack(key="food", label=food_label, emoji="🍕", value=n_players * 2 + 2, max=n_players * 3 + 4,
                              description="Eaten every scene. Running out hurts everyone's health and mood."),
        "money": ResourceTrack(key="money", label="Money", emoji="💰", value=6, max=15,
                               description="Bribes, purchases, and poorly-considered investments."),
        "time": ResourceTrack(key="time", label="Time Left", emoji="⏳", value=rounds + 3, max=rounds + 3,
                              description="Every scene costs time. At zero, the final challenge starts — ready or not."),
        "supplies": ResourceTrack(key="supplies", label="Supplies", emoji="🧰", value=5, max=10,
                                  description="Rope, tape, and duct-tape-adjacent materials. Needed to build and repair."),
        "morale": ResourceTrack(key="morale", label="Morale", emoji="🎉", value=6, max=10,
                                description="High morale helps every roll. Low morale hurts them. Zero morale ends the party."),
        "reputation": ResourceTrack(key="reputation", label="Reputation", emoji="📣", value=3, max=10,
                                    description=f"How the locals of {p.setting} feel about you. Affects every negotiation."),
        "vehicle": ResourceTrack(key="vehicle", label=vehicle, emoji="🛻", value=5, max=5,
                                 description="Condition of your transport. Broken transport makes travel cost extra time."),
    }


LOCATION_TEMPLATES = [
    ("The {S} Loading Dock", "Concrete, echoes, and one flickering light that clearly knows something.", ["dark", "mechanical"]),
    ("The Suspicious Bridge", "A rope bridge with the structural integrity of a strong opinion.", ["high", "water"]),
    ("The Gift Shop of Regret", "Everything is overpriced, and some of it is watching you.", ["crowded", "social"]),
    ("The Back Office", "Filing cabinets, a dying ficus, and a calendar stuck on a month that doesn't exist.", ["quiet", "secrets"]),
    ("The Rooftop", "Windy, high, and inexplicably home to a hot tub.", ["high", "open"]),
    ("The Food Court of {S}", "Every stall is closed except one, which only sells soup.", ["crowded", "food"]),
    ("The Basement Under {S}", "It goes down further than buildings are legally allowed to go.", ["dark", "secrets"]),
    ("The Grand Hall", "Banners, echoes, and a chandelier that sways when nobody is touching it.", ["open", "social"]),
    ("The Maintenance Tunnels", "Pipes, steam, and a laminated sign reading 'DEFINITELY NO GOBLINS'.", ["dark", "mechanical"]),
]


def make_locations(theme: str, seed: int, count: int = 6) -> list[Location]:
    p = profile_theme(theme)
    setting = title_case(p.setting)[:40]
    picks = rng.shuffled(seed, LOCATION_TEMPLATES, "locations")[:count]
    return [Location(name=name.format(S=setting), description=desc, tags=list(tags)) for name, desc, tags in picks]


NPC_FIRST = ["Gary", "Madame Ostrich", "Kevin", "Duchess Pamela", "Sir Reginald Bottomsworth", "Tina", "The Pretzel Man",
             "Dr. Feldspar", "Grandma Vortex", "Chad", "Bartholomew", "A Goose Named Steve", "Officer Dumpling", "Lorraine"]
NPC_ROLE = ["night manager", "self-appointed tour guide", "retired villain", "vending machine technician",
            "suspiciously helpful stranger", "lost intern", "local influencer", "fortune teller", "security guard on his last day"]
NPC_PERSONALITY = ["speaks exclusively in customer-service phrases", "trusts nobody, especially themselves",
                   "is extremely enthusiastic about everything, including danger", "deadpan and tired of all of this",
                   "dramatic, prone to monologues", "nervous, apologizes to furniture", "aggressively helpful"]
NPC_GOAL = ["get promoted before the end of the night", "protect a secret stash of {M}", "find their missing hat",
            "be included in literally anything", "get revenge on {A}", "retire somewhere quiet with a boat"]
NPC_EMOJI = ["🧑‍💼", "🦢", "🧙", "👵", "🕵️", "🤡", "🧛", "🤖", "🐐", "🦝", "👮", "🧑‍🍳"]


def make_npc(seed: int, key: object, objective: Objective | None, location_id: str | None, round_no: int) -> NPC:
    first = rng.pick(seed, NPC_FIRST, "npc_name", key)
    role = rng.pick(seed, NPC_ROLE, "npc_role", key)
    mac = objective.macguffin if objective else "the snacks"
    ant = objective.antagonist if objective else "everyone"
    goal = rng.pick(seed, NPC_GOAL, "npc_goal", key).format(M=mac, A=ant)
    return NPC(
        name=f"{first} the {title_case(role)}" if " " not in first else first,
        emoji=rng.pick(seed, NPC_EMOJI, "npc_emoji", key),
        personality=rng.pick(seed, NPC_PERSONALITY, "npc_pers", key),
        goal=goal,
        knowledge=[f"knows a shortcut past {ant}", f"once saw {mac} up close"],
        secrets=[rng.pick(seed, ["is secretly working for " + ant, "is afraid of pigeons", "has never actually read the rules",
                                 "is three raccoons in a trench coat", "owns the building"], "npc_secret", key)],
        location_id=location_id,
        disposition=rng.stream(seed, "npc_disp", key).randint(-1, 2),
        introduced_round=round_no,
    )


HIDDEN_FACTS = [
    ("{A} is terrified of jazz", "You find a crumpled note: 'NO SAXOPHONES. EVER. — {A}'.", ["music", "jazz", "sing", "song", "saxophone", "perform", "dance", "noise"]),
    ("{M} is actually a decoy; the real one is in the lost-and-found", "A receipt shows {M} was 'checked in' at the lost-and-found.", ["lost", "found", "search", "office", "investigate", "receipt", "box"]),
    ("{A} can be defeated by a sincere compliment", "A therapist's business card reads: 'Session notes for {A}: has never been complimented.'", ["compliment", "praise", "nice", "kind", "flatter", "negotiate", "talk"]),
    ("the back door was never locked", "You notice the back door has no lock. It never did. Nobody checked.", ["door", "back", "lock", "explore", "sneak", "exit"]),
    ("the goose has the map", "You spot a goose. It is holding a map. It sees you seeing it.", ["goose", "bird", "map", "animal", "chase"]),
    ("{A} is allergic to glitter", "A bottle of antihistamines labeled '{A} — GLITTER EMERGENCY'.", ["glitter", "craft", "build", "decorate", "throw", "art"]),
]


def make_hidden_variables(seed: int, objective: Objective, count: int = 2) -> list[HiddenVariable]:
    picks = rng.shuffled(seed, HIDDEN_FACTS, "hidden")[:count]
    out = []
    for fact, hint, kws in picks:
        fmt = {"A": objective.antagonist, "M": objective.macguffin}
        out.append(HiddenVariable(fact=fact.format(**fmt), hint=hint.format(**fmt), trigger_keywords=kws, final_bonus=2))
    return out


# ---------------------------------------------------------------------------
# Characters
# ---------------------------------------------------------------------------

ARCHETYPES = [
    ("Extremely Confident Accountant", "Can calculate probabilities instantly", "Cannot resist correcting people",
     "Believes every problem can be solved with spreadsheets", "Laminated pie chart", "brains"),
    ("Retired Stunt Double", "Can fall off anything without injury", "Narrates own actions in slow motion",
     "Wants one last dramatic scene", "Crash helmet with stickers", "brawn"),
    ("Disgraced Magician", "Can make small objects vanish (sometimes permanently)", "Must announce every trick",
     "Secretly searching for a rabbit that disappeared in 2009", "Deck of 51 cards", "weird"),
    ("Overqualified Intern", "Knows where everything is kept", "Physically unable to say no",
     "Wants a reference letter from literally anyone", "Lanyard with 14 keycards", "sneak"),
    ("Aggressively Friendly Neighbor", "Everyone instantly trusts them", "Overshares constantly",
     "Is planning a surprise party for someone who didn't ask", "Casserole dish (full)", "charm"),
    ("Conspiracy Podcaster", "Notices every hidden detail", "Thinks every detail is connected",
     "Wants proof of birds being real", "Tinfoil hat (decorative)", "brains"),
    ("Former Child Prodigy", "Can play any instrument instantly", "Peaked at age nine",
     "Wants to be famous again", "Kazoo of considerable quality", "charm"),
    ("Cryptid Enthusiast", "Can communicate with animals (badly)", "Easily distracted by footprints",
     "Wants to be the cryptid", "Night-vision binoculars", "weird"),
    ("Extreme Couponer", "Can get a discount on anything", "Hoards everything",
     "Owes money to a very large man named Doug", "Accordion folder of coupons", "sneak"),
    ("Medieval Reenactor Who Never Stopped", "Surprisingly good with a foam sword", "Refuses to acknowledge the modern world",
     "Wants to be knighted by a real monarch", "Foam sword (heavily dented)", "brawn"),
]
FIRST_NAMES = ["Greg", "Priya", "Dolores", "Tobias", "Mei", "Fitz", "Agnes", "Rafael", "Bex", "Otto", "Juniper", "Sal", "Nadia", "Clive"]
TRAITS = ["hums the Jeopardy theme under pressure", "carries exactly one emergency olive", "names every inanimate object",
          "laughs at the wrong moments, loudly", "has strong opinions about fonts", "always knows what time it is",
          "has a sworn enemy who is a pigeon", "speaks in movie trailer voice when nervous"]
AVATARS = ["🦊", "🐙", "🦉", "🐸", "🦄", "🐻", "🐼", "🦖", "🐝", "🦩", "🐢", "🦔", "🐧", "🦦"]


def suggest_character(seed: int, key: object, display_name: str = "") -> Character:
    arch = rng.pick(seed, ARCHETYPES, "arch", key)
    stats = {s: 1 for s in ("brawn", "brains", "charm", "sneak", "weird")}
    stats[arch[5]] = 3
    r = rng.stream(seed, "stats", key)
    others = [s for s in stats if s != arch[5]]
    stats[r.choice(others)] = 2
    return Character(
        name=display_name or rng.pick(seed, FIRST_NAMES, "fname", key),
        archetype=arch[0],
        personality=rng.pick(seed, ["overconfident and loud", "anxious but determined", "chaotically optimistic",
                                    "suspicious of everyone", "calm in a way that worries people"], "pers", key),
        special_ability=arch[1],
        weakness=arch[2],
        secret_motivation=arch[3],
        starting_item=arch[4],
        humorous_trait=rng.pick(seed, TRAITS, "trait", key),
        avatar=rng.pick(seed, AVATARS, "avatar", key),
        stats=stats,
    )


def default_stats_for(archetype: str, ability: str) -> dict[str, int]:
    text = f"{archetype} {ability}".lower()
    stats = {s: 1 for s in ("brawn", "brains", "charm", "sneak", "weird")}
    hints = {
        "brawn": ["strong", "fight", "stunt", "knight", "sword", "lift", "athlet", "wrestl", "soldier"],
        "brains": ["calculat", "smart", "accountant", "scien", "doctor", "know", "hack", "engineer", "notice"],
        "charm": ["friend", "trust", "talk", "sing", "music", "charm", "sales", "influenc", "perform"],
        "sneak": ["sneak", "thief", "intern", "hide", "find", "coupon", "spy", "quiet", "steal"],
        "weird": ["magic", "wizard", "animal", "cryptid", "ghost", "vanish", "weird", "psychic", "cursed"],
    }
    best = max(hints, key=lambda s: sum(h in text for h in hints[s]))
    if sum(h in text for h in hints[best]) == 0:
        best = "weird"
    stats[best] = 3
    return stats


# ---------------------------------------------------------------------------
# Situations (decision contexts) — templated choices with mechanical tags
# ---------------------------------------------------------------------------

def C(label: str, tags: list[str], risk: Risk, stat: str, desc: str = "", cost: dict[str, int] | None = None, adv: bool = False) -> Choice:
    return Choice(label=label, description=desc, tags=tags, risk=risk, stat=stat, cost=cost or {}, advances_objective=adv)


def individual_situations(ctx: dict[str, str]) -> list[tuple[str, str, list[Choice]]]:
    """Each returns (title, context, choices). ``ctx`` has loc, threat, mac, npc, setting."""
    return [
        ("Something Is Coming",
         "You hear something enormous approaching from {loc}. The floor vibrates in a way floors should not.".format(**ctx),
         [C("Hide and hold your breath", ["hide"], Risk.SAFE, "sneak"),
          C("Climb the nearest tall thing", ["climb", "explore"], Risk.RISKY, "brawn", adv=True),
          C("Investigate. Obviously.", ["investigate"], Risk.RISKY, "brains", adv=True)]),
        ("The Door",
         "Something strange is pushing against the door to {loc}. It knocks twice, politely.".format(**ctx),
         [C("Lock the door and barricade it", ["lock", "build"], Risk.SAFE, "brawn", cost={"supplies": 1}),
          C("Open it and investigate", ["investigate"], Risk.WILD, "weird", adv=True),
          C("Run and warn the others", ["warn", "flee"], Risk.SAFE, "sneak")]),
        ("Unattended Valuables",
         "In {loc} you find {mac}'s display case. It is unguarded. It is SO unguarded.".format(**ctx),
         [C("Take it. Quietly.", ["steal"], Risk.RISKY, "sneak", adv=True),
          C("Check it for traps first", ["investigate"], Risk.SAFE, "brains"),
          C("Smash the glass", ["destroy", "steal"], Risk.WILD, "brawn", adv=True)]),
        ("A Stranger Approaches",
         "{npc} sidles up to you in {loc} and whispers, 'Psst. Want to buy a shortcut?'".format(**ctx),
         [C("Haggle aggressively", ["negotiate"], Risk.RISKY, "charm", cost={"money": 1}, adv=True),
          C("Pay full price", ["negotiate"], Risk.SAFE, "charm", cost={"money": 3}, adv=True),
          C("Pickpocket them mid-sentence", ["steal", "betray"], Risk.WILD, "sneak")]),
        ("The Control Panel",
         "In {loc} there's a control panel with one big red button and a sticky note that says 'DO NOT'.".format(**ctx),
         [C("Press the button", ["destroy", "noise"], Risk.WILD, "weird", adv=True),
          C("Rewire it carefully", ["build"], Risk.RISKY, "brains", cost={"supplies": 1}, adv=True),
          C("Write 'DO' on a new sticky note and leave", ["distract"], Risk.SAFE, "weird")]),
        ("The Snack Situation",
         "You find a vending machine in {loc}. It is fully stocked. It is also humming a threatening tune.".format(**ctx),
         [C("Feed it money", ["negotiate"], Risk.SAFE, "charm", cost={"money": 1}),
          C("Shake it violently", ["fight", "noise", "steal"], Risk.RISKY, "brawn"),
          C("Ask it for directions", ["investigate", "negotiate"], Risk.WILD, "weird", adv=True)]),
        ("The Bridge",
         "You reach {loc}. The bridge ahead looks unsafe. A sign reads 'PROBABLY FINE'.".format(**ctx),
         [C("Cross normally", ["cross"], Risk.RISKY, "brawn", adv=True),
          C("Build a makeshift rope line", ["build", "cross"], Risk.SAFE, "brains", cost={"supplies": 2}, adv=True),
          C("Saw through it so nothing can follow", ["destroy"], Risk.RISKY, "brawn")]),
        ("The Distraction Opportunity",
         "From {loc} you can see {threat} pacing back and forth. Your friends are somewhere beyond.".format(**ctx),
         [C("Create a loud distraction", ["distract", "noise"], Risk.RISKY, "charm"),
          C("Sneak past", ["hide", "explore"], Risk.RISKY, "sneak", adv=True),
          C("Introduce yourself", ["negotiate"], Risk.WILD, "charm")]),
        ("The Mysterious Filing Cabinet",
         "In {loc} you find a filing cabinet labelled 'CONFIDENTIAL — ESPECIALLY FROM YOU'.".format(**ctx),
         [C("Read everything", ["investigate"], Risk.SAFE, "brains"),
          C("Take the files to sell later", ["steal"], Risk.RISKY, "sneak", adv=True),
          C("Set it on fire", ["destroy", "noise"], Risk.WILD, "weird")]),
        ("Someone Needs Help",
         "You hear a familiar voice calling for help from near {loc}. It might be a friend. It might be a trap.".format(**ctx),
         [C("Rush to help", ["help"], Risk.RISKY, "brawn"),
          C("Shout encouragement from a safe distance", ["help", "noise"], Risk.SAFE, "charm"),
          C("Use the distraction to go on alone", ["explore", "betray"], Risk.RISKY, "sneak", adv=True)]),
        ("The Performance",
         "A spotlight suddenly snaps on in {loc}. An audience of {setting} locals waits. They want a show.".format(**ctx),
         [C("Perform your heart out", ["perform", "distract", "noise"], Risk.RISKY, "charm"),
          C("Use the spotlight as cover to slip away", ["hide", "explore"], Risk.RISKY, "sneak", adv=True),
          C("Heckle yourself", ["perform"], Risk.WILD, "weird")]),
    ]


def group_situations(ctx: dict[str, str]) -> list[tuple[str, str, list[Choice]]]:
    return [
        ("The Suspicious Bridge",
         "Together you discover {loc}. The way forward looks unsafe. You can cross normally, construct a makeshift rope, or turn around.".format(**ctx),
         [C("Cross normally", ["cross"], Risk.RISKY, "brawn", adv=True),
          C("Construct a makeshift rope", ["build", "cross"], Risk.SAFE, "brains", cost={"supplies": 2}, adv=True),
          C("Turn around and find another way", ["explore"], Risk.SAFE, "brains", cost={"time": 1})]),
        ("The Guardian",
         "{npc} blocks the way through {loc}, arms folded. 'Nobody passes without a reason,' they say.".format(**ctx),
         [C("Give them a reason (a moving speech)", ["negotiate", "perform"], Risk.RISKY, "charm", adv=True),
          C("Bribe them", ["negotiate"], Risk.SAFE, "charm", cost={"money": 3}, adv=True),
          C("Rush past all at once", ["fight", "noise"], Risk.WILD, "brawn", adv=True)]),
        ("The Heist Plan",
         "In {loc}, {mac} is in sight — guarded, alarmed, and surrounded by a very small moat.".format(**ctx),
         [C("Elaborate heist with roles", ["steal", "distract"], Risk.RISKY, "sneak", adv=True),
          C("One person distracts, everyone else grabs", ["steal", "distract", "noise"], Risk.WILD, "charm", adv=True),
          C("Wait for a better moment", ["hide", "rest"], Risk.SAFE, "brains", cost={"time": 1})]),
        ("The Resource Crisis",
         "Camped in {loc}, you realize the supplies are a mess. Someone has to decide what to sacrifice.".format(**ctx),
         [C("Forage for food", ["explore"], Risk.RISKY, "sneak"),
          C("Repair the vehicle", ["build"], Risk.RISKY, "brains", cost={"supplies": 1}),
          C("Throw a morale-boosting party", ["perform", "rest"], Risk.SAFE, "charm", cost={"food": 2})]),
        ("The Chase",
         "{threat} has spotted you in {loc}. It's a chase now. Everyone is running in slightly different directions.".format(**ctx),
         [C("Split up and confuse them", ["flee", "distract"], Risk.RISKY, "sneak"),
          C("Take the vehicle", ["flee"], Risk.RISKY, "brawn", cost={"vehicle": 1}, adv=True),
          C("Stand your ground", ["fight"], Risk.WILD, "brawn", adv=True)]),
        ("The Moral Dilemma",
         "In {loc}, {npc} offers you {mac} — in exchange for leaving someone behind 'just for a little while'.".format(**ctx),
         [C("Take the deal", ["negotiate", "betray"], Risk.RISKY, "charm", adv=True),
          C("Refuse and walk away together", ["help"], Risk.SAFE, "charm"),
          C("Pretend to agree, then double-cross", ["negotiate", "steal"], Risk.WILD, "sneak", adv=True)]),
    ]


def final_choices(objective: Objective) -> list[Choice]:
    return [
        C("Charge straight at the problem", ["fight"], Risk.RISKY, "brawn", adv=True),
        C("Execute the clever plan", ["build", "investigate"], Risk.RISKY, "brains", adv=True),
        C("Talk everyone down", ["negotiate", "perform"], Risk.RISKY, "charm", adv=True),
        C(f"Grab {objective.macguffin} and run", ["steal", "flee"], Risk.WILD, "sneak", adv=True),
        C("Do something nobody will understand", ["perform", "distract"], Risk.WILD, "weird", adv=True),
        C("Protect your friends", ["help"], Risk.SAFE, "brawn"),
    ]


# ---------------------------------------------------------------------------
# Narration
# ---------------------------------------------------------------------------

TIER_OPENERS = {
    "catastrophic_success": ["It works. It works far too well.", "Technically, this is a success. Technically.",
                             "The plan succeeds, and then keeps succeeding, well past the point of usefulness."],
    "unexpected_success": ["Against every law of probability, it works.", "Nobody, least of all {name}, expected this to work.",
                           "In a twist that will be retold for years, it works perfectly."],
    "success": ["It works.", "Clean, quick, and only slightly embarrassing.", "{name} pulls it off with alarming confidence."],
    "partial_success": ["It mostly works.", "It works, in the sense that nothing is on fire. Yet.",
                        "Half of the plan works. The other half is still being discussed."],
    "complication": ["It does not go to plan.", "Something goes wrong in a new and creative way.",
                     "The universe, briefly, takes it personally."],
    "failure": ["It fails. Spectacularly.", "This is, unambiguously, a disaster.", "It goes about as well as everyone feared."],
}

TAG_FLAVOR = {
    "hide": "{name} becomes one with a potted plant",
    "investigate": "{name} pokes at the mystery with a pen they will later regret using",
    "lock": "{name} locks the door, double-checks it, then triple-checks it out loud",
    "warn": "{name} sprints off shouting a warning that is about 40% intelligible",
    "distract": "{name} creates a diversion involving jazz hands",
    "fight": "{name} attempts what can only charitably be called combat",
    "flee": "{name} runs with the urgency of someone who left the oven on",
    "steal": "{name} pockets the goods with the subtlety of a marching band",
    "negotiate": "{name} opens negotiations with a firm handshake and a weak argument",
    "build": "{name} builds something out of tape, hope, and a coat hanger",
    "destroy": "{name} destroys it, thoroughly and with no plan for what comes after",
    "help": "{name} rushes in to help",
    "explore": "{name} wanders off down a corridor nobody else noticed",
    "betray": "{name} quietly decides that the team is more of a suggestion",
    "perform": "{name} performs, with commitment that frightens everyone present",
    "climb": "{name} climbs, grunting like a dramatic tennis player",
    "cross": "{name} starts across, eyes closed, humming",
    "rest": "{name} takes a well-deserved and badly-timed nap",
    "noise": "{name} makes a noise heard three postcodes away",
}


PLURAL_VERBS = {"becomes": "become", "pokes": "poke", "locks": "lock", "sprints": "sprint", "creates": "create",
                "attempts": "attempt", "runs": "run", "pockets": "pocket", "opens": "open", "builds": "build",
                "destroys": "destroy", "rushes": "rush", "wanders": "wander", "quietly": "quietly", "performs": "perform",
                "climbs": "climb", "starts": "start", "takes": "take", "makes": "make", "improvises": "improvise",
                "decides": "decide", "is": "are", "was": "were", "has": "have", "comes": "come", "won't": "won't"}


def join_names(names: list[str]) -> str:
    names = [n for n in names if n]
    if len(names) <= 1:
        return names[0] if names else "someone"
    return ", ".join(names[:-1]) + " and " + names[-1]


def agree(text: str, plural: bool) -> str:
    """Make a third-person-singular template agree with a plural subject."""
    if not plural:
        return text
    words = text.split(" ")
    for i, w in enumerate(words):
        if w in PLURAL_VERBS:
            words[i] = PLURAL_VERBS[w]
            if w == "quietly" and i + 1 < len(words) and words[i + 1] in PLURAL_VERBS:
                words[i + 1] = PLURAL_VERBS[words[i + 1]]
            break
    return " ".join(words)


_FIRST_PERSON = [
    (r"\bI am\b", "{n} is"), (r"\bI'm\b", "{n} is"), (r"\bI've\b", "{n} has"), (r"\bI'll\b", "{n} will"),
    (r"\bI\b", "{n}"), (r"\bmyself\b", "themselves"), (r"\bmy\b", "their"), (r"\bMy\b", "Their"),
    (r"\bme\b", "them"), (r"\bmine\b", "theirs"),
    (r"\byourself\b", "themselves"), (r"\byour\b", "their"), (r"\bYour\b", "Their"), (r"\byou\b", "them"),
    (r"\bwe\b", "the group"), (r"\bWe\b", "The group"), (r"\bour\b", "their"), (r"\bus\b", "them"),
]
_VERB_FIX = {"convince": "convinces", "try": "tries", "go": "goes", "run": "runs", "steal": "steals", "take": "takes",
             "grab": "grabs", "use": "uses", "ask": "asks", "tell": "tells", "make": "makes", "burn": "burns",
             "betray": "betrays", "sneak": "sneaks", "hide": "hides", "attack": "attacks", "throw": "throws",
             "climb": "climbs", "sing": "sings", "dance": "dances", "bribe": "bribes", "build": "builds", "pretend": "pretends",
             "offer": "offers", "challenge": "challenges", "eat": "eats", "open": "opens", "follow": "follows", "do": "does",
             "have": "has", "give": "gives", "start": "starts", "distract": "distracts", "flee": "flees", "jump": "jumps"}


def third_person(text: str, name: str, plural: bool = False) -> str:
    """'I convince Greg to …' -> 'Sam convinces Greg to …' (best effort, for retelling player input)."""
    out = text.strip()
    if not re.search(r"\b(I|my|me|we|our|you|your|yourself)\b", out, re.I):
        return out
    for pat, rep in _FIRST_PERSON:
        out = re.sub(pat, rep.format(n=name), out)
    if not plural:
        out = re.sub(rf"\b{re.escape(name)} (\w+)\b", lambda m: f"{name} {_VERB_FIX.get(m.group(1), m.group(1))}", out)
    return out


_LOWERABLE = {"it", "the", "a", "an", "here", "what", "in", "unbeknownst", "something", "remember", "this", "which", "and",
              "only", "somewhere", "elsewhere", "everyone", "nobody", "things", "half", "clean", "use", "take", "build",
              "check", "follow", "hide", "climb", "investigate", "lock", "open", "run", "cross", "saw", "haggle", "pay",
              "pickpocket", "press", "rewire", "write", "feed", "shake", "ask", "create", "sneak", "introduce", "read",
              "set", "rush", "shout", "perform", "heckle", "give", "bribe", "elaborate", "one", "wait", "forage", "repair",
              "throw", "split", "stand", "refuse", "pretend", "charge", "execute", "talk", "grab", "do", "protect", "smash",
              "turn", "construct", "against", "technically", "while", "at", "across", "not", "meanwhile", "for", "with"}


def lower_first(text: str) -> str:
    """Lower-case the first word only if it's an ordinary word (never a name)."""
    if not text:
        return text
    first = re.split(r"[\s,:;—-]", text, maxsplit=1)[0].lower().strip("'\"")
    return text[:1].lower() + text[1:] if first in _LOWERABLE else text


def sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?\"'”)" else text + "."


def flavor_for(tags: list[str], name: str, plural: bool = False) -> str:
    for t in tags:
        if t in TAG_FLAVOR:
            return name + " " + agree(TAG_FLAVOR[t].removeprefix("{name} "), plural)
    return f"{name} {'improvise' if plural else 'improvises'}"


def narrate_action(seed: int, key: object, names: list[str] | str, action: str, tier: str, tags: list[str],
                   weakness: str = "", trait: str = "", interactions: list[str] | None = None,
                   consequences: list[str] | None = None) -> str:
    names = [names] if isinstance(names, str) else names
    name = join_names(names)
    plural = len(names) > 1
    template = rng.pick(seed, TIER_OPENERS[tier], "opener", key)
    opener = agree(template.format(name=name), plural) if "{name}" in template else template
    action_clean = third_person(action, names[0], plural).rstrip(".")
    parts = [f"{flavor_for(tags, name, plural)}: {action_clean[:1].lower() + action_clean[1:] if action_clean.split(' ')[0] != names[0] else action_clean}. {opener}"]
    parts.extend(sentence(i) for i in interactions or [])
    parts.extend(sentence(c) for c in consequences or [])
    if tier in ("failure", "complication") and weakness and rng.chance(seed, 0.5, "weak", key):
        parts.append(f"In fairness, {names[0]}'s weakness ({weakness.lower().rstrip('.')}) did not help.")
    elif trait and rng.chance(seed, 0.3, "trait", key):
        parts.append(f"Throughout, {names[0]} {trait.lower().rstrip('.')}.")
    return " ".join(p for p in parts if p)


SCENE_OPENERS = {
    "early_adventure": "The party assembles at {loc}. {obj} It is, everyone agrees, an extremely bad idea.",
    "first_major_decision": "Things move fast. In the confusion at {loc}, the party is scattered — each of you alone, each of you hearing something different.",
    "branching_paths": "The party has drifted into separate little expeditions. Paths cross, double back, and occasionally collapse.",
    "resource_pressure": "Supplies are thinning. Tempers are fraying. Somewhere, {threat} is getting closer.",
    "group_interaction": "The party regroups at {loc} — mostly. Stories don't quite line up. Nobody mentions the smell.",
    "escalation": "Everything is now significantly worse. {threat} knows you're here.",
    "plot_twist": "Then, without warning, the floor of reality shifts: nothing at {loc} is what it seemed.",
    "major_consequence": "Earlier decisions come home to roost at {loc}, carrying luggage.",
    "final_challenge": "This is it. {final}. Everything you did — everything you thought nobody saw — comes down to this moment.",
}

BEAT_TITLES = {
    "early_adventure": ["An Extremely Bad Idea", "Everyone Agrees, Briefly", "The Plan (Draft 1)"],
    "first_major_decision": ["Everyone Goes Their Separate Way", "Alone, Mostly", "Nobody Saw That"],
    "branching_paths": ["Paths Diverge, Loudly", "The Long Way Around", "Splinter Groups"],
    "resource_pressure": ["The Snack Situation", "Running on Fumes", "Budget Cuts"],
    "group_interaction": ["The Awkward Reunion", "Compare Notes", "Wait, You Did What?"],
    "escalation": ["Things Get Significantly Worse", "Escalation Station", "Now It's Personal"],
    "plot_twist": ["The Twist Nobody Ordered", "Wait, What?", "Reality Has Notes"],
    "major_consequence": ["The Bill Comes Due", "Consequences, Delivered", "Remember That Thing You Did?"],
    "final_challenge": ["The Final Disaster", "The Plan That Nobody Agreed On", "Last Call"],
}


def scene_text(seed: int, beat: str, round_no: int, ctx: dict[str, str]) -> tuple[str, str]:
    title = rng.pick(seed, BEAT_TITLES.get(beat, ["Meanwhile"]), "beat_title", round_no)
    text = SCENE_OPENERS.get(beat, "The adventure continues at {loc}.").format(**ctx)
    return title, text
