"""The Ridiculous Encounter Engine.

Encounters are the heartbeat of the adventure: an NPC barges into a scene with
a situation that is funny *before* anyone decides anything — two bodybuilders
arguing about how many days are in a week, a ghost worried about haunting
etiquette — and the players must commit to one of several ridiculous answers.

They are not joke cards bolted onto the side. An encounter *replaces* a
decision group's situation for that scene, so it flows through the same
authoritative machinery as everything else:

* answers are ordinary ``Choice``s with a stat, a risk and tags, so character
  stats, abilities, weaknesses, items, luck and cross-player interactions all
  apply, and the seeded dice decide how it goes;
* each answer carries a win effect and a setback effect that **only the engine
  applies** (reputation, NPC opinion, items, obligations that come due later,
  hidden consequences, new locations, honorary titles);
* the encounter NPC becomes a real NPC who remembers what you said and can
  come back several scenes later to bring it up (callbacks);
* everything is written to the Adventure Chronicle, so the final story
  remembers it.

The Encounter Director (``plan``) is rule-based and deterministic: it decides
whether a scene gets an encounter, who faces it and how they answer
(individually, as a group vote, or as a *secret council* where every solo
player answers the same encounter alone and their answers collide), which
category comes next, and whether an old NPC returns.

Content comes from an LLM ``EncounterGenerator`` when one is configured
(validated, moderated, checked for novelty) and otherwise from the original
library below. The library is also the fallback for any generation failure.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from ..models.game import (
    NPC,
    Choice,
    DecisionGroup,
    DelayedConsequence,
    Encounter,
    EncounterCategory,
    EncounterEffect,
    EncounterMode,
    EncounterOption,
    EncounterRecord,
    GameState,
    Item,
    Location,
    OutcomeTier,
    Player,
    Risk,
    Visibility,
)
from . import chronicle, director, rng
from .resolution import SUCCESSES, ActionOutcome, change_shared, describe_delta

# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------

FREQUENCY = {"off": 0.0, "occasional": 0.4, "normal": 0.75, "heartbeat": 1.0}
COUNCIL_CHANCE = 0.5  # when everyone is alone this scene, how often they all face the same encounter
CALLBACK_CHANCE = 0.4  # how often a past encounter NPC comes back instead of a new encounter
WIN_TIERS = SUCCESSES | {OutcomeTier.PARTIAL_SUCCESS}
RESOURCE_KEYS = ("food", "money", "time", "supplies", "morale", "reputation", "vehicle")

CATEGORY_INFO: dict[EncounterCategory, tuple[str, str]] = {
    EncounterCategory.SOCIAL: ("Ridiculous Social Situation", "🙃"),
    EncounterCategory.QUESTIONS: ("No Stupid Questions", "❓"),
    EncounterCategory.PETTY: ("Petty Revenge", "😤"),
    EncounterCategory.HYPOTHETICAL: ("Absurd Hypothetical", "🌀"),
    EncounterCategory.CHALLENGE: ("Ridiculous Challenge", "🏆"),
}

# Comedic mechanisms the generator is asked to build on (one per encounter).
MECHANISMS = [
    "escalation", "absurd contrast", "misunderstanding", "overconfidence", "awkward sincerity",
    "unexpected reversal", "deadpan delivery", "a petty grievance",
    "an inconsequential problem treated as a matter of national importance",
]

# Process-wide library performance, loaded from the database at start-up and
# updated as adventures are archived: key -> (plays, mean choice diversity).
# Encounters where everyone always picks the same "obvious" answer are drawn
# less often; encounters that split the table are drawn more.
LIBRARY_STATS: dict[str, tuple[int, float]] = {}


def note_library_stats(rows: Mapping[str, tuple[int, float]]) -> None:
    LIBRARY_STATS.update(rows)


def library_weight(key: str) -> float:
    plays, diversity = LIBRARY_STATS.get(key, (0, 0.6))
    if plays < 3:
        return 1.0  # not enough evidence yet
    return 0.4 + diversity


# ---------------------------------------------------------------------------
# The original encounter library
# ---------------------------------------------------------------------------
# Placeholders: {loc} location, {mac} macguffin, {threat} antagonist, {setting}.
# Every encounter is original writing. Every answer is entertaining, none is
# obviously correct, and each one leads somewhere different.


def _fx(**kw) -> EncounterEffect:
    if "obligation_cost" not in kw and kw.get("obligation"):
        kw["obligation_cost"] = {"reputation": -1}
    return EncounterEffect(**kw)


def _o(key: str, style: str, label_: str, teaser: str, tags: list[str], stat: str, risk: str, aftermath: str,
       win: dict, lose: dict, obj: bool = False) -> EncounterOption:
    return EncounterOption(key=key, style=style, label=label_, teaser=teaser, tags=tags, stat=stat, risk=Risk(risk),
                           aftermath=aftermath, win=_fx(**win), lose=_fx(**lose), advances_objective=obj)


@dataclass(frozen=True)
class Template:
    key: str
    category: EncounterCategory
    title: str
    intro: str
    question: str
    npc: tuple[str, str, str]  # name, emoji, personality
    options: tuple[EncounterOption, ...]


S, Q, P, H, C = (EncounterCategory.SOCIAL, EncounterCategory.QUESTIONS, EncounterCategory.PETTY,
                 EncounterCategory.HYPOTHETICAL, EncounterCategory.CHALLENGE)

LIBRARY: tuple[Template, ...] = (
    # -- Ridiculous social situations ---------------------------------------
    Template(
        "seven_day_crisis", S, "The Seven-Day Crisis",
        "Near {loc}, two enormous bodybuilders are having an increasingly heated argument. One insists a week has "
        "seven days. The other is certain it has eight, because leg day deserves its own day. Someone has brought a "
        "whiteboard. A third bodybuilder is doing long division with a protein shaker.",
        "They turn to you. Settle this immediately.",
        ("Brick Thundercalf", "💪", "settles every disagreement by flexing at it"),
        (
            _o("a", "The Diplomat", "Rule that both are correct if you count leg day twice",
               "Unexpected respect — but now you're responsible for the gym calendar.", ["negotiate"], "charm", "safe",
               "made you Keeper of the Gym Calendar and has urgent questions about Thursday",
               {"npc_disposition": 2, "resources": {"reputation": 1}, "title": "Keeper of the Gym Calendar"},
               {"npc_disposition": -1, "obligation": "the gym calendar you invented has double-booked leg day with an eclipse",
                "obligation_cost": {"time": -1}}),
            _o("b", "The Scientist", "Demand proof using a treadmill, a stopwatch and 47 protein bars",
               "A scientific breakthrough nobody understands.", ["investigate"], "brains", "risky",
               "is still on the treadmill, collecting data",
               {"resources": {"supplies": 1}, "item": "a peer-reviewed protein bar"},
               {"resources": {"food": -1}}),
            _o("c", "The Opportunist", "Announce there are nine days and charge admission to hear why",
               "Instant popularity, followed by an angry accounting department.", ["perform"], "charm", "risky",
               "is still waiting for day nine, and a refund",
               {"resources": {"money": 2}, "secret": "You kept the ticket money. Day Nine has been sold out for weeks."},
               {"resources": {"reputation": -1}, "obligation": "the accounting department has questions about Day Nine",
                "obligation_cost": {"money": -1}}),
            _o("d", "The Chaos Agent", "Challenge both of them to a push-up contest for calendar supremacy",
               "The argument becomes significantly more athletic.", ["perform", "noise"], "brawn", "wild",
               "has been doing push-ups ever since, out of spite",
               {"resources": {"morale": 2}, "npc_disposition": 1},
               {"resources": {"morale": -1, "reputation": -1}}),
        ),
    ),
    Template(
        "queue_for_the_queue", S, "The Line About the Line",
        "At {loc}, a line has formed. Nobody knows what it is for. A second line has formed to complain about the "
        "first one. At the very front, a woman with a folding chair and thirty years of queueing experience is "
        "inspecting newcomers.",
        "She narrows her eyes. \"Which line,\" she asks, \"are YOU in?\"",
        ("Marjorie Linewright", "🧍", "has queued professionally for thirty years and has never once been served"),
        (
            _o("a", "The Bureaucrat", "Produce a numbered ticket you just drew on a napkin",
               "Instant legitimacy, as long as nobody looks closely.", ["distract"], "sneak", "risky",
               "framed your napkin ticket and calls it 'the founding document'",
               {"resources": {"time": 1}, "title": "Holder of Ticket Number One"},
               {"resources": {"reputation": -1}}),
            _o("b", "The Philosopher", "Ask what the line is for. Out loud. In front of everyone.",
               "The truth — and the collapse of a social order.", ["investigate"], "brains", "risky",
               "has been looking for a new line ever since you ruined the last one",
               {"npc_disposition": 1, "secret": "The line was for gossip about {mac}. You heard it first."},
               {"npc_disposition": -2, "resources": {"morale": -1}}, obj=True),
            _o("c", "The Entrepreneur", "Sell places in the line to the people behind you",
               "Profit, and a very organised mob.", ["negotiate"], "charm", "risky",
               "has unionised the queue and lists you as 'management'",
               {"resources": {"money": 2}},
               {"resources": {"reputation": -2}}),
            _o("d", "The Chaos Agent", "Start a third, faster, much more exciting line",
               "Everyone joins. It goes nowhere. Quickly.", ["perform", "noise"], "weird", "wild",
               "defected to your line and has been standing in it ever since",
               {"resources": {"morale": 2}, "location": "The Third Line (now a minor tourist attraction)"},
               {"resources": {"time": -1}}),
        ),
    ),
    Template(
        "bench_custody", S, "The Great Bench Custody Battle",
        "Two elderly twins at {loc} are locked in a custody battle over a public bench. Both have brought paperwork. "
        "One has brought a lawyer. The lawyer is a seagull.",
        "They have agreed, reluctantly, that you will decide who gets the bench.",
        ("Harold and Edna Pemberton-Vale", "🪑", "twins who have shared one bench for sixty years, badly"),
        (
            _o("a", "The Solomon", "Propose sawing the bench exactly in half",
               "Ancient wisdom, or an extremely short bench.", ["negotiate"], "brains", "risky",
               "now each own exactly half a bench and are thrilled about it",
               {"npc_disposition": 2, "resources": {"reputation": 1}},
               {"npc_disposition": -1, "resources": {"supplies": -1}}),
            _o("b", "The Prosecutor", "Cross-examine the seagull",
               "The seagull knows more than it lets on.", ["investigate"], "charm", "risky",
               "retained the seagull full-time on your recommendation",
               {"npc_disposition": 1, "secret": "The seagull, under oath, squawked something very useful about {mac}."},
               {"resources": {"food": -1}}, obj=True),
            _o("c", "The Diplomat", "Draft a 47-page bench-sharing agreement",
               "Peace, paperwork, and possibly a new hobby.", ["build"], "brains", "safe",
               "laminated the agreement and keeps adding amendments",
               {"npc_disposition": 1, "item": "a 47-page bench-sharing agreement"},
               {"resources": {"time": -1}}),
            _o("d", "The Chaos Agent", "Sit on the bench yourself and refuse to move",
               "Two new enemies. Excellent posture.", ["hide"], "weird", "wild",
               "still talk about the day a stranger annexed their bench",
               {"resources": {"morale": 1}, "title": "Sovereign of the Bench"},
               {"npc_disposition": -2, "resources": {"reputation": -1}}),
        ),
    ),
    Template(
        "compliment_war", S, "The Compliment War",
        "Two rival bakers at {loc} are competing to compliment you more. It started with 'nice shoes'. It has "
        "escalated to 'your elbows have the quiet dignity of a cathedral'. Refusing a compliment is considered an "
        "act of war.",
        "It is your turn to say something nice back.",
        ("Crumbsworth and Doughty", "🥐", "rival bakers locked in a war of escalating politeness"),
        (
            _o("a", "The Poet", "Compliment both bakeries in rhyming couplets",
               "Free pastries — or an extremely long poem in reply.", ["perform"], "charm", "risky",
               "have been sending you rhyming pastries ever since",
               {"resources": {"food": 2}},
               {"resources": {"time": -1}}),
            _o("b", "The Overachiever", "Compliment them back so hard they cry",
               "Total victory. Tears. Possibly yours.", ["negotiate"], "charm", "wild",
               "framed your compliment and hung it above both ovens",
               {"npc_disposition": 2, "resources": {"morale": 1}},
               {"npc_disposition": -1, "resources": {"morale": -1}}),
            _o("c", "The Scientist", "Ask them to rate your elbows on a scale of one to ten",
               "Honest feedback you did not want.", ["investigate"], "brains", "safe",
               "now rate everyone's elbows, and blame you for the trend",
               {"secret": "Under the flour, one baker let slip where {threat} buys its bread."},
               {"resources": {"morale": -1}}, obj=True),
            _o("d", "The Escape Artist", "Say 'you too' and leave very quickly",
               "Freedom. The bakers will remember this.", ["flee"], "sneak", "safe",
               "still bring up the time you said 'you too' and fled",
               {"resources": {"time": 1}},
               {"resources": {"reputation": -1}, "obligation": "the bakers have written a strongly worded note about your rudeness"}),
        ),
    ),
    # -- No stupid questions ---------------------------------------------------
    Template(
        "vacuum_cleaner_cleaner", Q, "The Village Philosopher",
        "A nervous resident of {loc} approaches the party, clutching a feather duster for emotional support.",
        "\"I have a question that has bothered me for years. If you clean a vacuum cleaner, does that make you a "
        "vacuum cleaner cleaner?\" He looks genuinely concerned.",
        ("Ignatius Fluff", "🧹", "a nervous man who has worried about cleaning-related grammar for years"),
        (
            _o("a", "The Logician", "Explain the infinite chain of cleaners cleaning cleaners",
               "He will understand. That's the problem.", ["investigate"], "brains", "risky",
               "hired a vacuum cleaner cleaner cleaner and can't stop",
               {"npc_disposition": 1, "title": "Official Cleaning Department of the Realm"},
               {"resources": {"morale": -1}}),
            _o("b", "The Legal Department", "Declare it classified until he consults a licensed vacuum attorney",
               "Instant relief. A legal bill arrives later.", ["distract"], "charm", "safe",
               "found a vacuum attorney and is now suing the dust",
               {"npc_disposition": 1},
               {"obligation": "a licensed vacuum attorney has sent you the invoice", "obligation_cost": {"money": -1}}),
            _o("c", "The Pragmatist", "Suggest he buy a second vacuum to clean the first one",
               "Problem solved, forever, twice.", ["negotiate"], "charm", "safe",
               "now owns eleven vacuums, each one cleaning the last",
               {"item": "a slightly used vacuum cleaner"},
               {"resources": {"money": -1}}),
            _o("d", "The Mystic", "Ask whether a vacuum can become emotionally attached to its dust",
               "A spiritual awakening — for someone.", ["perform"], "weird", "wild",
               "started a support group for vacuums going through dust separation",
               {"resources": {"morale": 2}, "npc_disposition": 2},
               {"npc_disposition": -1, "resources": {"morale": -1}}),
        ),
    ),
    Template(
        "polite_haunting", Q, "A Question of Haunting Etiquette",
        "A ghost drifts through a wall at {loc}, apologising profusely to the wall.",
        "\"Forgive me. Is it rude to haunt someone who is already asleep? I don't want to be a bother.\"",
        ("Lady Wisp of the Third Floor", "👻", "a ghost with impeccable manners and terrible timing"),
        (
            _o("a", "The Etiquette Coach", "Write her a formal haunting schedule, with tea breaks",
               "A punctual ghost. Possibly an ally.", ["build"], "brains", "safe",
               "now haunts strictly by appointment and sends reminders",
               {"npc_disposition": 2, "item": "a ghost-signed haunting timetable"},
               {"resources": {"time": -1}}),
            _o("b", "The Strategist", "Suggest she haunt {threat} instead",
               "A spectral ally against the enemy — if she agrees.", ["negotiate"], "charm", "risky",
               "has been haunting {threat} night and day, very politely",
               {"npc_disposition": 1, "secret": "The ghost agreed to haunt {threat}. She will report back."},
               {"npc_disposition": -1, "obligation": "the ghost decided to practise haunting on YOU first",
                "obligation_cost": {"morale": -1}}, obj=True),
            _o("c", "The Brutally Honest", "Tell her yes, it's extremely rude, and she should be ashamed",
               "Honesty. Ghosts hold grudges.", ["negotiate"], "charm", "wild",
               "has been working on her manners and wants you to grade her progress",
               {"npc_disposition": 1, "resources": {"morale": 1}},
               {"npc_disposition": -2}),
            _o("d", "The Volunteer", "Offer to be haunted as a test subject",
               "A terrible night's sleep, for science.", ["rest"], "weird", "risky",
               "considers you her favourite person to haunt",
               {"resources": {"morale": 1}, "title": "First Officially Haunted Volunteer"},
               {"resources": {"morale": -1}}),
        ),
    ),
    Template(
        "does_the_horse_know", Q, "Does the Horse Know?",
        "A knight in slightly rusty armour blocks the road near {loc}. His horse is listening intently.",
        "\"Does my horse know it's a horse? I've been too afraid to ask him.\"",
        ("Sir Reginald the Perpetually Unsure", "🐴", "a knight who has never once been sure of anything"),
        (
            _o("a", "The Journalist", "Ask the horse directly",
               "The horse may answer. You may not like it.", ["investigate"], "charm", "risky",
               "now consults the horse on all decisions, and credits you",
               {"npc_disposition": 1, "secret": "Through a series of meaningful snorts, the horse told you something about {mac}."},
               {"resources": {"reputation": -1}}, obj=True),
            _o("b", "The Therapist", "Assure him the horse knows, and is very proud of it",
               "A relieved knight. A smug horse.", ["negotiate"], "charm", "safe",
               "tells everyone his horse is 'very self-aware', thanks to you",
               {"npc_disposition": 2, "item": "a horseshoe of eternal gratitude"},
               {"npc_disposition": -1}),
            _o("c", "The Agitator", "Suggest the horse might think it's a very large dog",
               "An identity crisis. Possibly two.", ["perform"], "weird", "wild",
               "has been throwing sticks for the horse, who brings them back",
               {"resources": {"morale": 2}},
               {"npc_disposition": -2, "obligation": "the horse now fetches things, mostly your supplies",
                "obligation_cost": {"supplies": -1}}),
            _o("d", "The Trader", "Offer to swap horses, to find out scientifically",
               "A new ride, of sorts.", ["negotiate"], "charm", "risky",
               "insists you still have custody of the horse on weekends",
               {"resources": {"vehicle": 2}},
               {"resources": {"vehicle": -1}}),
        ),
    ),
    Template(
        "weekly_birthday", Q, "The Weekly Birthday Question",
        "A very small king on an enormous throne at {loc} summons the party with a trumpet that is mostly decorative.",
        "\"Can I legally declare my birthday a national holiday every week? My advisers said 'technically'.\"",
        ("King Barnaby the Fairly Small", "👑", "a very small king with very large ambitions"),
        (
            _o("a", "The Royal Lawyer", "Draft a decree making every Tuesday his birthday",
               "Royal favour. A kingdom without Tuesdays.", ["build"], "brains", "risky",
               "has issued forty-seven birthday decrees since, citing you as precedent",
               {"resources": {"reputation": 2}, "title": "Royal Birthday Lawyer"},
               {"resources": {"time": -1}}),
            _o("b", "The Flatterer", "Suggest he deserves TWO birthdays, like a proper monarch",
               "Cake. A lot of cake.", ["negotiate"], "charm", "safe",
               "now has two birthdays and expects two presents from you",
               {"npc_disposition": 2, "resources": {"food": 1}},
               {"npc_disposition": -1}),
            _o("c", "The Party Animal", "Throw him a surprise party immediately",
               "Joy, confetti, and no food left anywhere.", ["perform", "noise"], "charm", "wild",
               "demands a surprise party every week, with advance notice",
               {"resources": {"morale": 2}, "npc_disposition": 1},
               {"resources": {"food": -2}}),
            _o("d", "The Realist", "Gently explain how calendars work",
               "Wisdom. Or a dungeon.", ["investigate"], "brains", "risky",
               "is still working out calendars and sends you questions by pigeon",
               {"npc_disposition": 1, "secret": "Grateful for your honesty, the king whispered what he knows about {threat}."},
               {"npc_disposition": -2, "obligation": "the king has scheduled your arrest for his next birthday (Tuesday)"}, obj=True),
        ),
    ),
    # -- Petty revenge & minor grievances -------------------------------------
    Template(
        "missing_sandwich", P, "The Case of the Missing Sandwich",
        "For the third time this adventure, someone has stolen your lunch. You finally catch the culprit at {loc}: a "
        "smug cafeteria worker who claims that all unattended sandwiches are technically community property.",
        "You have exactly one opportunity to teach him a lesson.",
        ("Gustavo Crumb", "🥪", "a smug cafeteria worker who believes unattended food belongs to everyone"),
        (
            _o("a", "The Psychological Mastermind", "Leave a note saying the sandwich was a classified royal experiment",
               "Paranoia. Delicious, delicious paranoia.", ["distract"], "sneak", "risky",
               "still checks every sandwich for royal seals",
               {"resources": {"morale": 1}, "secret": "Gustavo now believes he is a royal test subject and reports to you weekly."},
               {"obligation": "Gustavo reported the 'royal experiment' to the actual royals"}),
            _o("b", "The Culinary Strategist", "Offer him a sandwich with a harmless magic flavour that makes his tongue speak French",
               "Justice, with a light vinaigrette.", ["perform"], "weird", "wild",
               "now orders everything 'avec plaisir' and blames you",
               {"resources": {"morale": 2}, "title": "Sandwich Avenger"},
               {"resources": {"food": -1}}),
            _o("c", "The Legal Department", "Convene a formal trial with a tiny wig and a jury of pigeons",
               "Due process. Feathers everywhere.", ["negotiate"], "charm", "risky",
               "appealed the pigeon verdict and is still waiting for a hearing",
               {"resources": {"reputation": 1}, "location": "The Pigeon Court"},
               {"resources": {"time": -1}}),
            _o("d", "The Diplomat", "Offer to share — if he signs a 47-page sandwich treaty",
               "Peace in our time. And a lot of paperwork.", ["negotiate"], "brains", "safe",
               "has honoured the sandwich treaty to the letter, including the footnotes",
               {"npc_disposition": 2, "resources": {"food": 1}, "item": "a signed sandwich treaty"},
               {"resources": {"food": -1}}),
        ),
    ),
    Template(
        "sock_reservations", P, "The Sock Reservation Scandal",
        "Every seat at {loc} has been reserved with a single sock. All of the socks belong to one woman, who is sitting "
        "nowhere and watching everyone.",
        "She has noticed you looking at a sock.",
        ("Prudence Holdsworth", "🧦", "reserves seats with socks and defends them like national territory"),
        (
            _o("a", "The Bureaucrat", "Demand to see her sock-reservation permit",
               "Seats for everyone — or a counter-complaint.", ["negotiate"], "brains", "risky",
               "applied for an official sock-reservation permit and listed you as a reference",
               {"resources": {"time": 1, "reputation": 1}},
               {"npc_disposition": -2, "obligation": "Prudence filed a formal counter-complaint about your socks"}),
            _o("b", "The Saboteur", "Quietly swap every sock for a mismatched one",
               "Chaos she won't notice until laundry day.", ["steal"], "sneak", "wild",
               "has never found a matching pair again, and suspects you",
               {"resources": {"morale": 1}, "item": "a bag of extremely mismatched socks"},
               {"resources": {"reputation": -2}}),
            _o("c", "The Peacemaker", "Reserve her a seat with YOUR sock, as a gesture of trust",
               "A truce, at the cost of one sock.", ["negotiate"], "charm", "safe",
               "keeps your sock in a glass case as a symbol of peace",
               {"npc_disposition": 2, "resources": {"morale": 1}},
               {"resources": {"supplies": -1}}),
            _o("d", "The Escalator", "Reserve the entire building with a single glove",
               "A cold war between socks and gloves.", ["perform"], "weird", "wild",
               "has declared a cold war between socks and gloves",
               {"resources": {"morale": 1}, "location": "The Glove Annex (reserved)"},
               {"resources": {"time": -1}, "npc_disposition": -1}),
        ),
    ),
    Template(
        "umbrella_heist", P, "The Umbrella Doctrine",
        "Someone at {loc} has taken your umbrella. He is standing under it, dry and smug, explaining that umbrellas "
        "become public property the moment it starts raining.",
        "It is still raining. On you, specifically.",
        ("Sir Drizzleton", "☂️", "believes all umbrellas are communal during rain, especially yours"),
        (
            _o("a", "The Philosopher", "Argue that YOU are also public property during rain, and stand under it with him",
               "Shelter, and an extremely awkward silence.", ["negotiate"], "charm", "risky",
               "now shares every umbrella with strangers, which has made him wildly unpopular",
               {"npc_disposition": 1, "resources": {"morale": 1}},
               {"resources": {"morale": -1}}),
            _o("b", "The Engineer", "Find a bigger umbrella and hold it over his umbrella",
               "Dominance, through canopy.", ["build"], "brains", "risky",
               "has been searching for an umbrella bigger than yours ever since",
               {"item": "an industrial-sized umbrella"},
               {"resources": {"supplies": -1}}),
            _o("c", "The Patient One", "Wait for the rain to stop, then reclaim it as private property",
               "Legally flawless. Very damp.", ["rest"], "brains", "safe",
               "now carries a barometer to check exactly when umbrellas change owners",
               {"npc_disposition": 1, "resources": {"morale": 1}},
               {"resources": {"time": -1}}),
            _o("d", "The Mastermind", "Stage an elaborate umbrella heist with a decoy and a getaway puddle",
               "Your umbrella back, in style. Witnesses, possibly.", ["steal", "distract"], "sneak", "wild",
               "still tells the story of the Great Umbrella Heist, incorrectly",
               {"resources": {"morale": 2}, "title": "The Umbrella Bandit"},
               {"resources": {"reputation": -1}, "obligation": "the umbrella shop 'recognised your work' and wants a word",
                "obligation_cost": {"money": -1}}),
        ),
    ),
    # -- Absurd hypotheticals --------------------------------------------------
    Template(
        "discount_genie", H, "The Discount Genie",
        "A genie bursts out of a lamp at {loc}, coughing. He grants exactly one wish — but, due to budget cuts, only "
        "for things costing under three dollars.",
        "\"Choose wisely. Or cheaply. Ideally both.\"",
        ("Derek the Discount Djinn", "🧞", "a genie whose lamp was bought on clearance"),
        (
            _o("a", "The Loophole Hunter", "Wish for three dollars",
               "The loophole of the century.", ["investigate"], "brains", "risky",
               "closed the three-dollar loophole and named the new rule after you",
               {"resources": {"money": 2}},
               {"npc_disposition": -1}),
            _o("b", "The Snacker", "Wish for an endless supply of snacks (budget edition)",
               "Food forever. Small food. Mostly crumbs.", ["negotiate"], "charm", "safe",
               "has been sending you crumbs ever since, exactly as agreed",
               {"resources": {"food": 2}},
               {"resources": {"morale": -1}}),
            _o("c", "The Selfless One", "Wish for the genie to get a nicer lamp",
               "A grateful genie. Grateful genies talk.", ["help"], "charm", "risky",
               "lives in a slightly nicer lamp and owes you a favour",
               {"npc_disposition": 2, "secret": "Deeply moved, Derek told you a cheap trick for dealing with {threat}."},
               {"npc_disposition": -1}, obj=True),
            _o("d", "The Gambler", "Wish for 'whatever's in the lost-and-found'",
               "Anything. Literally anything.", ["explore"], "weird", "wild",
               "opened a lost-and-found business and lists you as a loyal customer",
               {"resources": {"morale": 1}, "item": "a mysterious object from the lost-and-found"},
               {"resources": {"supplies": -1}}),
        ),
    ),
    Template(
        "optional_gravity", H, "Gravity Is Optional on Tuesdays",
        "An official at {loc} announces that, effective immediately, gravity is optional on Tuesdays. He needs "
        "someone to write the regulations.",
        "\"It is currently Tuesday,\" he adds, floating slightly. \"Please hurry.\"",
        ("Commissioner Pembrook", "📋", "a minor official responsible for physics paperwork"),
        (
            _o("a", "The Bureaucrat", "Write a sensible twelve-point gravity policy",
               "Order, structure, and a fancy title.", ["build"], "brains", "safe",
               "enforces your gravity policy so strictly that birds now need permits",
               {"resources": {"reputation": 1}, "title": "Minister of Gravity (Tuesdays)"},
               {"resources": {"time": -1}}),
            _o("b", "The Opportunist", "Float up to the high shelves and grab everything useful",
               "Supplies from places nobody could reach.", ["steal", "climb"], "sneak", "risky",
               "is still trying to get things down from the ceiling",
               {"resources": {"supplies": 2}},
               {"obligation": "everything you took has to come back down when gravity returns",
                "obligation_cost": {"supplies": -1}}, obj=True),
            _o("c", "The Safety Officer", "Recommend gravity stays ON, with optional weekends",
               "Sensible. Suspiciously sensible.", ["negotiate"], "charm", "risky",
               "now sells 'gravity-free weekends' as a premium service",
               {"npc_disposition": 1, "resources": {"morale": 1}},
               {"npc_disposition": -1}),
            _o("d", "The Chaos Agent", "Declare gravity optional every single day",
               "Freedom! Also, everything floats away.", ["perform"], "weird", "wild",
               "has never recovered from the week everything floated",
               {"resources": {"morale": 2}, "location": "The Floating Market (gravity optional)"},
               {"resources": {"vehicle": -1}}),
        ),
    ),
    Template(
        "goose_voice_curse", H, "The Goose Voice Clause",
        "A fortune teller at {loc} informs you, apologetically, that a minor curse is now in effect: the next person "
        "to speak will swap voices with the nearest goose.",
        "There is a goose. It is very close. Everyone is looking at you.",
        ("Madame Honksworth", "🪿", "a fortune teller whose curses are mostly administrative"),
        (
            _o("a", "The Silent Performer", "Communicate only through interpretive dance from now on",
               "Elegant. Confusing. Effective?", ["perform"], "charm", "risky",
               "now recommends interpretive dance as an approved curse workaround",
               {"npc_disposition": 1, "resources": {"morale": 1}},
               {"resources": {"reputation": -1}}),
            _o("b", "The Martyr", "Speak first and take the goose voice for the team",
               "Heroism, with a honk.", ["help"], "weird", "wild",
               "reports the goose has been using your voice to order snacks",
               {"resources": {"morale": 2}, "title": "Honorary Goose"},
               {"obligation": "the goose, now with your voice, has been ordering things on your account",
                "obligation_cost": {"money": -1}}),
            _o("c", "The Lawyer", "Ask to read the curse's terms and conditions",
               "The fine print always has something.", ["investigate"], "brains", "safe",
               "rewrote all her curses with clearer terms and conditions",
               {"npc_disposition": 1, "secret": "Clause 14 of the curse mentions {mac} by name. Interesting."},
               {"resources": {"time": -1}}, obj=True),
            _o("d", "The Survivor", "Make absolutely sure someone ELSE speaks first",
               "You'll be fine. A friend might honk.", ["distract", "noise"], "sneak", "wild",
               "still asks after whoever ended up with the goose voice",
               {"resources": {"morale": 1}, "secret": "A teammate now honks. They don't know it was you."},
               {"resources": {"reputation": -1}}),
        ),
    ),
    # -- Ridiculous challenges ---------------------------------------------------
    Template(
        "mime_directions", C, "Directions from a Mime",
        "You need directions. The only person at {loc} who knows the way is a mime who refuses to speak, write, or "
        "point in any normal fashion.",
        "He is pretending to be trapped in a box. He looks expectant.",
        ("Marcel the Mime", "🤐", "a mime with a vow of silence and a separate vow of pettiness"),
        (
            _o("a", "The Mimic", "Mime back, perfectly, until he respects you",
               "Mutual respect — or you're both in boxes now.", ["perform"], "charm", "risky",
               "now trains other mimes using your technique",
               {"npc_disposition": 2, "secret": "Through an elaborate mime involving a ladder and a duck, Marcel showed you a shortcut toward {mac}."},
               {"resources": {"morale": -1}}, obj=True),
            _o("b", "The Game Host", "Turn it into a game of charades with the whole party",
               "Teamwork, shouting, and eventually directions.", ["help"], "charm", "safe",
               "hosts a weekly charades night and still holds the trophy",
               {"resources": {"time": 1}},
               {"resources": {"time": -1}}, obj=True),
            _o("c", "The Briber", "Offer him an invisible sandwich",
               "He might be insulted. He might be hungry.", ["negotiate"], "weird", "risky",
               "is still eating the invisible sandwich, very slowly, out of respect",
               {"npc_disposition": 1, "resources": {"time": 1}},
               {"npc_disposition": -1}),
            _o("d", "The Liberator", "Break him out of the invisible box",
               "Freedom! Unless the box was load-bearing.", ["help", "noise"], "brawn", "wild",
               "has been trapped in a slightly larger invisible box ever since",
               {"npc_disposition": 2, "resources": {"morale": 1}},
               {"resources": {"reputation": -1}, "obligation": "the invisible box, it turns out, was load-bearing",
                "obligation_cost": {"supplies": -1}}),
        ),
    ),
    Template(
        "spoon_bridge", C, "The Spoon Bridge Ordinance",
        "A puddle is blocking the way at {loc}. A construction foreman insists it may only be crossed by a bridge made "
        "entirely of spoons. A crowd has gathered. Someone is selling popcorn.",
        "You have a lot of spoons and very little time.",
        ("Foreman Ladle", "🥄", "a construction foreman with strong, specific opinions about cutlery"),
        (
            _o("a", "The Engineer", "Design a structurally sound spoon arch",
               "A landmark — or a heap of spoons.", ["build"], "brains", "risky",
               "registered your spoon bridge as a historic landmark",
               {"resources": {"reputation": 1}, "location": "The Spoon Bridge", "title": "Spoon Engineer"},
               {"resources": {"supplies": -1}}, obj=True),
            _o("b", "The Showman", "Sell tickets to watch you fail",
               "You win either way. Probably.", ["perform"], "charm", "risky",
               "still sells tickets to the spot where you failed",
               {"resources": {"money": 2}},
               {"resources": {"reputation": -1}}),
            _o("c", "The Minimalist", "Step over the puddle",
               "Technically allowed. Spiritually controversial.", ["cross"], "brawn", "safe",
               "filed a formal complaint about the step, which is now illegal",
               {"resources": {"time": 1}, "npc_disposition": -1},
               {"resources": {"morale": -1}}),
            _o("d", "The Rebel", "Build a fork bridge instead, to make a statement",
               "A statement. Pointy.", ["build", "noise"], "weird", "wild",
               "considers you the greatest rebel in the history of cutlery",
               {"resources": {"morale": 2}},
               {"npc_disposition": -2}),
        ),
    ),
    Template(
        "pancake_honesty", C, "The Emotionally Honest Pancake",
        "You have been entered into a pancake contest at {loc} without your consent. The judge does not care about "
        "taste. Only emotional honesty.",
        "Your pancake is ready. The judge leans in and asks how it feels.",
        ("Judge Flapjack Vanderwhisk", "🥞", "a contest judge who scores pancakes purely on emotional honesty"),
        (
            _o("a", "The Method Actor", "Describe your pancake's tragic backstory with total sincerity",
               "Tears, applause, and a prize.", ["perform"], "charm", "risky",
               "still cries whenever someone mentions your pancake",
               {"resources": {"food": 2}, "title": "Most Emotionally Honest Pancake"},
               {"resources": {"morale": -1}}),
            _o("b", "The Researcher", "Present peer-reviewed data on your pancake's feelings",
               "Science! Also, a suspicious discovery.", ["investigate"], "brains", "risky",
               "now demands citations from every pancake",
               {"resources": {"reputation": 1}, "secret": "The judge's scoresheet has a doodle of {threat} in the margin. Suspicious."},
               {"resources": {"time": -1}}, obj=True),
            _o("c", "The Whistleblower", "Accuse another contestant's pancake of faking it",
               "The prize money — or a scandal.", ["negotiate"], "sneak", "wild",
               "launched an inquiry into pancake fraud and named you lead witness",
               {"resources": {"money": 2}},
               {"resources": {"reputation": -2}}),
            _o("d", "The Realist", "Admit the pancake feels nothing. It is a pancake.",
               "Refreshing honesty, or a disqualification.", ["negotiate"], "brains", "safe",
               "was so moved by your honesty that they quit judging to become a pancake chef",
               {"npc_disposition": 2},
               {"npc_disposition": -1}),
        ),
    ),
)

LIBRARY_BY_KEY = {t.key: t for t in LIBRARY}

# Returning NPCs: generic answers to "we need to talk about what you did".
CALLBACK_OPTIONS = (
    _o("a", "The Proud One", "Own it completely and demand credit",
       "Respect, or a much longer grudge.", ["perform"], "charm", "risky",
       "commissioned a small statue of the moment you owned it",
       {"npc_disposition": 1, "resources": {"reputation": 1}},
       {"npc_disposition": -1, "resources": {"reputation": -1}}),
    _o("b", "The Amnesiac", "Insist you have never met before",
       "A clean escape, if you can keep a straight face.", ["hide"], "sneak", "safe",
       "keeps a scrapbook to prove that you met",
       {"resources": {"time": 1}},
       {"npc_disposition": -2}),
    _o("c", "The Escalator", "Double down and make it twice as ridiculous",
       "Legendary. Possibly expensive.", ["perform", "noise"], "weird", "wild",
       "is now twice as committed to the bit as you are",
       {"resources": {"morale": 2}},
       {"resources": {"morale": -1}, "obligation": "the escalation has escalated further without you"}),
    _o("d", "The Delegator", "Make it somebody else's problem by pointing at a teammate",
       "Freedom, at a teammate's expense.", ["distract"], "charm", "risky",
       "now blames everything on whoever you pointed at",
       {"npc_disposition": 1, "secret": "You successfully blamed a teammate. They don't know yet."},
       {"resources": {"reputation": -1}}),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return {"loc": "here", "mac": "the thing", "threat": "the enemy", "setting": "the world"}.get(key, "something")


def fill(text: str, ctx: Mapping[str, str]) -> str:
    try:
        return text.format_map(_Safe(ctx))
    except (ValueError, IndexError):
        return text


def _clamp(v: int, lo: int = -2, hi: int = 2) -> int:
    return max(lo, min(hi, int(v)))


def category_label(cat: EncounterCategory) -> str:
    return CATEGORY_INFO[cat][0]


def category_emoji(cat: EncounterCategory) -> str:
    return CATEGORY_INFO[cat][1]


def _join(names: list[str]) -> str:
    from .procgen import join_names

    return join_names(names)


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def diversity(selections: Mapping[str, str], options: int | None = None) -> tuple[int, float]:
    """(distinct answers, normalised entropy). 0.0 = everyone agreed; 1.0 = answers spread as evenly as possible."""
    # every improvised answer is its own answer: two players who both went off-script did not agree
    picks = [f"freeform:{pid}" if v == "freeform" else v.removeprefix("auto:") for pid, v in selections.items()]
    counts = Counter(picks)
    total = len(picks)
    ceiling = min(total, options or total)
    if total < 2 or len(counts) < 2 or ceiling < 2:
        return len(counts), 0.0
    h = -sum((c / total) * math.log(c / total) for c in counts.values())
    return len(counts), round(min(1.0, h / math.log(ceiling)), 3)


# ---------------------------------------------------------------------------
# The Encounter Director
# ---------------------------------------------------------------------------


@dataclass
class EncounterPlan:
    group_indexes: list[int]
    mode: EncounterMode
    category: EncounterCategory
    mechanism: str
    template_key: str  # library pick: the content when no generator runs, and the fallback when one fails
    callback: EncounterRecord | None = None


def wants_encounter(state: GameState, round_no: int) -> bool:
    freq = state.settings.encounter_frequency
    p = FREQUENCY.get(freq, 0.75)
    if p <= 0:
        return False
    if round_no == 1:
        return True  # the first scene sets the tone
    last = state.encounter_history[-1].round if state.encounter_history else 0
    if freq in ("normal", "heartbeat") and round_no - last >= 2:
        return True  # never two encounter-free scenes in a row
    return rng.chance(state.random_seed, p, "encounter", round_no)


def _next_category(state: GameState, round_no: int) -> EncounterCategory:
    used = Counter(r.category for r in state.encounter_history)
    last = state.encounter_history[-1].category if state.encounter_history else None
    cats = [c for c in EncounterCategory if c != last] or list(EncounterCategory)
    least = min(used.get(c, 0) for c in cats)
    pool = sorted(c for c in cats if used.get(c, 0) == least)
    return rng.pick(state.random_seed, pool, "enc_cat", round_no)


def _pick_template(state: GameState, category: EncounterCategory, round_no: int) -> str:
    used = {r.key for r in state.encounter_history} | {e.key for e in state.encounters.values()}
    pool = [t for t in LIBRARY if t.category == category and t.key not in used]
    if not pool:
        pool = [t for t in LIBRARY if t.key not in used] or list(LIBRARY)
    weights = [library_weight(t.key) for t in pool]
    return rng.stream(state.random_seed, "enc_tpl", round_no).choices(pool, weights=weights, k=1)[0].key


def _callback_candidate(state: GameState, round_no: int) -> EncounterRecord | None:
    """A returning NPC is a running joke: rare enough to land, never twice in a row, never too soon."""
    history = state.encounter_history
    if not history or history[-1].callback_of:
        return None
    if sum(1 for r in history if r.callback_of) >= max(1, len(history) // 3):
        return None
    candidates = [r for r in history
                  if not r.called_back and not r.callback_of and r.npc_id in state.npcs and round_no - r.round >= 2
                  and r.winning_option]
    if not candidates or not rng.chance(state.random_seed, CALLBACK_CHANCE, "enc_callback", round_no):
        return None
    return rng.pick(state.random_seed, candidates, "enc_callback_pick", round_no)


def plan(state: GameState, round_plan: director.RoundPlan) -> EncounterPlan | None:
    """Decide whether this scene gets an encounter, who faces it, and how they answer."""
    if round_plan.is_final or not round_plan.groups or not wants_encounter(state, round_plan.number):
        return None
    rnd, seed, groups = round_plan.number, state.random_seed, round_plan.groups
    if len(groups) >= 2 and all(len(g) == 1 for g in groups) and rng.chance(seed, COUNCIL_CHANCE, "enc_council", rnd):
        idx, mode = list(range(len(groups))), EncounterMode.COUNCIL
    else:
        spot = next((i for i, g in enumerate(groups) if round_plan.spotlight in g), None)
        i = spot if spot is not None else rng.stream(seed, "enc_group", rnd).randrange(len(groups))
        idx, mode = [i], (EncounterMode.VOTE if len(groups[i]) > 1 else EncounterMode.INDIVIDUAL)
    callback = _callback_candidate(state, rnd)
    category = callback.category if callback else _next_category(state, rnd)
    return EncounterPlan(group_indexes=idx, mode=mode, category=category,
                         mechanism=rng.pick(seed, MECHANISMS, "enc_mech", rnd),
                         template_key=_pick_template(state, category, rnd), callback=callback)


# ---------------------------------------------------------------------------
# Building encounters
# ---------------------------------------------------------------------------


def from_library(key: str, ctx: Mapping[str, str]) -> Encounter:
    t = LIBRARY_BY_KEY[key]
    options = [_fill_option(o, ctx) for o in t.options]
    return Encounter(key=t.key, category=t.category, title=t.title, intro=fill(t.intro, ctx), question=fill(t.question, ctx),
                     npc_name=t.npc[0], npc_emoji=t.npc[1], npc_personality=t.npc[2], options=options, source="library")


def _fill_option(o: EncounterOption, ctx: Mapping[str, str]) -> EncounterOption:
    def fx(e: EncounterEffect) -> EncounterEffect:
        return e.model_copy(update={k: fill(getattr(e, k), ctx) for k in ("item", "obligation", "secret", "location", "title")})

    return o.model_copy(update={"label": fill(o.label, ctx), "teaser": fill(o.teaser, ctx), "aftermath": fill(o.aftermath, ctx),
                                "win": fx(o.win), "lose": fx(o.lose)}, deep=True)


def build_callback(state: GameState, record: EncounterRecord, ctx: Mapping[str, str]) -> Encounter:
    """The NPC comes back, and remembers exactly what you said."""
    npc = state.npcs[record.npc_id or ""]
    who = _join([state.players[p].display for p in record.player_ids if p in state.players]) or "someone in your party"
    original = state.encounters.get(record.encounter_id)
    chosen = original.option(record.winning_option) if original else None
    if chosen:
        did = "chose to " + re.sub(r"\byour\b", "their", _lower_first(chosen.label))
    else:
        did = "improvised an answer nobody has forgotten"
    intro = (f"{npc.emoji} {npc.name} is back. Ever since {who} {did} back in scene {record.round} (\"{record.title}\"), "
             f"{npc.name} {record.aftermath or 'has thought about nothing else'}.")
    opener = rng.pick(state.random_seed, [
        "\"We need to talk about what you did.\"",
        "\"I have prepared a presentation about what happened. It has slides.\"",
        "\"Everyone keeps asking me about it. So now I'm asking YOU.\"",
    ], "callback_q", record.encounter_id)
    options = [_fill_option(o, ctx) for o in CALLBACK_OPTIONS]
    return Encounter(key=f"callback:{record.key}", category=record.category, title=f"{npc.name} Returns", intro=intro,
                     question=opener, npc_name=npc.name, npc_emoji=npc.emoji, npc_personality=npc.personality,
                     npc_id=npc.id, options=options, source="callback", callback_of=record.encounter_id)


def signature_option(p: Player) -> EncounterOption | None:
    """Character-specific answer: a solo responder can always try their special ability."""
    ability = (p.character.special_ability or "").strip().rstrip(".")
    if not ability:
        return None
    from ..llm.services import interpret_deterministic

    tags = interpret_deterministic(ability, {}, {}, []).tags or ["perform"]
    best = max(p.character.stats.items(), key=lambda kv: (kv[1], kv[0]))[0] if p.character.stats else "weird"
    return EncounterOption(
        key="sig", style="Your Specialty", label=f"Solve it your way: {ability[:90]}",
        teaser="Your character was born for this moment. Probably.", tags=tags[:2], stat=best, risk=Risk.RISKY,
        aftermath=f"still talks about how {p.display} handled it",
        win=EncounterEffect(npc_disposition=1, resources={"reputation": 1}),
        lose=EncounterEffect(resources={"morale": -1}),
    )


def context_text(enc: Encounter) -> str:
    return f"{enc.intro}\n\n{enc.npc_emoji} {enc.npc_name}: {enc.question}"


def to_choices(enc: Encounter, solo: Player | None = None) -> list[Choice]:
    options = list(enc.options)
    if solo is not None and enc.mode == EncounterMode.INDIVIDUAL:
        sig = signature_option(solo)
        if sig and not enc.option("sig"):
            enc.options.append(sig)
            options.append(sig)
    return [
        Choice(label=o.label, description=o.teaser, tags=list(o.tags), risk=o.risk, stat=o.stat, style=o.style,
               encounter_option=o.key, target_npc_id=enc.npc_id, advances_objective=o.advances_objective)
        for o in options
    ]


def open_encounter(state: GameState, enc: Encounter, groups: list[DecisionGroup], mode: EncounterMode) -> None:
    """Register the encounter (and its NPC) and hand it to the chosen decision groups."""
    enc.mode, enc.round = mode, state.turn_number
    members = [pid for g in groups for pid in g.player_ids]
    npc = state.npcs.get(enc.npc_id or "")
    if npc is None:
        npc = NPC(name=enc.npc_name, emoji=enc.npc_emoji, personality=enc.npc_personality,
                  goal=f"get a straight answer about '{enc.title}'", location_id=groups[0].location_id,
                  introduced_round=state.turn_number)
        state.npcs[npc.id] = npc
        enc.npc_id = npc.id
    for pid in members:
        if pid not in npc.known_by:
            npc.known_by.append(pid)
    state.encounters[enc.id] = enc
    for g in groups:
        enc.group_ids.append(g.group_id)
        g.encounter_id = enc.id
        g.title = enc.title
        g.shared_context = context_text(enc)
        solo = state.players[g.player_ids[0]] if len(g.player_ids) == 1 else None
        g.available_choices = to_choices(enc, solo)
        g.allow_freeform = True
        if mode == EncounterMode.COUNCIL:
            g.shared_context += ("\n\n(Everyone in the party is facing this exact question right now, alone. "
                                 "Nobody can see anybody else's answer. The NPC will compare notes afterwards.)")


# ---------------------------------------------------------------------------
# Resolution: the engine applies every consequence
# ---------------------------------------------------------------------------


def _option_for(enc: Encounter, g: DecisionGroup, choice_id: str | None) -> EncounterOption | None:
    if not choice_id:
        return None
    c = next((c for c in g.available_choices if c.id == choice_id), None)
    return enc.option(c.encounter_option) if c else None


def _selection(enc: Encounter, g: DecisionGroup, sub) -> str:
    if sub.freeform:
        return "freeform"
    opt = _option_for(enc, g, sub.choice_id)
    key = opt.key if opt else "?"
    return f"auto:{key}" if sub.auto else key


def _apply_effect(state: GameState, enc: Encounter, effect: EncounterEffect, o: ActionOutcome) -> list[str]:
    lines: list[str] = []
    actors = [state.players[p] for p in o.planned.player_ids if p in state.players]
    names = _join([p.display for p in actors])
    for key, delta in effect.resources.items():
        if key not in RESOURCE_KEYS:
            continue
        actual = change_shared(state, key, _clamp(delta))
        if actual:
            o.result.resource_changes[key] = o.result.resource_changes.get(key, 0) + actual
            lines.append(describe_delta(state, key, actual))
    npc = state.npcs.get(enc.npc_id or "")
    if npc and effect.npc_disposition:
        npc.disposition = _clamp(npc.disposition + _clamp(effect.npc_disposition), -5, 5)
        lines.append(f"{npc.name} now feels {'much ' if abs(effect.npc_disposition) > 1 else ''}"
                     f"{'better' if effect.npc_disposition > 0 else 'worse'} about the party")
    if effect.item and actors:
        actors[0].inventory.append(Item(name=effect.item, description=f"Acquired during '{enc.title}'.", tags=["encounter"]))
        lines.append(f"{actors[0].display} is now carrying {effect.item}")
    if effect.obligation:
        cost = {k: _clamp(v) for k, v in effect.obligation_cost.items() if k in RESOURCE_KEYS} or {"reputation": -1}
        state.delayed_consequences.append(DelayedConsequence(
            trigger_round=state.turn_number + 2, player_ids=list(o.planned.player_ids), description=effect.obligation,
            resource_changes=cost, kind="obligation"))
        lines.append(f"a new obligation: {effect.obligation} (due in two scenes)")
    if effect.secret:
        for p in actors:
            o.private_notes.setdefault(p.id, []).append(effect.secret)
    if effect.location and effect.location not in {loc.name for loc in state.locations.values()}:
        loc = Location(name=effect.location, description=f"It exists because of '{enc.title}'.", tags=["social", "crowded"])
        state.locations[loc.id] = loc
        lines.append(f"a new place now exists: {effect.location}")
    if effect.title and actors:
        titles = state.world_state.setdefault("titles", {})
        for p in actors:
            titles.setdefault(p.id, [])
            if effect.title not in titles[p.id]:
                titles[p.id].append(effect.title)
            state.memory.character_memories.setdefault(p.id, []).append(f"I am now officially '{effect.title}'.")
        lines.append(f"{names} {'are' if len(actors) > 1 else 'is'} now officially '{effect.title}'")
    return lines


def apply_outcomes(state: GameState, groups: list[DecisionGroup], outcomes: list[ActionOutcome]) -> list[EncounterRecord]:
    """Apply encounter consequences to already-resolved actions (before they are narrated)."""
    by_enc: dict[str, list[tuple[DecisionGroup, ActionOutcome]]] = {}
    enc_groups: dict[str, list[DecisionGroup]] = {}
    for g in groups:
        if g.encounter_id and g.encounter_id in state.encounters:
            enc_groups.setdefault(g.encounter_id, []).append(g)
            for o in outcomes:
                if o.planned.group_id == g.group_id:
                    by_enc.setdefault(g.encounter_id, []).append((g, o))
    records: list[EncounterRecord] = []
    for enc_id, gs in enc_groups.items():
        enc = state.encounters[enc_id]
        npc = state.npcs.get(enc.npc_id or "")
        pairs = by_enc.get(enc_id, [])
        rec = EncounterRecord(encounter_id=enc.id, key=enc.key, title=enc.title, category=enc.category, mode=enc.mode,
                              source=enc.source, round=enc.round, npc_id=enc.npc_id, npc_name=enc.npc_name,
                              player_ids=[p for g in gs for p in g.player_ids], options_offered=len(enc.options),
                              callback_of=enc.callback_of, started_at=enc.started_at)
        for g in gs:
            for pid, sub in g.submissions.items():
                rec.selections[pid] = _selection(enc, g, sub)
        carried: list[tuple[str, ActionOutcome]] = []
        for g, o in pairs:
            opt = _option_for(enc, g, o.planned.choice_id) if not o.planned.freeform else None
            won = o.result.tier in WIN_TIERS
            if opt:
                effect = opt.win if won else opt.lose
            else:  # an improvised answer: the NPC has never heard anything like it
                effect = EncounterEffect(npc_disposition=1 if won else -1)
            lines = _apply_effect(state, enc, effect, o)
            o.consequences.extend(lines)
            rec.consequences.extend(lines)
            key = opt.key if opt else "freeform"
            rec.outcomes[key] = o.result.tier.value
            if not o.planned.rogue:
                carried.append((key, o))
            for pid in o.planned.player_ids:
                p = state.players.get(pid)
                if p:
                    p.stats.encounters_faced += 1
                    p.stats.encounter_wins += 1 if won else 0
            if npc:
                npc.memory.append(f"Scene {state.turn_number}: {_join([state.players[p].display for p in o.planned.player_ids])} "
                                  f"answered '{o.planned.description[:80]}' ({o.result.tier.value.replace('_', ' ')}).")
        if enc.mode == EncounterMode.COUNCIL and len(pairs) >= 2:
            _council(state, enc, pairs, rec)
        # the answer the NPC remembers: plurality of what was actually carried out
        if carried:
            counts = Counter(k for k, _ in carried)
            top = max(counts.values())
            rec.winning_option = min(k for k, v in counts.items() if v == top)
            opt = enc.option(rec.winning_option)
            rec.aftermath = opt.aftermath if opt else "has been telling everyone about your answer, with embellishments"
        rec.distinct_choices, rec.choice_diversity = diversity(rec.selections, rec.options_offered + 1)
        director.register_joke(state, f"encounter:{enc.key}", f"{enc.npc_name} and '{enc.title}'", state.turn_number, None)
        if enc.callback_of:
            for r in state.encounter_history:
                if r.encounter_id == enc.callback_of:
                    r.called_back = True
        state.encounter_history.append(rec)
        records.append(rec)
    return records


def _council(state: GameState, enc: Encounter, pairs: list[tuple[DecisionGroup, ActionOutcome]], rec: EncounterRecord) -> None:
    """Secret council: everyone answered the same question alone. Now the answers collide."""
    picks: dict[str, str] = {}
    for _, o in pairs:
        pid = o.planned.player_ids[0]
        sel = rec.selections.get(pid, "?").removeprefix("auto:")
        picks[pid] = f"freeform:{pid}" if sel == "freeform" else sel
    counts = Counter(picks.values())
    npc = state.npcs.get(enc.npc_id or "")
    who = enc.npc_name
    first = pairs[0][1]
    if len(counts) == 1:
        line = f"independently, every single member of the party gave the exact same answer — {who} finds this deeply unsettling"
        actual = change_shared(state, "morale", 1)
        if actual:
            first.result.resource_changes["morale"] = first.result.resource_changes.get("morale", 0) + actual
        for _, o in pairs:
            o.consequences.append(line)
        rec.consequences.append(line)
        rec.council_verdict = line
    elif len(counts) == len(picks):
        line = f"{who} received {len(picks)} completely contradictory answers from the party and has written every one of them down"
        actual = change_shared(state, "morale", -1)
        if actual:
            first.result.resource_changes["morale"] = first.result.resource_changes.get("morale", 0) + actual
        if npc:
            npc.disposition = _clamp(npc.disposition - 1, -5, 5)
        for _, o in pairs:
            o.consequences.append(line)
        rec.consequences.append(line)
        rec.council_verdict = line
        director.register_joke(state, f"contradictions:{enc.key}", f"{who}'s file of the party's contradictory answers",
                               state.turn_number, None)
    else:
        top = max(counts.values())
        majority = min(k for k, v in counts.items() if v == top)
        loners = [pid for pid, k in picks.items() if counts[k] == 1 and k != majority]
        for _, o in pairs:
            pid = o.planned.player_ids[0]
            if pid in loners:
                state.players[pid].stats.contrarian_picks += 1
                o.consequences.append(f"{who} noticed {state.players[pid].display} disagreed with everyone else, and is keeping an eye on them")
        dissent = _join([state.players[p].display for p in loners])
        rec.council_verdict = (f"most of the party agreed — except {dissent}, whom {who} is now keeping an eye on" if dissent
                               else "the party split into camps, and the NPC has started a spreadsheet")
        rec.consequences.append(rec.council_verdict)


def council_reveal(state: GameState, enc: Encounter, rec: EncounterRecord) -> str:
    """Public 'who said what' line, shown when a secret council resolves."""
    parts = []
    for pid, sel in rec.selections.items():
        p = state.players.get(pid)
        if not p:
            continue
        key = sel.removeprefix("auto:")
        opt = enc.option(key)
        said = f"{opt.style} — \"{opt.label}\"" if opt else "something completely improvised"
        parts.append(f"{p.display}: {said}" + (" (after hesitating far too long)" if sel.startswith("auto:") else ""))
    return f"{enc.npc_emoji} {enc.npc_name} compares notes. " + "; ".join(parts) + "."


def chronicle_records(state: GameState, records: list[EncounterRecord], key_to_event: Mapping[str, str],
                      groups: list[DecisionGroup]) -> None:
    """One chronicle event per encounter, linked to the actions that answered it, so the final story remembers."""
    for rec in records:
        enc = state.encounters.get(rec.encounter_id)
        if not enc:
            continue
        everyone = set(rec.player_ids) >= {p.id for p in state.active_players()}
        if enc.mode == EncounterMode.COUNCIL or (enc.mode == EncounterMode.VOTE and everyone):
            vis = Visibility.PUBLIC
        elif enc.mode == EncounterMode.VOTE:
            vis = Visibility.GROUP
        else:
            vis = Visibility.PRIVATE
        who = _join([state.players[p].display for p in rec.player_ids if p in state.players])
        opt = enc.option(rec.winning_option)
        answer = f"answered as {opt.style}: {_lower_first(opt.label)}" if opt else "improvised an answer"
        summary = (f"{enc.npc_emoji} {enc.npc_name} put a question to {who} — {enc.question} "
                   f"{'They' if len(rec.player_ids) > 1 else who} {answer}. "
                   + (f"Consequences: {'; '.join(rec.consequences[:4])}." if rec.consequences else ""))
        if enc.callback_of:
            summary = f"(A callback.) {enc.intro} " + summary
        related = [key_to_event[k] for k in key_to_event if any(k.startswith(g) for g in enc.group_ids)]
        importance = 3 + (1 if enc.callback_of or (enc.mode == EncounterMode.COUNCIL and rec.distinct_choices != 1) else 0)
        ev = chronicle.record(state, "encounter", player_ids=list(rec.player_ids), npc_ids=[enc.npc_id] if enc.npc_id else [],
                              visibility=vis, importance=importance, public_information=enc.title, narrative_summary=summary,
                              consequences=list(rec.consequences), related_events=related,
                              tags=["encounter", enc.category.value, enc.mode.value],
                              decisions=[{"encounter": enc.key, "selections": dict(rec.selections), "winning_option": rec.winning_option}])
        rec.event_id = ev.event_id


def analytics_props(state: GameState, rec: EncounterRecord) -> dict:
    """Counts, enums and keys only — never player text."""
    previous = [r for r in state.encounter_history if r.encounter_id != rec.encounter_id and r.started_at < rec.started_at]
    since = round(rec.started_at - previous[-1].started_at) if previous else None
    sels = list(rec.selections.values())
    return {
        "encounter_key": rec.key, "category": rec.category.value, "mode": rec.mode.value, "source": rec.source,
        "round": rec.round, "responders": len(rec.player_ids), "options_offered": rec.options_offered,
        "distinct_choices": rec.distinct_choices, "choice_diversity": rec.choice_diversity,
        "freeform_answers": sum(1 for s in sels if s == "freeform"), "auto_answers": sum(1 for s in sels if s.startswith("auto:")),
        "participation": round(sum(1 for s in sels if not s.startswith("auto:")) / max(1, len(rec.player_ids)), 3),
        "winning_option": rec.winning_option, "tiers": sorted(set(rec.outcomes.values())),
        "is_callback": bool(rec.callback_of), "seconds_since_last_encounter": since,
        "resolution_seconds": round(rec.resolved_at - rec.started_at),
    }


def library_rows(state: GameState) -> list[tuple[str, float]]:
    """(key, choice diversity) for every *library* encounter played in this adventure, for cross-game stats."""
    return [(r.key, r.choice_diversity) for r in state.encounter_history
            if r.source == "library" and len(r.selections) >= 1]


def new_generated_key(title: str) -> str:
    import hashlib

    return "gen:" + hashlib.sha1(title.lower().encode()).hexdigest()[:10]


__all__ = [
    "CATEGORY_INFO", "LIBRARY", "LIBRARY_BY_KEY", "LIBRARY_STATS", "EncounterPlan", "analytics_props", "apply_outcomes",
    "build_callback", "category_emoji", "category_label", "chronicle_records", "context_text", "council_reveal",
    "diversity", "fill", "from_library", "library_rows", "new_generated_key", "note_library_stats", "open_encounter",
    "plan", "signature_option", "to_choices", "wants_encounter",
]
