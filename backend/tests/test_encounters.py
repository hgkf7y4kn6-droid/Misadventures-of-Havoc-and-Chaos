"""The Ridiculous Encounter Engine: content, pacing, authority, memory, moderation and generation."""

import itertools
import re

import pytest

from app.engine import encounters
from app.engine.encounters import LIBRARY
from app.engine.game_manager import GameError
from app.engine.resolution import ActionOutcome, PlannedAction
from app.engine.visibility import project
from app.llm.safety import family_friendly, moderate
from app.llm.services import LLMService
from app.models import events as ev
from app.models.game import (
    DecisionGroup,
    DecisionKind,
    EncounterCategory,
    EncounterMode,
    OutcomeTier,
    ResolvedAction,
    Risk,
    Submission,
)
from app.models.llm_schemas import TAG_VOCABULARY
from tests.conftest import make_game, play_through
from tests.test_llm_services import ScriptedProvider

RESOURCES = {"food", "money", "time", "supplies", "morale", "reputation", "vehicle"}


async def start_adventure(settings, n=3, seed=42, frequency="normal", llm=None):
    """Lobby → themes → characters → scene 1, with every character saved."""
    mgr, players, _pub = await make_game(settings, n=n, seed=seed)
    if llm is not None:
        mgr.llm = llm
    host = players[0]
    await mgr.handle(host.id, ev.UpdateSettings(action="update_settings", settings={"encounter_frequency": frequency}))
    for p in players[1:]:
        await mgr.handle(p.id, ev.SetReady(action="set_ready", ready=True))
    await mgr.handle(host.id, ev.StartGame(action="start_game"))
    for p in players:
        await mgr.handle(p.id, ev.SubmitTheme(action="submit_theme", text="Pirates steal the moon"))
    if mgr.state.phase.value == "theme_voting":
        for p in players:
            await mgr.handle(p.id, ev.CastVote(action="cast_vote", option_id=mgr.state.theme_options[0].id))
    await mgr.on_timeout("objective_reveal")
    for p in players:
        await mgr.handle(p.id, ev.SaveCharacter(action="save_character", name=p.name, archetype="Overconfident Lifeguard",
                                                special_ability="Can talk anyone into anything", weakness="Afraid of geese",
                                                secret_motivation="Wants to be famous", starting_item="Whistle",
                                                humorous_trait="narrates own actions"))
    assert mgr.state.phase.value == "adventure"
    return mgr, players


def _outcome(group: DecisionGroup, pid: str, choice_id: str | None, tier: OutcomeTier, freeform: bool = False) -> ActionOutcome:
    planned = PlannedAction(key=f"{group.group_id}:{pid}", group_id=group.group_id, player_ids=[pid], description="answer",
                            tags=["negotiate"], risk=Risk.RISKY, stat="charm", choice_id=choice_id, freeform=freeform)
    return ActionOutcome(planned=planned, result=ResolvedAction(player_ids=[pid], description="answer", tier=tier, roll=10, target=10))


def _solo_groups_with(mgr, enc_key: str, mode: EncounterMode):
    s = mgr.state
    groups = [DecisionGroup(round=s.turn_number, kind=DecisionKind.INDIVIDUAL, player_ids=[p.id], shared_context="",
                            available_choices=[]) for p in s.active_players()]
    if mode != EncounterMode.COUNCIL:
        groups = groups[:1]
    enc = encounters.from_library(enc_key, {"loc": "the docks", "mac": "the moon", "threat": "the Admiral"})
    encounters.open_encounter(s, enc, groups, mode)
    return enc, groups


# ---------------------------------------------------------------------------
# The library
# ---------------------------------------------------------------------------


