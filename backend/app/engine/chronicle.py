"""The Adventure Chronicle and the final-story pipeline.

    Game History → Event Filtering → Chronological Reconstruction →
    Cross-Player Relationship Mapping → Story Chronicle → Final Narrative →
    Validation → Complete Adventure Story

The chronicle is written *during* play as structured ``AdventureEvent``s, so
the final story is grounded in what actually happened rather than in any
model's memory of the conversation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import GAME_TITLE
from ..models.game import (
    AdventureEvent,
    FinalStory,
    GameOutcomeKind,
    GameState,
    StoryChapter,
    ValidationReport,
    Visibility,
)
from . import rng
from .procgen import keywords, lower_first

# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def record(state: GameState, event_type: str, **fields) -> AdventureEvent:
    ev = AdventureEvent(event_type=event_type, sequence_number=len(state.adventure_chronicle) + 1,
                        round=state.turn_number, **fields)
    state.adventure_chronicle.append(ev)
    return ev


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


@dataclass
class Intersection:
    cause: AdventureEvent
    effect: AdventureEvent
    description: str


@dataclass
class StoryChronicle:
    title: str
    theme: str
    objective: str
    antagonist: str
    macguffin: str
    characters: list[dict]
    rounds: dict[int, list[AdventureEvent]]
    intersections: list[Intersection]
    hidden_truths: list[str]
    secret_motivations: dict[str, str]
    running_jokes: list[str]
    npcs: list[dict]
    outcome_kind: str
    outcome_headline: str
    outcome_summary: str
    personal: dict[str, str]
    achievements: dict[str, list[str]]
    epilogue_facts: dict[str, list[str]] = field(default_factory=dict)
    forbidden: list[str] = field(default_factory=list)  # never_reveal texts
    major_events: list[AdventureEvent] = field(default_factory=list)


def filter_events(events: list[AdventureEvent]) -> list[AdventureEvent]:
    """Drop trivia and anything marked never_reveal; scrub never-reveal fragments."""
    out = []
    for ev in events:
        if ev.visibility == Visibility.NEVER_REVEAL:
            continue
        if ev.importance < 2 and ev.event_type not in ("action", "random_event", "outcome", "final_action"):
            continue
        out.append(ev)
    return out


def map_relationships(events: list[AdventureEvent]) -> list[Intersection]:
    """Find where one player's choice changed another player's outcome."""
    by_id = {e.event_id: e for e in events}
    out: list[Intersection] = []
    seen: set[tuple[str, str]] = set()
    seen_text: set[str] = set()
    for ev in events:
        if ev.event_type not in ("action", "group_action", "final_action", "delayed_consequence"):
            continue
        links = [c.removeprefix("[link] ") for c in ev.consequences if c.startswith("[link]")]
        for rid in ev.related_events:
            cause = by_id.get(rid)
            if not cause or set(cause.player_ids) == set(ev.player_ids) or (cause.event_id, ev.event_id) in seen:
                continue
            desc = next((link for link in links if link not in seen_text), "")
            seen.add((cause.event_id, ev.event_id))
            if desc:
                seen_text.add(desc)
            out.append(Intersection(cause=cause, effect=ev, description=desc))
    return out


def build_story_chronicle(state: GameState) -> StoryChronicle:
    events = sorted(filter_events(state.adventure_chronicle), key=lambda e: e.sequence_number)
    rounds: dict[int, list[AdventureEvent]] = {}
    for ev in events:
        rounds.setdefault(ev.round, []).append(ev)
    o = state.outcome
    obj = state.objective
    never = [i.text for i in state.hidden_information.values() if i.visibility == Visibility.NEVER_REVEAL]
    names = {p.id: p.display for p in state.players.values()}
    return StoryChronicle(
        title=f"{GAME_TITLE}: {state.theme or 'An Untitled Disaster'}",
        theme=state.theme or "",
        objective=f"{obj.title} — {obj.description}" if obj else "",
        antagonist=obj.antagonist if obj else "",
        macguffin=obj.macguffin if obj else "",
        characters=[
            {"id": p.id, "name": p.display, "player": p.name, "archetype": p.character.archetype,
             "personality": p.character.personality, "ability": p.character.special_ability,
             "weakness": p.character.weakness, "item": p.character.starting_item, "trait": p.character.humorous_trait,
             "status": p.status, "health": p.resources.health,
             "inventory": [i.name for i in p.inventory]}
            for p in sorted(state.active_players(), key=lambda p: p.joined_at)
        ],
        rounds=rounds,
        intersections=map_relationships(events),
        hidden_truths=[f"{hv.fact} (discovered by {', '.join(names.get(p, '?') for p in hv.discovered_by) or 'nobody'})"
                       for hv in state.hidden_variables if hv.visibility != Visibility.NEVER_REVEAL],
        secret_motivations={p.display: p.character.secret_motivation for p in state.active_players() if p.character.secret_motivation},
        running_jokes=[j.description for j in state.memory.running_jokes],
        npcs=[{"name": n.name, "personality": n.personality, "goal": n.goal, "disposition": n.disposition,
               "memory": n.memory[-3:]} for n in state.npcs.values() if n.known_by or n.memory],
        outcome_kind=o.kind if o else "",
        outcome_headline=o.headline if o else "",
        outcome_summary=o.group_summary if o else "",
        personal={names.get(k, k): v for k, v in (o.personal if o else {}).items()},
        achievements={names.get(k, k): [a.title for a in v] for k, v in (o.achievements if o else {}).items()},
        epilogue_facts={p.display: _epilogue_facts(state, p.id) for p in state.active_players()},
        forbidden=never,
        major_events=[e for e in events if e.importance >= 4],
    )


def _epilogue_facts(state: GameState, pid: str) -> list[str]:
    p = state.players[pid]
    s = p.stats
    facts = [f"{s.successes} successes, {s.failures} failures, {s.complications_caused} complications"]
    if s.betrayals:
        facts.append(f"betrayed the party {s.betrayals} time(s)")
    if s.helps:
        facts.append(f"helped others {s.helps} time(s)")
    if s.secrets_found:
        facts.append("discovered a hidden truth")
    if p.inventory:
        facts.append("ended up holding: " + ", ".join(i.name for i in p.inventory))
    return facts


def chronicle_text(sc: StoryChronicle) -> str:
    """Render the structured chronicle for the final-story LLM."""
    lines = [f"TITLE: {sc.title}", f"THEME: {sc.theme}", f"OBJECTIVE: {sc.objective}",
             f"ANTAGONIST: {sc.antagonist}", f"MACGUFFIN: {sc.macguffin}", "CHARACTERS:"]
    for c in sc.characters:
        lines.append(f"- {c['name']} ({c['archetype']}; {c['personality']}; ability: {c['ability']}; weakness: "
                     f"{c['weakness']}; item: {c['item']}; trait: {c['trait']}; final status: {c['status']})")
    for rnd in sorted(sc.rounds):
        lines.append(f"\nSCENE {rnd}:" if rnd else "\nSETUP:")
        for ev in sc.rounds[rnd]:
            who = ",".join(ev.player_ids)
            secret = " [HIDDEN DURING PLAY — reveal it]" if ev.visibility != Visibility.PUBLIC else ""
            lines.append(f"  #{ev.sequence_number} {ev.event_type}{secret} players={who} importance={ev.importance}: "
                         f"{ev.narrative_summary or ev.public_information}")
            for pid, txt in ev.private_information.items():
                lines.append(f"     private to {pid}: {txt}")
            if ev.consequences:
                lines.append(f"     consequences: {'; '.join(c.removeprefix('[link] ') for c in ev.consequences)}")
    if sc.intersections:
        lines.append("\nINTERSECTIONS (one player's actions affected another's — make these the big reveals):")
        for it in sc.intersections:
            lines.append(f"- #{it.cause.sequence_number} → #{it.effect.sequence_number}: {it.description}")
    lines.append("\nHIDDEN TRUTHS: " + "; ".join(sc.hidden_truths))
    lines.append("SECRET MOTIVATIONS: " + "; ".join(f"{k}: {v}" for k, v in sc.secret_motivations.items()))
    lines.append("RUNNING JOKES: " + "; ".join(sc.running_jokes))
    lines.append("NPCS: " + "; ".join(f"{n['name']} ({n['personality']})" for n in sc.npcs))
    lines.append(f"\nOUTCOME: {sc.outcome_kind} — {sc.outcome_headline}. {sc.outcome_summary}")
    for name, text in sc.personal.items():
        lines.append(f"PERSONAL OUTCOME {name}: {text}")
    for name, ach in sc.achievements.items():
        lines.append(f"ACHIEVEMENTS {name}: {', '.join(ach)}")
    # player ids -> names mapping so the model never prints ids
    lines.append("\nPLAYER ID MAP: " + "; ".join(f"{c['id']}={c['name']}" for c in sc.characters))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Procedural final story
# ---------------------------------------------------------------------------

MEANWHILE = ["Meanwhile,", "At roughly the same moment,", "Elsewhere —", "Across the building,", "Not far away,",
             "While all of that was happening,"]
REVEAL = ["What nobody knew at the time was that", "Here is the part nobody found out until much later:",
          "Unbeknownst to the rest of the party,", "In a development that was, at the time, a closely guarded secret,"]
LINK = ["This, it turns out, is exactly why", "Which — and this is important — explains why", "And THAT is why"]
OUTCOME_LINES = {
    GameOutcomeKind.FULL_SUCCESS: "Against every reasonable expectation, they won. Cleanly. Nobody has ever been more surprised.",
    GameOutcomeKind.PARTIAL_SUCCESS: "They half-won, which, given everything, is the most successful thing any of them had ever done.",
    GameOutcomeKind.FAILURE: "They failed. Gloriously, memorably, and in a way that would be described at parties for years.",
    GameOutcomeKind.COSTLY_SUCCESS: "They won — but at a cost so specific and so ridiculous that it still comes up at reunions.",
    GameOutcomeKind.ACCIDENTAL_SUCCESS: "They won by accident. Nobody planned it. Several people actively planned against it.",
    GameOutcomeKind.SUCCESS_NEW_PROBLEM: "They won. Unfortunately, winning created a brand-new and significantly larger problem.",
}


def _names_for(state: GameState, ids: list[str]) -> str:
    names = [state.players[p].display for p in ids if p in state.players]
    if not names:
        return "someone"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _clean(text: str, state: GameState) -> str:
    for p in state.players.values():
        text = text.replace(p.id, p.display)
    return text.strip()


def procedural_story(state: GameState, sc: StoryChronicle, words: tuple[int, int]) -> FinalStory:
    seed = state.random_seed
    obj = state.objective
    chapters: list[StoryChapter] = []
    round_titles = {r.number: r.title for r in state.rounds}

    # Chapter 1 — opening
    intro = [f"This is the story of {_names_for(state, [c['id'] for c in sc.characters])}, and the day they decided to "
             f"attempt something that can only be described as \"{sc.theme}\"."]
    if obj:
        intro.append(f"The plan, such as it was: {obj.description}")
        intro.append(f"Success meant: {'; '.join(obj.success_conditions).lower()}. Failure meant almost anything else.")
    for c in sc.characters:
        intro.append(
            f"{c['name']} — {c['archetype'].lower() or 'an unclassifiable person'} — was {c['personality'].lower() or 'present'}. "
            f"Their special ability: {c['ability'].lower() or 'unclear'}. Their weakness: {c['weakness'].lower() or 'also unclear'}. "
            f"They brought {c['item'] or 'nothing useful'}." + (f" Also, {c['name']} {c['trait'].lower().rstrip('.')}." if c['trait'] else "")
        )
    intro.append(f"Standing between them and glory was {sc.antagonist or 'fate'}. Nobody had a plan for that. "
                 "Several people had a plan for snacks.")
    first = sc.rounds.get(1, [])
    intro.extend(_round_paragraphs(state, first, seed, 1, opening=True))
    chapters.append(StoryChapter(index=0, title=f"Chapter 1 — {round_titles.get(1, 'An Extremely Bad Idea')}",
                                 text="\n\n".join(intro)))

    # Middle chapters
    final_round = max(sc.rounds) if sc.rounds else 0
    for rnd in sorted(r for r in sc.rounds if 1 < r < final_round or (r == final_round and not _is_final_round(sc.rounds[r]))):
        paras = _round_paragraphs(state, sc.rounds[rnd], seed, rnd)
        if not paras:
            continue
        chapters.append(StoryChapter(index=len(chapters), title=f"Chapter {len(chapters) + 1} — {round_titles.get(rnd, 'Meanwhile')}",
                                     text="\n\n".join(paras)))

    # Hidden events chapter: the big reveal
    reveal = []
    if sc.intersections:
        reveal.append("Only when everyone finally compared notes did the full picture emerge. Several people had to sit down.")
    for it in sc.intersections[:12]:
        cause_who = _names_for(state, it.cause.player_ids)
        effect_who = _names_for(state, it.effect.player_ids)
        desc = re.sub(r"^(Elsewhere|Somewhere nearby|Meanwhile),\s*", "", _clean(it.description, state))
        desc = desc[:1].upper() + desc[1:]
        lead = rng.pick(seed, LINK, "link", it.cause.event_id, it.effect.event_id)
        when = "that same scene" if it.cause.round == it.effect.round else f"scene {it.cause.round}"
        if desc:
            reveal.append(f"{lead} scene {it.effect.round} went the way it did for {effect_who}. "
                          f"In {when}, {cause_who} {_decided(it.cause, cause_who)} {desc}")
        else:
            reveal.append(f"{lead} {effect_who} kept running into trouble: back in {when}, {cause_who} {_decided(it.cause, cause_who)}")
    for hv in state.hidden_variables:
        finders = _names_for(state, hv.discovered_by) if hv.discovered_by else None
        if finders:
            reveal.append(f"{rng.pick(seed, REVEAL, 'rev', hv.id)} {finders} had discovered the truth: {hv.fact}.")
        else:
            reveal.append(f"And the truth that nobody ever found? {hv.fact[0].upper() + hv.fact[1:]}. It was right there the whole time.")
    for name, motive in sc.secret_motivations.items():
        reveal.append(f"As for {name}: the whole time, their secret motivation was simple — {motive[0].lower() + motive[1:] if motive else 'unknown'}. "
                      "It explains a great deal, in retrospect.")
    if reveal:
        chapters.append(StoryChapter(index=len(chapters), title=f"Chapter {len(chapters) + 1} — Wait. You Did WHAT?",
                                     text="\n\n".join(reveal)))

    # Climax
    climax = []
    if obj:
        climax.append(f"And so it came down to this: {obj.final_challenge}.")
    for rnd in sorted(sc.rounds):
        if _is_final_round(sc.rounds[rnd]):
            climax.extend(_round_paragraphs(state, sc.rounds[rnd], seed, rnd))
    kind = GameOutcomeKind(sc.outcome_kind) if sc.outcome_kind else GameOutcomeKind.PARTIAL_SUCCESS
    climax.append(OUTCOME_LINES[kind])
    if sc.outcome_headline:
        climax.append(f"{sc.outcome_headline}. {sc.outcome_summary}")
    chapters.append(StoryChapter(index=len(chapters), title=f"Chapter {len(chapters) + 1} — {round_titles.get(final_round, 'The Final Disaster')}",
                                 text="\n\n".join(climax)))

    # Epilogue
    epilogues: dict[str, str] = {}
    epi = []
    for c in sc.characters:
        personal = sc.personal.get(c["name"], "")
        ach = sc.achievements.get(c["name"], [])
        line = f"{c['name']}: {personal}"
        if ach:
            line += f" They will forever be remembered as \"{ach[0]}\"."
        if c["inventory"]:
            line += f" They still have {c['inventory'][-1]}. Nobody has asked how."
        epilogues[c["name"]] = line
        epi.append(line)
    if sc.running_jokes:
        epi.append(f"And somewhere out there: {sc.running_jokes[-1].rstrip('.')}. Still. Some things never end.")
    for n in sc.npcs[:3]:
        mood = "fondly" if n["disposition"] > 1 else ("with deep suspicion" if n["disposition"] < 0 else "with confusion")
        epi.append(f"{n['name']} still talks about the party {mood}.")
    title_word = {"failure": "Somehow, They Lost", "partial_success": "Sort Of", "costly_success": "At What Cost",
                  "accidental_success": "Somehow, They Won", "success_new_problem": "Now There's a New Problem",
                  "full_success": "Somehow, They Won"}.get(sc.outcome_kind, "Afterwards")
    chapters.append(StoryChapter(index=len(chapters), title=f"Epilogue — {title_word}", text="\n\n".join(epi)))

    # Pad short stories with a grounded "documentary" interlude built from real stats.
    total = sum(len(c.text.split()) for c in chapters)
    if total < words[0]:
        chapters.insert(len(chapters) - 1, StoryChapter(index=0, title="Interlude — By the Numbers",
                                                        text="\n\n".join(_documentary(state, sc, words[0] - total))))
    for i, c in enumerate(chapters):
        c.index = i
        c.word_count = len(c.text.split())
    return FinalStory(title=f"{GAME_TITLE}: {state.theme or ''}".strip(": "), chapters=chapters, epilogues=epilogues,
                      word_count=sum(c.word_count for c in chapters), generated_by="procedural")


def _decided(ev: AdventureEvent, who: str) -> str:
    """'had decided to hide in the vents.' / 'had a plan, in their own words: "…".'"""
    d = ev.decisions[0] if ev.decisions else {}
    action = (d.get("action") or "act").rstrip(".")
    if d.get("freeform"):
        return f"had a plan. In their own words: \"{action}.\""
    return f"had decided to {lower_first(action)}."


def _is_final_round(events: list[AdventureEvent]) -> bool:
    return any(e.event_type == "final_action" for e in events)


def _round_paragraphs(state: GameState, events: list[AdventureEvent], seed: int, rnd: int, opening: bool = False) -> list[str]:
    paras: list[str] = []
    seen_players: set[str] = set()
    for ev in events:
        if ev.event_type == "scene" and not opening:
            paras.append(_clean(ev.narrative_summary or ev.public_information, state))
        elif ev.event_type == "scene" and opening:
            paras.append(_clean(ev.narrative_summary, state))
        elif ev.event_type in ("random_event", "delayed_consequence", "npc_introduced"):
            body = _clean(ev.narrative_summary, state)
            paras.append(rng.pick(seed, REVEAL, "rev", ev.event_id) + " " + lower_first(body) if ev.visibility != Visibility.PUBLIC else body)
        elif ev.event_type in ("action", "final_action", "group_action"):
            who = _names_for(state, ev.player_ids)
            text = _clean(ev.narrative_summary, state)
            if ev.visibility != Visibility.PUBLIC and ev.event_type != "final_action":
                rogue = " — going completely rogue —" if ev.decisions and ev.decisions[0].get("rogue") else ""
                lead = rng.pick(seed, REVEAL, "rev", ev.event_id)
                if seen_players and not set(ev.player_ids) & seen_players:
                    lead = rng.pick(seed, MEANWHILE, "mw", ev.event_id) + " " + lower_first(lead)
                text = f"{lead} {who}{rogue} {_decided(ev, who)} " + text
            elif seen_players and not set(ev.player_ids) & seen_players:
                text = rng.pick(seed, MEANWHILE, "mw", ev.event_id) + " " + lower_first(text)
            seen_players |= set(ev.player_ids)
            for pid, secret in ev.private_information.items():
                if pid in state.players:
                    text += f" (Privately, {state.players[pid].display} learned: {_clean(secret, state)})"
            paras.append(text)
        elif ev.event_type in ("discovery", "betrayal"):
            paras.append(f"{rng.pick(seed, REVEAL, 'rev', ev.event_id)} {lower_first(_clean(ev.narrative_summary, state))}")
        elif ev.event_type == "objective":
            paras.append(_clean(ev.narrative_summary, state))
    return [p for p in paras if p]


def _documentary(state: GameState, sc: StoryChronicle, need: int) -> list[str]:
    out = ["Historians (one historian, mostly guessing) have since tried to reconstruct exactly how this happened. The numbers help."]
    for p in sorted(state.active_players(), key=lambda p: p.joined_at):
        s = p.stats
        out.append(
            f"{p.display} took {s.actions} actions, {s.freeform_actions} of them entirely off-script. They succeeded "
            f"{s.successes} times, failed {s.failures} times, and caused {s.complications_caused} complications"
            + (f", betrayed the party {s.betrayals} time(s)" if s.betrayals else "")
            + (f", helped someone {s.helps} time(s)" if s.helps else "")
            + f". They ended the adventure with {p.resources.health} health and {p.resources.luck} luck. "
            f"Their weakness — {p.character.weakness.lower() or 'unknown'} — came up more than anyone would like."
        )
    res = ", ".join(f"{t.label.lower()} at {t.value} of {t.max}" for t in state.shared_resources.values())
    out.append(f"By the end, the party's shared resources stood at: {res}. Each number has a story. Most of the stories are embarrassing.")
    if sc.npcs:
        out.append("The locals had opinions. " + " ".join(
            f"{n['name']}, who {n['personality'].lower()}, wanted only to {n['goal'].lower()}." for n in sc.npcs[:4]))
    while sum(len(x.split()) for x in out) < need and len(out) < 40:
        ev = rng.pick(state.random_seed, sc.major_events or state.adventure_chronicle or [None], "doc", len(out))  # type: ignore[list-item]
        if ev is None:
            break
        out.append(f"Looking back at scene {ev.round}, one witness remembers it like this: \"{_clean(ev.narrative_summary, state)}\"")
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

SUCCESS_WORDS = ("won", "win", "succeed", "success", "victory", "triumph", "did it", "pulled it off")
FAILURE_WORDS = ("fail", "lost", "defeat", "didn't make it", "did not make it", "doom", "disaster")


def validate_story(state: GameState, sc: StoryChronicle, story: FinalStory, words: tuple[int, int]) -> ValidationReport:
    text = "\n".join(c.title + "\n" + c.text for c in story.chapters) + "\n" + "\n".join(story.epilogues.values())
    low = text.lower()
    issues: list[str] = []
    for c in sc.characters:
        if c["name"].lower() not in low:
            issues.append(f"Missing player character {c['name']}")
    obj = state.objective
    if obj and not (_mentions(low, obj.title) or _mentions(low, obj.macguffin) or _mentions(low, obj.description)):
        issues.append("Does not mention the shared objective")
    for ev in sc.major_events:
        if not _mentions(low, ev.narrative_summary, need=0.35):
            issues.append(f"Major event #{ev.sequence_number} is not reflected: {ev.narrative_summary[:120]}")
    kind = sc.outcome_kind
    final_text = " ".join(c.text.lower() for c in story.chapters[-2:])
    if kind == GameOutcomeKind.FAILURE and not any(w in final_text for w in FAILURE_WORDS):
        issues.append("Does not correctly state that the party failed")
    if kind and kind != GameOutcomeKind.FAILURE and not any(w in final_text for w in SUCCESS_WORDS + ("half",)):
        issues.append("Does not correctly state the party's success")
    if kind and kind != GameOutcomeKind.FAILURE and "they failed" in final_text and "half" not in final_text:
        issues.append("Contradicts the recorded outcome")
    for hv in state.hidden_variables:
        if hv.visibility == Visibility.NEVER_REVEAL:
            continue
        if not _mentions(low, hv.fact, need=0.5):
            issues.append(f"Hidden truth not revealed: {hv.fact}")
    for n in sc.npcs:
        if n.get("memory") and n["name"].lower().split()[0] not in low:
            issues.append(f"Important NPC missing: {n['name']}")
    for joke in state.memory.running_jokes:
        if joke.mentions >= 2 and joke.subject.lower() not in low:
            issues.append(f"Running joke dropped: {joke.subject}")
    for secret in sc.forbidden:
        if secret and secret.lower()[:60] in low:
            issues.append("Reveals information marked never_reveal")
    for p in state.players.values():
        if p.id in text:
            issues.append("Leaks internal player ids")
            break
    count = len(text.split())
    if count < words[0] * 0.6:
        issues.append(f"Too short ({count} words; target {words[0]}-{words[1]})")
    return ValidationReport(passed=not issues, issues=issues)


def _mentions(low_text: str, phrase: str, need: float = 0.4) -> bool:
    kws = [k for k in keywords(phrase) if len(k) > 3][:12]
    if not kws:
        return True
    hits = sum(1 for k in kws if re.search(rf"\b{re.escape(k)}", low_text))
    return hits / len(kws) >= need
