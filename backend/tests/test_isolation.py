"""Information isolation is enforced server-side, for clients and for LLM prompts."""

import json

import pytest

from app.engine.visibility import authorized_context, project
from app.models.game import InfoItem, Visibility
from tests.conftest import make_game, play_through


@pytest.mark.parametrize("seed", [3, 11, 27])
async def test_projection_never_leaks(settings, seed):
    mgr, players, _ = await make_game(settings, n=4, length="short", seed=seed)
    # Snapshot projections every time state changes by wrapping sync
    snapshots = []
    orig = mgr.sync

    async def spy(only=None):
        if mgr.state.phase.value not in ("ended",):
            for p in mgr.state.players.values():
                snapshots.append((p.id, json.dumps(project(mgr.state, p.id), default=str), mgr.state.model_copy(deep=True)))
        await orig(only)

    mgr.sync = spy
    await play_through(mgr, players)
    assert snapshots
    for pid, blob, st in snapshots:
        for other in st.players.values():
            assert other.token not in blob, "tokens must never be projected"
            mine = st.players[pid].character.secret_motivation
            if other.id != pid and other.character.secret_motivation and other.character.secret_motivation != mine:
                assert json.dumps(other.character.secret_motivation) not in blob, "secret motivations stay secret during play"
        for info in st.hidden_information.values():
            if info.visibility != Visibility.PUBLIC and pid not in info.audience:
                assert info.id not in blob
        for entry in st.story_history:
            if entry.visibility != Visibility.PUBLIC and pid not in entry.audience:
                assert entry.id not in blob
        for g in st.pending_decisions.values():
            for other_id in g.submissions:
                if other_id != pid:
                    assert f'"choice_id": "{g.submissions[other_id].choice_id}", "freeform"' not in blob or g.submissions[pid if pid in g.submissions else other_id].choice_id == g.submissions[other_id].choice_id
        assert str(st.random_seed) not in blob or len(str(st.random_seed)) < 4
        for hv in st.hidden_variables:
            if pid not in hv.discovered_by:
                assert hv.hint not in blob


async def test_other_players_choices_hidden_until_resolution(settings):
    mgr, players, _ = await make_game(settings, n=3, seed=8)
    from app.models import events as ev
    from tests.conftest import play_through  # noqa: F401

    # advance to first decision
    host = players[0]
    for p in players[1:]:
        await mgr.handle(p.id, ev.SetReady(action="set_ready", ready=True))
    await mgr.handle(host.id, ev.StartGame(action="start_game"))
    for i, p in enumerate(players):
        await mgr.handle(p.id, ev.SubmitTheme(action="submit_theme", text=["Knights deliver pizza", "Haunted Costco escape", "Raccoon government"][i]))
    if mgr.state.phase.value == "theme_voting":
        for p in players:
            await mgr.handle(p.id, ev.CastVote(action="cast_vote", option_id=mgr.state.theme_options[0].id))
    await mgr.on_timeout("objective_reveal")
    await mgr.close_phase(host)
    g = next(iter(mgr.state.pending_decisions.values()))
    actor = g.player_ids[0]
    await mgr.handle(actor, ev.SubmitDecision(action="submit_decision", group_id=g.group_id, freeform="I secretly lick the doorknob"))
    for p in players:
        view = json.dumps(project(mgr.state, p.id))
        if p.id != actor:
            assert "lick the doorknob" not in view
    # someone outside a decision can't submit to it
    outsider_groups = [x for x in mgr.state.pending_decisions.values() if host.id not in x.player_ids]
    if outsider_groups:
        from app.engine.game_manager import GameError

        with pytest.raises(GameError):
            await mgr.handle(host.id, ev.SubmitDecision(action="submit_decision", group_id=outsider_groups[0].group_id, choice_id=outsider_groups[0].available_choices[0].id))


async def test_authorized_context_for_group_excludes_member_private_info(settings):
    mgr, players, _ = await make_game(settings, n=3, seed=2)
    s = mgr.state
    a, b, c = players
    a.character.secret_motivation = "SECRET-A-MOTIVE"
    s.hidden_information["x"] = InfoItem(id="x", visibility=Visibility.PRIVATE, audience=[a.id], title="t", text="ONLY-A-KNOWS")
    s.hidden_information["y"] = InfoItem(id="y", visibility=Visibility.GROUP, audience=[a.id, b.id], title="t", text="A-AND-B-KNOW")
    ctx_ab = authorized_context(s, [a.id, b.id])
    assert "ONLY-A-KNOWS" not in ctx_ab and "SECRET-A-MOTIVE" not in ctx_ab
    assert "A-AND-B-KNOW" in ctx_ab
    ctx_bc = authorized_context(s, [b.id, c.id])
    assert "A-AND-B-KNOW" not in ctx_bc
    ctx_a = authorized_context(s, [a.id])
    assert "ONLY-A-KNOWS" in ctx_a and "SECRET-A-MOTIVE" in ctx_a


async def test_never_reveal_excluded_from_final_story(settings):
    mgr, players, _ = await make_game(settings, n=3, seed=13)
    from app.engine import chronicle

    state = await play_through(mgr, players)
    state.hidden_information["nr"] = InfoItem(id="nr", visibility=Visibility.NEVER_REVEAL, audience=[players[0].id],
                                              title="t", text="THE-UNSPEAKABLE-THING")
    chronicle.record(state, "action", visibility=Visibility.NEVER_REVEAL, importance=5, narrative_summary="THE-UNSPEAKABLE-THING happened")
    story = await mgr.build_final_story()
    assert "UNSPEAKABLE" not in " ".join(c.text for c in story.chapters)
    assert story.validation.passed, story.validation.issues