def test_library_covers_every_category_with_original_well_formed_encounters():
    by_cat = {c: [t for t in LIBRARY if t.category == c] for c in EncounterCategory}
    assert all(len(v) >= 3 for v in by_cat.values()), {c.value: len(v) for c, v in by_cat.items()}
    assert len({t.key for t in LIBRARY}) == len(LIBRARY)
    for t in LIBRARY:
        enc = encounters.from_library(t.key, {"loc": "X", "mac": "Y", "threat": "Z"})
        assert 3 <= len(enc.options) <= 4
        assert len({o.style for o in enc.options}) == len(enc.options), t.key
        assert len({o.label for o in enc.options}) == len(enc.options), t.key
        assert len({o.risk for o in enc.options}) >= 2, f"{t.key}: every answer has the same risk"
        texts = [enc.title, enc.intro, enc.question, enc.npc_name, enc.npc_personality]
        for o in enc.options:
            assert o.teaser and o.aftermath, (t.key, o.key)
            assert set(o.tags) <= set(TAG_VOCABULARY) and o.stat in ("brawn", "brains", "charm", "sneak", "weird")
            assert "betray" not in o.tags and "destroy" not in o.tags  # encounter answers are mischief, not sabotage
            for fx in (o.win, o.lose):
                assert set(fx.resources) <= RESOURCES and all(abs(v) <= 2 for v in fx.resources.values())
                assert abs(fx.npc_disposition) <= 2
                assert o.win != o.lose
                texts += [fx.item, fx.obligation, fx.secret, fx.location, fx.title]
            texts += [o.style, o.label, o.teaser, o.aftermath]
        for text in texts:
            assert family_friendly(text), (t.key, text)
            assert "{" not in text, (t.key, text)  # every placeholder was filled


def test_choice_diversity_metric():
    assert encounters.diversity({"a": "x", "b": "x", "c": "x"}) == (1, 0.0)
    assert encounters.diversity({"a": "x", "b": "y", "c": "z"})[1] == 1.0
    distinct, d = encounters.diversity({"a": "x", "b": "x", "c": "y", "d": "auto:x"})
    assert distinct == 2 and 0 < d < 1
    assert encounters.diversity({}) == (0, 0.0)
    assert encounters.diversity({"a": "freeform", "b": "freeform"}) == (2, 1.0)  # two improvisers did not agree


def test_dominated_encounters_are_drawn_less_often():
    encounters.LIBRARY_STATS.clear()
    assert encounters.library_weight("seven_day_crisis") == 1.0
    encounters.note_library_stats({"seven_day_crisis": (10, 0.05), "bench_custody": (10, 0.95)})
    try:
        assert encounters.library_weight("seven_day_crisis") < encounters.library_weight("bench_custody")
    finally:
        encounters.LIBRARY_STATS.clear()


# ---------------------------------------------------------------------------
# Pacing & presentation
# ---------------------------------------------------------------------------


async def test_first_scene_opens_with_an_encounter_for_the_whole_party(settings):
    mgr, players = await start_adventure(settings, n=3)
    s = mgr.state
    groups = list(s.pending_decisions.values())
    enc_groups = [g for g in groups if g.encounter_id]
    assert len(enc_groups) == 1
    enc = s.encounters[enc_groups[0].encounter_id]
    assert enc.mode == EncounterMode.VOTE and set(enc_groups[0].player_ids) == {p.id for p in players}
    assert enc.npc_id in s.npcs and set(s.npcs[enc.npc_id].known_by) >= {p.id for p in players}
    choices = enc_groups[0].available_choices
    assert all(c.style and c.description and c.encounter_option for c in choices)
    # everyone sees the interruption in their feed
    assert any(e.kind == "encounter" and e.visibility.value == "public" for e in s.story_history)
    # the projection shows the encounter, never its consequences
    view = project(s, players[0].id)
    d = view["decisions"][0]
    assert d["encounter"]["title"] == enc.title and d["encounter"]["category_label"]
    assert "win" not in str(d["encounter"]) and "obligation" not in str(d)


async def test_frequency_off_means_no_encounters(settings):
    mgr, _players = await start_adventure(settings, n=3, frequency="off")
    assert not any(g.encounter_id for g in mgr.state.pending_decisions.values())


async def test_invalid_frequency_is_rejected(settings):
    mgr, players, _ = await make_game(settings, n=2)
    with pytest.raises(GameError):
        await mgr.handle(players[0].id, ev.UpdateSettings(action="update_settings", settings={"encounter_frequency": "always!!"}))


@pytest.mark.parametrize("seed", [3, 8, 21])
async def test_encounters_are_the_heartbeat_of_a_full_adventure(settings, seed):
    mgr, players, _ = await make_game(settings, n=4, length="long", seed=seed)
    st = await play_through(mgr, players)
    hist = st.encounter_history
    rounds = [r.round for r in hist]
    assert st.phase.value == "ended"
    assert 1 in rounds
    gaps = [b - a for a, b in zip([0, *rounds], rounds + [st.total_rounds + 1], strict=False)]
    assert max(gaps[1:-1] or [1]) <= 2  # never two encounter-free scenes in a row (normal frequency)
    keys = [r.key for r in hist if r.source == "library"]
    assert len(keys) == len(set(keys))  # no repeats within an adventure
    callbacks = [r for r in hist if r.callback_of]
    assert all(not a.callback_of or not b.callback_of for a, b in itertools.pairwise(hist))
    assert len(callbacks) <= max(1, len(hist) // 3) + 1
    # the chronicle and the final story remember every encounter
    assert sum(1 for e in st.adventure_chronicle if e.event_type == "encounter") == len(hist)
    story = " ".join(c.text for c in st.final_story.chapters)
    assert all(r.npc_name.split()[0] in story for r in hist)


async def test_solo_responders_get_a_signature_answer(settings):
    mgr, _players = await start_adventure(settings, n=3)
    s = mgr.state
    s.turn_number = 2
    _enc, groups = _solo_groups_with(mgr, "polite_haunting", EncounterMode.INDIVIDUAL)
    sig = [c for c in groups[0].available_choices if c.encounter_option == "sig"]
    assert sig and "talk anyone into anything" in sig[0].label.lower()


# ---------------------------------------------------------------------------
# Authority: the engine applies every consequence
# ---------------------------------------------------------------------------


async def test_answers_have_engine_applied_consequences(settings):
    mgr, _players = await start_adventure(settings, n=3)
    s = mgr.state
    s.turn_number = 2
    enc, groups = _solo_groups_with(mgr, "seven_day_crisis", EncounterMode.INDIVIDUAL)
    g, pid = groups[0], groups[0].player_ids[0]
    diplomat = next(c for c in g.available_choices if c.encounter_option == "a")
    g.submissions[pid] = Submission(player_id=pid, choice_id=diplomat.id)
    rep0, disp0 = s.res("reputation"), s.npcs[enc.npc_id].disposition
    o = _outcome(g, pid, diplomat.id, OutcomeTier.SUCCESS)
    recs = encounters.apply_outcomes(s, [g], [o])
    assert s.res("reputation") == rep0 + 1 and o.result.resource_changes["reputation"] == 1
    assert s.npcs[enc.npc_id].disposition == disp0 + 2
    assert s.world_state["titles"][pid] == ["Keeper of the Gym Calendar"]
    assert any("Keeper of the Gym Calendar" in c for c in o.consequences)  # narrated
    rec = recs[0]
    assert rec.winning_option == "a" and rec.selections == {pid: "a"} and "Gym Calendar" in rec.aftermath
    assert s.players[pid].stats.encounter_wins == 1 and s.encounter_history[-1] is rec


async def test_setbacks_create_obligations_and_hidden_consequences_stay_private(settings):
    mgr, _players = await start_adventure(settings, n=3)
    s = mgr.state
    s.turn_number = 2
    _enc, groups = _solo_groups_with(mgr, "seven_day_crisis", EncounterMode.INDIVIDUAL)
    g, pid = groups[0], groups[0].player_ids[0]
    opportunist = next(c for c in g.available_choices if c.encounter_option == "c")
    g.submissions[pid] = Submission(player_id=pid, choice_id=opportunist.id)
    fail = _outcome(g, pid, opportunist.id, OutcomeTier.FAILURE)
    encounters.apply_outcomes(s, [g], [fail])
    owed = [d for d in s.delayed_consequences if d.kind == "obligation"]
    assert owed and owed[0].trigger_round == 4 and owed[0].resource_changes == {"money": -1}
    # a winning opportunist pockets the money in secret
    s.encounter_history.clear()
    win = _outcome(g, pid, opportunist.id, OutcomeTier.UNEXPECTED_SUCCESS)
    encounters.apply_outcomes(s, [g], [win])
    assert list(win.private_notes) == [pid] and "Day Nine" in win.private_notes[pid][0]


async def test_improvised_answers_count_and_are_remembered(settings):
    mgr, _players = await start_adventure(settings, n=3)
    s = mgr.state
    s.turn_number = 2
    _enc, groups = _solo_groups_with(mgr, "does_the_horse_know", EncounterMode.INDIVIDUAL)
    g, pid = groups[0], groups[0].player_ids[0]
    g.submissions[pid] = Submission(player_id=pid, freeform="I teach the horse to read")
    rec = encounters.apply_outcomes(s, [g], [_outcome(g, pid, None, OutcomeTier.SUCCESS, freeform=True)])[0]
    assert rec.selections == {pid: "freeform"} and rec.winning_option == "freeform"


# ---------------------------------------------------------------------------
# Secret councils: everyone answers alone, then the answers collide
# ---------------------------------------------------------------------------


async def _council(settings, picks: list[str]):
    mgr, _players = await start_adventure(settings, n=len(picks))
    s = mgr.state
    s.turn_number = 2
    s.shared_resources["morale"].value = 5
    enc, groups = _solo_groups_with(mgr, "vacuum_cleaner_cleaner", EncounterMode.COUNCIL)
    outs = []
    for g, key in zip(groups, picks, strict=True):
        pid = g.player_ids[0]
        c = next(c for c in g.available_choices if c.encounter_option == key)
        g.submissions[pid] = Submission(player_id=pid, choice_id=c.id)
        outs.append(_outcome(g, pid, c.id, OutcomeTier.PARTIAL_SUCCESS))
    morale0 = s.res("morale")
    rec = encounters.apply_outcomes(s, groups, outs)[0]
    return mgr, enc, rec, outs, morale0


async def test_council_contradictions_collide(settings):
    mgr, enc, rec, outs, morale0 = await _council(settings, ["a", "b", "c"])
    s = mgr.state
    assert rec.distinct_choices == 3 and rec.choice_diversity == 1.0
    assert all(any("contradictory" in c for c in o.consequences) for o in outs)
    assert any(j.subject.startswith("contradictions:") for j in s.memory.running_jokes)
    assert s.res("morale") == morale0 - 1
    reveal = encounters.council_reveal(s, enc, rec)
    assert all(s.players[o.planned.player_ids[0]].display in reveal for o in outs)


async def test_council_unanimity_and_contrarians(settings):
    mgr, _enc, rec, outs, morale0 = await _council(settings, ["d", "d", "d"])
    assert rec.distinct_choices == 1 and any("same answer" in c for c in outs[0].consequences)
    # the Mystic's win (+2 morale) for each responder, plus +1 for the eerie unanimity, capped at the track maximum
    assert mgr.state.res("morale") == min(mgr.state.shared_resources["morale"].max, morale0 + 3 * 2 + 1)
    mgr, _enc, rec, outs, _ = await _council(settings, ["b", "b", "a"])
    loner = outs[2].planned.player_ids[0]
    assert mgr.state.players[loner].stats.contrarian_picks == 1
    assert any("disagreed with everyone else" in c for c in outs[2].consequences)


# ---------------------------------------------------------------------------
# Memory: NPCs come back and remember what you said
# ---------------------------------------------------------------------------


async def test_callbacks_remember_the_actual_answer(settings):
    mgr, enc, rec, outs, _ = await _council(settings, ["b", "b", "a"])
    s = mgr.state
    cb = encounters.build_callback(s, rec, {"loc": "the docks"})
    assert cb.npc_id == enc.npc_id and cb.callback_of == enc.id and cb.source == "callback"
    assert "classified" in cb.intro.lower()  # the majority answer, 'b'
    assert "vacuum attorney" in cb.intro  # and its aftermath
    groups = [DecisionGroup(round=4, kind=DecisionKind.INDIVIDUAL, player_ids=[outs[0].planned.player_ids[0]],
                            shared_context="", available_choices=[])]
    s.turn_number = 4
    encounters.open_encounter(s, cb, groups, EncounterMode.INDIVIDUAL)
    pid = groups[0].player_ids[0]
    c = groups[0].available_choices[0]
    groups[0].submissions[pid] = Submission(player_id=pid, choice_id=c.id)
    encounters.apply_outcomes(s, groups, [_outcome(groups[0], pid, c.id, OutcomeTier.SUCCESS)])
    assert next(r for r in s.encounter_history if r.encounter_id == enc.id).called_back


# ---------------------------------------------------------------------------
# Family-friendly moderation
# ---------------------------------------------------------------------------


def test_moderation_rewrites_or_rejects():
    assert moderate("I put laxatives in his sandwich") == "I put harmless glitter in his sandwich"
    assert "inconvenience" in moderate("I kill the goose")
    assert moderate("Sussex therapist reads Dickens") == "Sussex therapist reads Dickens"
    assert moderate("a very naked dance") is None


async def test_unsuitable_free_text_never_reaches_the_table(settings):
    mgr, players = await start_adventure(settings, n=2)
    p = players[0]
    g = next(g for g in mgr.state.pending_decisions.values() if p.id in g.player_ids)
    with pytest.raises(GameError):
        await mgr.handle(p.id, ev.SubmitDecision(action="submit_decision", group_id=g.group_id, freeform="something sexy"))
    await mgr.handle(p.id, ev.SubmitDecision(action="submit_decision", group_id=g.group_id,
                                             freeform="I poison the bodybuilder's protein shake"))
    sub = mgr.state.pending_decisions[g.group_id].submissions[p.id]
    assert "poison" not in sub.freeform and "mustard" in sub.freeform


# ---------------------------------------------------------------------------
# Generation: structured, validated, moderated, novel — or the library
# ---------------------------------------------------------------------------


def _generated(title="The Lighthouse Keeper's Census", unsafe=False, options=4):
    opts = [
        {"style": style, "label": label, "teaser": "Something happens.", "tags": ["negotiate", "WIN"], "stat": "charm",
         "risk": risk, "advances_objective": i == 0, "aftermath": "has not stopped counting since",
         "success_resource": "reputation", "success_amount": 99, "setback_resource": "food", "setback_amount": 5,
         "npc_reaction": 7, "hidden_consequence": "The census lists the moon as a resident." if i == 1 else ""}
        for i, (style, label, risk) in enumerate([
            ("The Statistician", "Count everyone twice to be safe", "risky"),
            ("The Rebel", "Refuse to be counted on principle", "wild"),
            ("The Helper", "Volunteer to count the seagulls", "risky"),
            ("The Liar", "Claim you are seventeen separate people", "wild"),
        ][:options])
    ]
    return {"title": title, "intro": "A lighthouse keeper is conducting a census of everyone, and everything, on the dock.",
            "question": "\"How many of you are there, and are any of you secretly a lighthouse?\"",
            "npc_name": "Keeper Ollivant", "npc_emoji": "🗼",
            "npc_personality": "takes counting extremely seriously" + (" and is naked" if unsafe else ""), "options": opts}


async def test_generated_encounters_are_validated_and_clamped(settings):
    llm = LLMService(ScriptedProvider(lambda schema, user: _generated() if "ridiculous encounter" in user
                                      else {"nonsense": True}), settings)
    enc = await llm.encounter_generator.generate("absurd_hypothetical", "escalation", {"loc": "the docks"}, "", [], "group_vote")
    assert enc is not None and enc.source == "generated" and enc.key.startswith("gen:")
    assert enc.category == EncounterCategory.HYPOTHETICAL and len(enc.options) == 4
    o = enc.options[0]
    assert o.tags == ["negotiate"] and o.win.resources == {"reputation": 2} and o.lose.resources == {"food": -2}
    assert o.win.npc_disposition == 2
    assert any(x.risk == Risk.SAFE for x in enc.options)  # at least one safe answer is guaranteed
    assert enc.options[1].win.secret


@pytest.mark.parametrize("bad", ["unsafe", "too_few", "repeat"])
async def test_bad_generations_fall_back_to_the_library(settings, bad):
    payload = _generated(unsafe=bad == "unsafe", options=2 if bad == "too_few" else 4)
    llm = LLMService(ScriptedProvider(lambda schema, user: payload), settings)
    recent = ["The Lighthouse Keepers Census"] if bad == "repeat" else []
    assert await llm.encounter_generator.generate("absurd_hypothetical", "escalation", {}, "", recent, "individual") is None


async def test_generated_encounters_run_in_a_live_game_and_prompts_stay_isolated(settings):
    prompts = []

    def responder(schema, user):
        prompts.append(user)
        if "ridiculous encounter" in user:
            return _generated()
        return {"nonsense": True}  # every other service falls back to procedural

    llm = LLMService(ScriptedProvider(responder), settings)
    mgr, _players = await start_adventure(settings, n=3, llm=llm)
    enc = next(mgr.state.encounters[g.encounter_id] for g in mgr.state.pending_decisions.values() if g.encounter_id)
    assert enc.source == "generated" and enc.npc_id in mgr.state.npcs
    enc_prompt = next(p for p in prompts if "ridiculous encounter" in p)
    assert "Wants to be famous" not in enc_prompt  # secret motivations never reach a shared prompt
    assert re.search(r"original", enc_prompt, re.IGNORECASE)
